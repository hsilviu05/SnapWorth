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

Identity is the Keychain `device_id`. It is client-chosen, so it proves nothing
on its own, and until this was hardened any string of the right shape was
accepted from anyone: one attested install could mint a referral code for as
many invented devices as it liked. So both routes now refuse a caller without
an App Attest token, and tie the device to the caller (`_bind`): the first
attested subject to present a device owns it, and a subject speaks for only the
first device it presented. A claim is once per device and once per subject; a
reward is once per friend's device and once per Apple transaction, and capped
per referrer per year — which, with the binding, is also per subject.

What the binding costs: an App Attest key is per install, so a reinstall is a
new subject presenting a device the old one owns, and it is refused for as long
as the binding lives. A referrer who reinstalls loses sight of their code and
of any weeks parked for it. RUNBOOK §18 records that as a decision to confirm
before `REFERRALS_ENABLED` goes on.

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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

import notify
import ratelimit
from auth import Principal, deps, require_auth
from cache import CacheUnavailable, ResilientCache
from entitlements import Entitlement, is_bounded

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

# The operator is told when a pool gets down to this many codes, and again
# when it is empty (`notify.referral_pool_low`). A new batch has to be
# generated in App Store Connect and loaded by hand, so the warning has to
# come while there is still a day or two of claims left, not at zero — when
# every invite already answers "Invites are paused".
POOL_LOW_AT = max(0, int(os.environ.get("REFERRAL_POOL_LOW_AT", "20") or 0))

# Per-subject and per-IP limit for both routes, injected by `main._lifespan`
# because the limiter lives in `main`, which imports this module. The router
# was mounted with nothing in front of it, unlike every other authenticated
# route. None only before startup and in tests that do not care.
limiter: Callable[[str, str | None], Awaitable[None]] | None = None


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


def _rewarded_txn_key(otid: str) -> str:    # friend's Apple purchase → reward dedupe
    return f"ref:rewardedtxn:{otid}"


def _redeemed_key(otid: str) -> str:        # a friend-offer purchase, seen once
    return f"ref:redeemed:{otid}"


def _paid_key(otid: str) -> str:            # its first paid period, seen once
    return f"ref:paid:{otid}"


def _owner_key(device: str) -> str:         # device → the subject that owns it
    return f"ref:owner:{device}"


def _subject_device_key(subject: str) -> str:   # subject → the device it speaks for
    return f"ref:subjdev:{subject}"


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
    """The next unissued code in `pool`, or None when it is empty.

    Every read and the increment are `required`: the cursor is the only record
    of which codes have been given away. Without that, an INCR that failed
    during a Redis blip was answered from process memory, counting from 1, and
    if Redis was back for the item read the friend was handed a code someone
    already had — which Apple refuses, and a device can claim only once. A
    failed size read was answered 0 from memory the same way: an empty pool,
    and now an alert, for what was a Redis outage. Raises `CacheUnavailable`;
    each caller undoes what it had recorded.

    The cursor is read before it is moved. Moving it on an empty pool carried
    it past `size` on every refused claim, and the loader appended the next
    batch at `size + 1`, so each refusal while the pool was dry skipped one
    code of the next batch. Two requests racing for the last code can still
    carry it one past, which is why the loader now appends after whichever of
    the two is further on.
    """
    cache = deps.cache
    size = int(await cache.get(pool_size_key(pool), required=True) or 0)
    used = int(await cache.get(pool_cursor_key(pool), required=True) or 0)
    if used >= size:
        _warn_if_low(pool, 0)
        return None
    index = await cache.incr(pool_cursor_key(pool), required=True)
    if index > size:
        _warn_if_low(pool, 0)            # raced another request to the last code
        return None
    code = await cache.get(pool_item_key(pool, index), required=True)
    # The slot, never the code: the only record of what was handed out that
    # outlives Redis. After a loss it says how far into each batch the service
    # had got, which is the evidence for treating the batch as burned (§9).
    log.info("referral code issued", extra={"pool": pool, "slot": index, "size": size})
    if code is None:
        log.error("referral pool slot is empty", extra={"pool": pool, "slot": index})
    _warn_if_low(pool, size - index)
    return code


def _warn_if_low(pool: str, remaining: int) -> None:
    if remaining <= POOL_LOW_AT:
        notify.referral_pool_low(pool, remaining)


async def pool_level(pool: str, cache: ResilientCache | None = None) -> tuple[int, int]:
    """(codes loaded, codes left) for `pool`.

    `cache` so the ops bot can read through its own handle; the two are the
    same object in production.
    """
    cache = cache or deps.cache
    size = int(await cache.get(pool_size_key(pool)) or 0)
    used = int(await cache.get(pool_cursor_key(pool)) or 0)
    return size, max(0, size - used)


