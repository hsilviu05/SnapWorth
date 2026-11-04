"""`GET /ops/report`: the bot's read-only reports as plain text, over HTTP.

The same text an operator gets by typing the command in Telegram, so it can be
read by a script (or an assistant) without pasting screenshots. Only commands
that read are allowed: nothing that writes (/lever, /revenue backfill, /sub,
which records grace), calls a model (/checkup, /post, /caption …) or reaches a
third party (/social, /trend).
"""

from __future__ import annotations

import asyncio
import html
import logging
import re

from ops import notify

log = logging.getLogger(__name__)

#: In the order a combined report lists them.
REPORTS = ("status", "subs", "trials", "paywall", "revenue", "lifetime", "users",
           "costs", "week", "digest", "experiment")

REPORT_TIMEOUT_SECONDS = 20.0

_TAG = re.compile(r"<[^>]+>")


def to_text(message: str) -> str:
    """Telegram HTML to plain text: tags dropped, entities decoded."""
    return html.unescape(_TAG.sub("", message))


def requested(names: str) -> list[str] | None:
    """The reports a `names` query asks for, in REPORTS order; all when empty.

    None when any name is not an allowed report, so a typo is refused rather
    than silently answered with the help text.
    """
    wanted = [n.strip().lower().lstrip("/") for n in (names or "").split(",") if n.strip()]
    if not wanted:
        return list(REPORTS)
    if any(n not in REPORTS for n in wanted):
        return None
    return [n for n in REPORTS if n in wanted]


async def _one(name: str) -> str:
    try:
        reply = await asyncio.wait_for(notify.handle_command_with_buttons("/" + name),
                                       REPORT_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return f"/{name}: no answer within {REPORT_TIMEOUT_SECONDS:.0f}s"
    except Exception as exc:
        log.warning("report /%s failed: %s", name, type(exc).__name__)
        return f"/{name}: failed ({type(exc).__name__})"
    return to_text(reply[0]) if reply else f"/{name}: no reply"


async def report(names: list[str]) -> str:
    parts = await asyncio.gather(*(_one(n) for n in names))
    return "\n\n".join(f"===== /{n} =====\n{p.strip()}" for n, p in zip(names, parts)) + "\n"
