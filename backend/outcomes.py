"""Opt-in sale outcomes: what a find actually sold for, beside its estimate (#224).

Real sold prices already exist on users' phones (My Flips) and never left them.
A user who turns on "Share sale prices to improve estimates" sends, per sold
flip, the scan-time estimate and the sale: enough to measure how far the
estimates are from what things sell for, by prompt version, storefront,
category and confidence band (`eval.cli field`).

What a record is, and is not
----------------------------
`Outcome` is the whole of it, and it forbids any other field: the request is
refused rather than a stray key stored. **Never sent, so never stored:** the
photo, the item's name, notes, the price paid, or any device identifier. The
brand is a yes/no (was one identified), not its text.

It is not gold data. A typed price with no receipt is `medium` label
confidence at best (`eval/schema.py`), so field outcomes are their own
measurement, tagged user-reported, and never an input to the CI gate.

Identity and deletion
---------------------
The app makes a random **contribution id** per flip (an edit re-sends and
replaces the same record) and a random **contributor token** per install,
used only to delete. The server keeps a keyed hash of the token
(`auditlog.keyed_tag`), never the token. Un-marking a sale deletes its
record; "Delete my shared sales" deletes every record the token made. A
record can be replaced or deleted only with the token that made it.

Storage
-------
The cache offers get, set, incr and delete, so the indexes are counters, not
read-modify-write lists: a global sequence for the export
(`outcome-seq`, `outcome-slot:<n>`) and one per contributor for deletion
(`outcome-by:<tag>:seq`, `outcome-by:<tag>:<n>`). A deleted record leaves its
slot pointing at nothing, which the export and the deletion skip. Records and
indexes keep for `RECORD_TTL`.

Durable records need Redis persistence (AOF), which is RUNBOOK §11's and the
Railway config issue's; until it is on, a Redis restart loses what arrived
since the last snapshot.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

import auditlog
import ratelimit
from auth import Principal, deps, require_auth
from cache import CacheUnavailable

#: How long a shared outcome is kept: the same horizon as the other long-lived
#: records (RUNBOOK §11's table). The policy states it.
RECORD_TTL = 400 * 86400

#: Records one install may send or replace in a UTC day. A seller marks a few
#: sales a day; this bounds a misbehaving build or a poisoning attempt.
DAILY_CAP = 50

#: Sale currencies accepted. Explicitly chosen in the app, never assumed:
#: the markets SnapWorth's marketplaces serve.
CURRENCIES = frozenset({"USD", "EUR", "GBP", "RON", "PLN", "CHF", "CAD", "AUD",
                        "CNY", "MXN", "SEK", "DKK", "NOK", "CZK", "HUF", "BGN"})

#: `Marketplace` raw values in the app.
MARKETPLACES = frozenset({"ebay", "poshmark", "mercari", "depop", "vinted", "facebook",
                          "olx", "xianyu", "kleinanzeigen"})

#: The ceiling on any price field. A thrift flip above this is not one this
#: product prices, and a typo of a few zeros should not skew a median.
MAX_PRICE = 100_000.0

limiter: Callable[[str, str | None], Awaitable[None]] | None = None


class Outcome(BaseModel):
    """One sold flip, exactly the documented fields. `extra="forbid"`: any
    other key — an item name, a photo, a note — refuses the whole request."""

    model_config = ConfigDict(extra="forbid")

    contribution_id: str = Field(pattern=r"^[0-9A-Fa-f-]{32,36}$")
    contributor_token: str = Field(min_length=32, max_length=64,
                                   pattern=r"^[A-Za-z0-9_-]+$")
    build: str = Field(min_length=1, max_length=16, pattern=r"^[0-9.]+$")
    storefront: str | None = Field(default=None, pattern=r"^[A-Z]{2,3}$")
    scan_day: date
    prompt_version: str = Field(min_length=1, max_length=16)
    valuation_source: Literal["model", "comps"] = "model"
    category: str = Field(min_length=1, max_length=40)
    brand_identified: bool
    condition_grade: str | None = Field(default=None, max_length=20)
    condition_chosen: str | None = Field(default=None, max_length=20)
    confidence_score: int | None = Field(default=None, ge=0, le=100)
    confidence_band: str = Field(min_length=1, max_length=10)
    estimate_low: float = Field(ge=0, le=MAX_PRICE)
    estimate_high: float = Field(ge=0, le=MAX_PRICE)
    likely: float | None = Field(default=None, ge=0, le=MAX_PRICE)
    expected: float | None = Field(default=None, ge=0, le=MAX_PRICE)
    sold_price: float = Field(gt=0, le=MAX_PRICE)
    currency: str
    sold_day: date
    days_listed_to_sold: int | None = Field(default=None, ge=0, le=3650)
    marketplace: str | None = None

    @field_validator("currency")
    @classmethod
    def _known_currency(cls, value: str) -> str:
        if value not in CURRENCIES:
            raise ValueError(f"currency must be one of {sorted(CURRENCIES)}")
        return value

    @field_validator("marketplace")
    @classmethod
    def _known_marketplace(cls, value: str | None) -> str | None:
        if value is not None and value not in MARKETPLACES:
            raise ValueError("unknown marketplace")
        return value

    @field_validator("sold_day", "scan_day")
    @classmethod
    def _not_in_the_future(cls, value: date) -> date:
        # A day of slack: the device's calendar day can be ahead of UTC.
        today = datetime.now(timezone.utc).date()
        if value.toordinal() > today.toordinal() + 1:
            raise ValueError("date is in the future")
        if value.year < 2024:
            raise ValueError("date is before SnapWorth existed")
        return value


class Deletion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contributor_token: str = Field(min_length=32, max_length=64,
                                   pattern=r"^[A-Za-z0-9_-]+$")
    #: One record (un-marking a sale), or every record the token made when
    #: absent ("Delete my shared sales").
    contribution_id: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f-]{32,36}$")


def contributor_tag(token: str) -> str:
    """The stored stand-in for a contributor token, keyed with the audit salt."""
    return auditlog.keyed_tag("outcomes", token, 24)


def _record_key(contribution_id: str) -> str:
    return f"outcome:{contribution_id.lower()}"


def _slot_key(n: int) -> str:
    return f"outcome-slot:{n}"


def _by_seq_key(tag: str) -> str:
    return f"outcome-by:{tag}:seq"


def _by_key(tag: str, n: int) -> str:
    return f"outcome-by:{tag}:{n}"


def _cap_key(tag: str) -> str:
    return f"outcome-cap:{tag}:{time.strftime('%Y%m%d', time.gmtime())}"


class OutcomeError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


async def store(outcome: Outcome) -> bool:
    """Insert or replace a record. True when it was new."""
    cache = deps.cache
    tag = contributor_tag(outcome.contributor_token)
    if await cache.incr(_cap_key(tag), ttl=2 * 86400, required=True) > DAILY_CAP:
        raise OutcomeError(429, "Too many shared sales today. Try again tomorrow.")
    key = _record_key(outcome.contribution_id)
    record = outcome.model_dump(mode="json", exclude={"contributor_token"})
    record["contributor"] = tag
    record["received"] = datetime.now(timezone.utc).date().isoformat()
    body = json.dumps(record, sort_keys=True)
    # Set-if-absent, so two first sends of one sale cannot both index it.
    if not await cache.add(key, body, ttl=RECORD_TTL, required=True):
        existing = await cache.get(key, required=True)
        if existing and json.loads(existing).get("contributor") != tag:
            raise OutcomeError(403, "That sale was shared from another install.")
        await cache.set(key, body, ttl=RECORD_TTL, required=True)
        return False
    n = await cache.incr("outcome-seq", required=True)
    await cache.set(_slot_key(n), outcome.contribution_id.lower(), ttl=RECORD_TTL, required=True)
    m = await cache.incr(_by_seq_key(tag), ttl=RECORD_TTL, required=True)
    await cache.set(_by_key(tag, m), outcome.contribution_id.lower(), ttl=RECORD_TTL,
                    required=True)
    return True


async def delete(req: Deletion) -> int:
    """Delete one record or every record the token made; how many went."""
    cache = deps.cache
    tag = contributor_tag(req.contributor_token)
    if req.contribution_id:
        ids = [req.contribution_id.lower()]
    else:
        count = int(await cache.get(_by_seq_key(tag), required=True) or 0)
        ids = [i for n in range(1, count + 1)
               if (i := await cache.get(_by_key(tag, n), required=True))]
    removed = 0
    for contribution_id in ids:
        raw = await cache.get(_record_key(contribution_id), required=True)
        if not raw:
            continue
        if json.loads(raw).get("contributor") != tag:
            if req.contribution_id:
                raise OutcomeError(403, "That sale was shared from another install.")
            continue
        await cache.delete(_record_key(contribution_id), required=True)
        removed += 1
    return removed


async def export_jsonl() -> str:
    """Every stored record, one JSON object a line, for `eval.cli field`.

    The contributor tag is dropped: the export is for measurement, and a tag
    would let rows be grouped by install."""
    cache = deps.cache
    count = int(await cache.get("outcome-seq", required=True) or 0)
    lines: list[str] = []
    for n in range(1, count + 1):
        contribution_id = await cache.get(_slot_key(n), required=True)
        if not contribution_id:
            continue
        raw = await cache.get(_record_key(contribution_id), required=True)
        if not raw:
            continue                        # deleted since
        record = json.loads(raw)
        record.pop("contributor", None)
        lines.append(json.dumps(record, sort_keys=True))
    return "\n".join(lines) + ("\n" if lines else "")


router = APIRouter(prefix="/outcomes", tags=["outcomes"])

_UNAVAILABLE = "Sharing is temporarily unavailable. Please try again shortly."


async def _admit(principal: Principal, request: Request) -> None:
    """Attested callers only, then this route's own limit."""
    if not principal.authenticated:
        raise HTTPException(status_code=401, detail="Authentication required.",
                            headers={"WWW-Authenticate": "Bearer"})
    if limiter is not None:
        await limiter(principal.subject, ratelimit.client_ip(request))


@router.post("")
async def share(outcome: Outcome, request: Request,
                principal: Principal = Depends(require_auth)) -> dict:
    await _admit(principal, request)
    if outcome.estimate_high < outcome.estimate_low:
        raise HTTPException(status_code=422, detail="estimate_high is below estimate_low")
    try:
        created = await store(outcome)
    except OutcomeError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    except CacheUnavailable:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE) from None
    return {"stored": True, "created": created}


@router.delete("")
async def unshare(req: Deletion, request: Request,
                  principal: Principal = Depends(require_auth)) -> dict:
    await _admit(principal, request)
    try:
        removed = await delete(req)
    except OutcomeError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    except CacheUnavailable:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE) from None
    return {"deleted": removed}
