"""Referrals (#97): give a friend a week of Pro, get a week of Pro.

Apple does the entitling. Both weeks are Apple *one-time offer codes*, created
in App Store Connect and loaded into two pools here (`tools/load_referral_codes.py`).
This module only decides who gets which code; it never grants Pro itself.

Why attribution is by claim, not by code
----------------------------------------
The obvious design is "hand each referrer a code, reward them when it is
redeemed". It cannot work: when an offer code is redeemed, the signed
transaction carries `offerIdentifier` = the offer's *reference name*, the same
for every code in the batch (Apple: "If the offer type is code, this value
contains the reference name of the offer code"). The server can see that a
referral code was redeemed, never which one. And one offer per referrer is
out too: App Store Connect allows ten active offers per subscription.

So the friend tells us who referred them:

1. The referrer opens Invite a friend and gets a short referral code.
2. The friend enters it in the app. `claim` records "this device was referred
   by that one" and hands out one friend code from the pool.
3. The friend redeems it with Apple. Their next entitlement sync arrives with
   `offerType` 3 and the friend offer's reference name; `on_entitlement` then
   takes a reward code from the other pool and parks it for the referrer, whose
   app shows it on the next status check.

Identity is the Keychain `device_id`, which survives a reinstall where the App
Attest subject does not; a referrer who reinstalls keeps their code and their
rewards. It is client-chosen, so it proves nothing on its own: every route also
requires an attested principal, a claim is once per device *and* once per
subject, and the reward is capped per referrer per year.

Off unless `REFERRALS_ENABLED` is set, and inert until the friend offer's
reference name is configured. Nothing here can fail an entitlement sync:
`on_entitlement` never raises.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import Principal, deps, require_auth
from entitlements import Entitlement

log = logging.getLogger("snapworth.referral")

# Apple's offerType for an offer-code redemption.
OFFER_CODE = 3

# Referral codes: short, typed by hand, read aloud. No 0/O, 1/I/L, so a code
# said over a table in a thrift shop survives the trip. 31^6 ≈ 887M.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6

# A year and a bit: a referral relationship outlives the yearly cap window.
RECORD_TTL = 60 * 60 * 24 * 400
# Failed claims per subject per day, before claims are refused. Enumerating
# 887M codes at ten a day is not a plan.
MAX_FAILED_CLAIMS_PER_DAY = 10

POOL_FRIEND = "friend"
POOL_REWARD = "reward"
POOLS = (POOL_FRIEND, POOL_REWARD)


def _bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ReferralConfig:
    enabled: bool = False
    # Reference name of the App Store Connect offer the friend codes belong to.
    # A redemption is recognised by this, since it is all Apple reports.
    friend_offer: str = ""
    rewards_per_year: int = 5
    app_apple_id: str = "6788521307"
    share_base: str = "https://www.snapworth.eu/i/"

    @property
    def active(self) -> bool:
        return self.enabled and bool(self.friend_offer)

    def redeem_url(self, code: str) -> str:
        return f"https://apps.apple.com/redeem?ctx=offercodes&id={self.app_apple_id}&code={code}"


def config_from_env() -> ReferralConfig:
    try:
        cap = int(os.environ.get("REFERRAL_REWARDS_PER_YEAR", "5"))
    except ValueError:
        cap = 5
    return ReferralConfig(
        enabled=_bool("REFERRALS_ENABLED"),
        friend_offer=os.environ.get("REFERRAL_FRIEND_OFFER", "").strip(),
        rewards_per_year=max(0, cap),
        app_apple_id=os.environ.get("APPLE_APP_APPLE_ID", "").strip() or "6788521307",
    )


config = config_from_env()


# ── Keys ────────────────────────────────────────────────────────────────────

def _code_key(code: str) -> str:            # referral code → referrer device
    return f"ref:code:{code}"


def _mine_key(device: str) -> str:          # referrer device → their code
    return f"ref:mine:{device}"


def _claim_key(device: str) -> str:         # friend device → claim record
    return f"ref:claim:{device}"


def _claim_subject_key(subject: str) -> str:
    return f"ref:claimsubj:{subject}"


def _rewards_key(device: str) -> str:       # referrer device → earned codes
    return f"ref:rewards:{device}"


def _rewarded_key(device: str) -> str:      # friend device → reward dedupe
    return f"ref:rewarded:{device}"


def _count_key(device: str, year: int) -> str:
    return f"ref:count:{device}:{year}"


def _fail_key(subject: str) -> str:
    return f"ref:fail:{subject}:{time.strftime('%Y%m%d', time.gmtime())}"


def pool_size_key(pool: str) -> str:
    return f"refpool:{pool}:size"


def pool_cursor_key(pool: str) -> str:
    return f"refpool:{pool}:next"


def pool_item_key(pool: str, index: int) -> str:
    return f"refpool:{pool}:{index}"


# ── Pools ───────────────────────────────────────────────────────────────────
#
# An indexed array claimed with an atomic INCR cursor, because the cache has
# no list pop: `incr(next)` hands out each index exactly once however many
# requests race, and an index past `size` means the pool is empty.

async def take_code(pool: str) -> str | None:
    cache = deps.cache
    size = int(await cache.get(pool_size_key(pool)) or 0)
    if size <= 0:
        return None
    index = await cache.incr(pool_cursor_key(pool))
    if index > size:
        return None
    return await cache.get(pool_item_key(pool, index))


async def pool_remaining(pool: str) -> int:
    cache = deps.cache
    size = int(await cache.get(pool_size_key(pool)) or 0)
    used = int(await cache.get(pool_cursor_key(pool)) or 0)
    return max(0, size - used)


# ── Core ────────────────────────────────────────────────────────────────────

class ReferralError(Exception):
    """A claim that cannot be honoured; the message is shown to the user."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def normalise_code(raw: str) -> str:
    return "".join(ch for ch in raw.upper() if ch.isalnum())


