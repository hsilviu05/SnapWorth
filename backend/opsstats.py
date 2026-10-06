"""The daily counters, in the shared cache: `opsstats:<day>:<name>`.

Split out of notify.py (#230). What is counted here feeds the app as well as
the operator: `/trends` reports the week's scans to users. So nothing in this
module depends on Telegram; it needs a cache and nothing else, bound once at
startup by `notify.configure`. A counter the operator alone reads, and that
deliberately runs only while the bot is configured, stays in notify and bumps
through `bump`.

Every write is best-effort: a counter that fails to increment loses one count,
and never fails the request that caused it.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from background import spawn

if TYPE_CHECKING:
    from cache import ResilientCache

log = logging.getLogger("snapworth.notify")

# Counters live long enough for a 30-day spend view and the weekly report's
# two full weeks, plus slack; they are operational tallies, not records.
STATS_TTL = 60 * 60 * 24 * 35

_cache: ResilientCache | None = None


def bind(cache: ResilientCache | None) -> None:
    """Point the counters at the app's cache. None leaves them all no-ops."""
    global _cache
    _cache = cache


def day(at: datetime | None = None) -> str:
    return (at or datetime.now(timezone.utc)).strftime("%Y%m%d")


def stat_key(day: str, name: str) -> str:
    return f"opsstats:{day}:{name}"


async def bump(name: str) -> None:
    if _cache is None:
        return
    try:
        await _cache.incr(stat_key(day(), name), STATS_TTL)
    except Exception as exc:
        log.debug("ops counter %s failed: %s", name, type(exc).__name__)


def count_scan(tier: str) -> None:
    """Tally one successful scan. Fire-and-forget.

    Runs whenever there is a cache, Telegram or not: `/trends` reports this
    count to users ("N scans this week"), and a feature in the app must not
    depend on whether the operator's bot is configured."""
    if _cache is None:
        return
    spawn(bump("scans_pro" if tier == "pro" else "scans_free"))


async def read_stat(day: str, name: str) -> int:
    if _cache is None:
        return 0
    try:
        raw = await _cache.get(stat_key(day, name))
        return int(raw or 0)
    except Exception:
        return 0


async def read_doc_for_update(key: str) -> dict | None:
    """A JSON document read for a caller about to write the whole of it back.

    None means the store could not be read, and the caller must not write.
    A report's read (notify's `_read_index`) answers {} for that, which is
    right for a report and wrong here: a plain `get` on a failing Redis falls
    back to memory and returns None rather than raising, so the writer took
    the document as empty and, once Redis answered again, overwrote it with
    the one row it had just added. A 300-row subscription index became 1, and
    the auto-renew state and history in those rows came back from nowhere.
    `required=True` makes that read raise instead. A document that is present
    but unreadable is still replaced, as before.
    """
    if _cache is None:
        return None
    try:
        raw = await _cache.get(key, required=True)
    except Exception as exc:
        log.warning("index %s unreadable, not rewriting it: %s", key, type(exc).__name__)
        return None
    try:
        doc = json.loads(raw or "{}")
    except Exception:
        return {}
    return doc if isinstance(doc, dict) else {}