async def pool_remaining(pool: str) -> int:
    return (await pool_level(pool))[1]


# ── Core ────────────────────────────────────────────────────────────────────

# A dry pool and a Redis that cannot answer look the same to the user, and the
# app words both from the 503 alone (ReferralClaimError.paused).
_UNAVAILABLE = "Invites are paused for a moment. Try again later."


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


async def _bind(subject: str, device: str) -> None:
    """Tie `device` to `subject` for referrals, or refuse with 403.

    The first attested subject to present a device owns it, and a subject
    speaks for only the first device it presented. Without this, `device_id`
    was whatever the caller sent: one install could mint a code for any number
    of invented devices, and the yearly reward cap, keyed on the device, never
    capped anyone.

    `required`, because this is the check and a check answered from one
    replica's memory is not one. Could-not-ask raises `CacheUnavailable`,
    which the routes answer 503.
    """
    cache = deps.cache
    owner_key, device_key = _owner_key(device), _subject_device_key(subject)
    took_device = await cache.add(owner_key, subject, ttl=RECORD_TTL, required=True)
    if not took_device and await cache.get(owner_key, required=True) != subject:
        raise ReferralError(403, "This phone's invites belong to another install of the app.")
    if not await cache.add(device_key, device, ttl=RECORD_TTL, required=True):
        if await cache.get(device_key, required=True) != device:
            if took_device:
                # Hand the device back: it was taken by a caller that turned
                # out to be bound elsewhere, and must not stay burned.
                await cache.delete(owner_key, required=True)
            raise ReferralError(403, "This install already has an invite on another device.")


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

    try:
        friend_code = await take_code(POOL_FRIEND)
        unavailable = False
    except CacheUnavailable:
        friend_code, unavailable = None, True
    if friend_code is None:
        # Undo, so the friend can try again once the pool is topped up or
        # Redis answers again.
        await cache.delete(_claim_key(device))
        await cache.delete(_claim_subject_key(subject))
        if not unavailable:
            log.error("referral friend pool is empty")
        raise ReferralError(503, _UNAVAILABLE)
    record["friend_code"] = friend_code
    await cache.set(_claim_key(device), json.dumps(record), ttl=RECORD_TTL)
    notify.count_referral("claimed")
    return friend_code


async def on_entitlement(subject: str, device: str | None, ent: Entitlement) -> bool:
    """Reward the referrer when a referred friend redeems the friend offer.

    Called from `/auth/entitlement`. Returns whether a reward was issued, for
    tests; never raises, so a referral problem cannot fail an entitlement sync.
    """
    try:
        if not config.active:
            return False
        if is_bounded(ent):
            # A Sandbox redemption — a tester or App Review — is not a friend
            # who subscribed, and every reward is a real Apple offer code.
            return False
        otid = ent.original_transaction_id
        if not otid:
            return False
        if ent.offer_type != OFFER_CODE or ent.offer_identifier != config.friend_offer:
            # The same subscription later, paid for: a friend who stayed.
            await note_paid_period(ent)
            return False
        cache = deps.cache
        # Counted whether or not it earns anyone a week: this is the one
        # place the server sees a friend week actually taken at Apple.
        if await cache.add(_redeemed_key(otid), "1", ttl=RECORD_TTL):
            notify.count_referral("redeemed")
        if not device:
            return False
        raw = await cache.get(_claim_key(device))
        if not raw:
            return False                 # redeemed a friend code without a claim
        claimed = json.loads(raw)
        referrer = claimed["referrer"]
        return await _reward(referrer, device, otid)
    except Exception:  # noqa: BLE001 — must never fail the sync
        log.exception("referral reward failed")
        return False