async def code_for(device: str) -> str:
    """The referrer's code, created on first request and kept thereafter."""
    cache = deps.cache
    existing = await cache.get(_mine_key(device))
    if existing:
        return existing
    for _ in range(8):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if await cache.add(_code_key(code), device, ttl=RECORD_TTL):
            # Two first requests racing could each mint a code; the second
            # write wins `mine`, and the loser's code still resolves to this
            # device, so either one shared works.
            await cache.set(_mine_key(device), code, ttl=RECORD_TTL)
            return code
    raise ReferralError(503, "Couldn't create an invite right now. Try again later.")


async def rewards_for(device: str) -> list[dict]:
    raw = await deps.cache.get(_rewards_key(device))
    return json.loads(raw) if raw else []


async def claim(subject: str, device: str, raw_code: str) -> str:
    """Record the referral and return the friend's Apple offer code."""
    cache = deps.cache
    if int(await cache.get(_fail_key(subject)) or 0) >= MAX_FAILED_CLAIMS_PER_DAY:
        raise ReferralError(429, "Too many tries today. Try again tomorrow.")

    code = normalise_code(raw_code)
    referrer = await cache.get(_code_key(code)) if len(code) == CODE_LENGTH else None
    if referrer is None:
        await cache.incr(_fail_key(subject), ttl=60 * 60 * 24)
        raise ReferralError(404, "That code doesn't match an invite. Check it and try again.")
    if referrer == device:
        raise ReferralError(400, "That's your own invite code. Share it with a friend instead.")

    record = {"code": code, "referrer": referrer, "subject": subject, "claimed_at": int(time.time())}
    # Once per device and once per subject: a reinstall is a new subject, a
    # spoofed device_id is a new device, and neither alone gets a second week.
    if not await cache.add(_claim_key(device), json.dumps(record), ttl=RECORD_TTL):
        raise ReferralError(409, "This phone has already used an invite.")
    if not await cache.add(_claim_subject_key(subject), device, ttl=RECORD_TTL):
        await cache.delete(_claim_key(device))
        raise ReferralError(409, "This phone has already used an invite.")

    friend_code = await take_code(POOL_FRIEND)
    if friend_code is None:
        # Undo, so the friend can try again once the pool is topped up.
        await cache.delete(_claim_key(device))
        await cache.delete(_claim_subject_key(subject))
        log.error("referral friend pool is empty")
        raise ReferralError(503, "Invites are paused for a moment. Try again later.")
    record["friend_code"] = friend_code
    await cache.set(_claim_key(device), json.dumps(record), ttl=RECORD_TTL)
    return friend_code


async def on_entitlement(subject: str, device: str | None, ent: Entitlement) -> bool:
    """Reward the referrer when a referred friend redeems the friend offer.

    Called from `/auth/entitlement`. Returns whether a reward was issued, for
    tests; never raises, so a referral problem cannot fail an entitlement sync.
    """
    try:
        if not config.active or not device:
            return False
        if ent.offer_type != OFFER_CODE or ent.offer_identifier != config.friend_offer:
            return False
        cache = deps.cache
        raw = await cache.get(_claim_key(device))
        if not raw:
            return False                 # redeemed a friend code without a claim
        claimed = json.loads(raw)
        referrer = claimed["referrer"]
        if not await cache.add(_rewarded_key(device), referrer, ttl=RECORD_TTL):
            return False                 # this friend already paid out
        year = time.gmtime().tm_year
        if await cache.incr(_count_key(referrer, year), ttl=RECORD_TTL) > config.rewards_per_year:
            log.info("referral reward capped", extra={"year": year})
            return False
        reward = await take_code(POOL_REWARD)
        if reward is None:
            log.error("referral reward pool is empty; reward owed but not issued")
            # Let a later sync retry once the pool is refilled.
            await cache.delete(_rewarded_key(device))
            await cache.incr(_count_key(referrer, year), ttl=RECORD_TTL, amount=-1)
            return False
        rewards = await rewards_for(referrer)
        rewards.append({"code": reward, "earned_at": int(time.time())})
        await cache.set(_rewards_key(referrer), json.dumps(rewards), ttl=RECORD_TTL)
        return True
    except Exception:  # noqa: BLE001 — must never fail the sync
        log.exception("referral reward failed")
        return False


# ── Routes ──────────────────────────────────────────────────────────────────

DEVICE_ID = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")


class StatusRequest(BaseModel):
    device_id: str = DEVICE_ID


class RewardOut(BaseModel):
    code: str
    redeem_url: str
    earned_at: int


class StatusResponse(BaseModel):
    enabled: bool
    code: str | None = None
    share_url: str | None = None
    rewards: list[RewardOut] = []
    rewards_left_this_year: int | None = None


class ClaimRequest(BaseModel):
    device_id: str = DEVICE_ID
    code: str = Field(min_length=1, max_length=32)


class ClaimResponse(BaseModel):
    friend_code: str
    redeem_url: str


router = APIRouter(prefix="/referral", tags=["referral"])


@router.post("/status", response_model=StatusResponse)
async def status(req: StatusRequest, principal: Principal = Depends(require_auth)) -> StatusResponse:
    """The caller's invite code and any weeks they have earned.

    `enabled: false` tells the app to hide the feature, so the flag is the one
    switch for both sides.
    """
    if not config.active:
        return StatusResponse(enabled=False)
    try:
        code = await code_for(req.device_id)
    except ReferralError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    rewards = await rewards_for(req.device_id)
    earned = int(await deps.cache.get(_count_key(req.device_id, time.gmtime().tm_year)) or 0)
    return StatusResponse(
        enabled=True, code=code, share_url=config.share_base + code,
        rewards=[RewardOut(code=r["code"], redeem_url=config.redeem_url(r["code"]),
                           earned_at=r["earned_at"]) for r in rewards],
        rewards_left_this_year=max(0, config.rewards_per_year - earned))


@router.post("/claim", response_model=ClaimResponse)
async def claim_route(req: ClaimRequest, principal: Principal = Depends(require_auth)) -> ClaimResponse:
    if not config.active:
        raise HTTPException(status_code=404, detail="Invites aren't available right now.")
    try:
        code = await claim(principal.subject, req.device_id, req.code)
    except ReferralError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    return ClaimResponse(friend_code=code, redeem_url=config.redeem_url(code))