async def _reward(referrer: str, device: str, otid: str) -> bool:
    """Park one reward code for `referrer`, once per friend and per purchase.

    Once per friend device *and* once per Apple transaction. The device alone
    was the only dedupe, and `device_id` is whatever the syncing client sends:
    one real redemption posted from several claimed devices paid out once per
    device. The transaction is Apple's, signed, and a free week redeemed once
    has exactly one `originalTransactionId`, which its renewals keep.

    `required` throughout, because each step decides whether a real Apple code
    leaves the pool, and a marker written to one replica's memory is not a
    marker. Anything that fails after the markers are set hands them back, so
    a later sync retries.
    """
    cache = deps.cache
    if not await cache.add(_rewarded_key(device), referrer, ttl=RECORD_TTL, required=True):
        return False                     # this friend already paid out
    if not await cache.add(_rewarded_txn_key(otid), "1", ttl=RECORD_TTL, required=True):
        # This purchase already paid out through another claimed device.
        await cache.delete(_rewarded_key(device), required=True)
        log.warning("referral reward refused: transaction already rewarded")
        return False
    year = time.gmtime().tm_year
    count_key = _count_key(referrer, year)

    async def hand_back() -> None:
        await cache.delete(_rewarded_key(device), required=True)
        await cache.delete(_rewarded_txn_key(otid), required=True)
        await cache.incr(count_key, ttl=RECORD_TTL, amount=-1, required=True)

    try:
        if await cache.incr(count_key, ttl=RECORD_TTL, required=True) > config.rewards_per_year:
            log.info("referral reward capped", extra={"year": year})
            return False
        reward = await take_code(POOL_REWARD)
        if reward is None:
            log.error("referral reward pool is empty; reward owed but not issued")
            # Let a later sync retry once the pool is refilled.
            await hand_back()
            return False
        rewards = await rewards_for(referrer)
        rewards.append({"code": reward, "earned_at": int(time.time())})
        await cache.set(_rewards_key(referrer), json.dumps(rewards), ttl=RECORD_TTL,
                        required=True)
    except CacheUnavailable:
        log.error("referral reward not issued: cache unavailable; a later sync retries")
        await hand_back()
        return False
    notify.count_referral("rewarded")
    return True


async def note_paid_period(ent: Entitlement) -> None:
    """Count a referred friend's first paid period, once. Never raises.

    The number the other three counters cannot give: whether a friend who took
    the free week went on to pay. A paid period is one with no `offerType` —
    the rule `appstorenotify.Notification.is_paid_period` applies — on a
    subscription that began as a referral week. Called from the entitlement
    sync and from Apple's renewal notifications, so a friend who never opens
    the app again is counted too; whichever arrives first counts it.
    """
    try:
        otid = ent.original_transaction_id
        if (not otid or ent.offer_type is not None or ent.revoked_at is not None
                or is_bounded(ent)):
            return
        cache = deps.cache
        if not await cache.get(_redeemed_key(otid)):
            return
        if await cache.add(_paid_key(otid), "1", ttl=RECORD_TTL):
            notify.count_referral("paid")
    except Exception:  # noqa: BLE001 — a counter must never fail its caller
        log.exception("referral paid-period counter failed")


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


async def _admit(principal: Principal, device: str, request: Request) -> None:
    """What both routes check before touching anything, in this order.

    Attested first: the legacy principal's subject is `legacy:` plus a header
    the caller picks, so it can bind nothing and limit nothing. The installed
    client always sends a token here (`requireBearerToken`), so this refuses
    only callers that are not the app. Then the limiter, then the binding —
    which writes, so it comes after the two that are cheap to refuse.
    """
    if not principal.authenticated:
        raise HTTPException(status_code=401, detail="Authentication required.",
                            headers={"WWW-Authenticate": "Bearer"})
    if limiter is not None:
        await limiter(principal.subject, ratelimit.client_ip(request))
    await _bind(principal.subject, device)


@router.post("/status", response_model=StatusResponse)
async def status(req: StatusRequest, request: Request,
                 principal: Principal = Depends(require_auth)) -> StatusResponse:
    """The caller's invite code and any weeks they have earned.

    `enabled: false` tells the app to hide the feature, so the flag is the one
    switch for both sides. Answered before any check: it is the same for every
    caller and writes nothing, and the app asks on every return to the
    foreground.
    """
    if not config.active:
        return StatusResponse(enabled=False)
    try:
        await _admit(principal, req.device_id, request)
        code = await code_for(req.device_id)
    except ReferralError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    except CacheUnavailable:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE) from None
    rewards = await rewards_for(req.device_id)
    earned = int(await deps.cache.get(_count_key(req.device_id, time.gmtime().tm_year)) or 0)
    return StatusResponse(
        enabled=True, code=code, share_url=config.share_base + code,
        rewards=[RewardOut(code=r["code"], redeem_url=config.redeem_url(r["code"]),
                           earned_at=r["earned_at"]) for r in rewards],
        rewards_left_this_year=max(0, config.rewards_per_year - earned))


@router.post("/claim", response_model=ClaimResponse)
async def claim_route(req: ClaimRequest, request: Request,
                      principal: Principal = Depends(require_auth)) -> ClaimResponse:
    if not config.active:
        raise HTTPException(status_code=404, detail="Invites aren't available right now.")
    try:
        await _admit(principal, req.device_id, request)
        code = await claim(principal.subject, req.device_id, req.code)
    except ReferralError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    except CacheUnavailable:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE) from None
    return ClaimResponse(friend_code=code, redeem_url=config.redeem_url(code))
