"""The app's own funnel, from TelemetryDeck, for the weekly report.

The server sees trials and purchases but not the screens before them: how many
people who open the app reach a scan result, the free limit, the paywall. The
app signals those to TelemetryDeck, so this asks TelemetryDeck's query API for
the unique users behind each signal over the report's week.

Off until `TELEMETRYDECK_TOKEN` is set (a personal access token, `tdpat_…`,
which needs a paid TelemetryDeck plan and expires within a year). Each step is
counted on its own, not as a strict sequence: "saw the paywall" includes
people who opened it from Settings without hitting the limit.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone

import httpx

log = logging.getLogger(__name__)

QUERY_URL = "https://api.telemetrydeckapi.com/api/v4/query/tql"
# The app ID the iOS app signals under (`Config.telemetryDeckAppID`); public in
# the binary, so a default rather than a secret.
DEFAULT_APP_ID = "D4C9C11E-F611-4B92-9646-0DF0B2E0F10C"
DEFAULT_DATA_SOURCE = "com.telemetrydeck.all"
TIMEOUT_SECONDS = 15.0

#: Signal names as `AnalyticsEvent` sends them, and how the report says them.
STEPS: tuple[tuple[str, str], ...] = (
    ("app_opened", "Opened the app"),
    ("scan_result_shown", "Saw a scan result"),
    ("free_scan_limit_hit", "Hit the free limit"),
    ("paywall_viewed", "Saw the paywall"),
    ("purchase_started", "Started a purchase"),
    ("purchase_completed", "Completed a purchase"),
)


def build_query(start: date, end: date, app_id: str, data_source: str,
                today: date | None = None) -> dict:
    """Unique users per signal between `start` and `end`, inclusive (UTC days).

    Days relative to today, the form TelemetryDeck's API documents."""
    today = today or datetime.now(timezone.utc).date()
    return {
        "queryType": "timeseries",
        "dataSource": data_source,
        "granularity": "all",
        "relativeIntervals": [{
            "beginningDate": {"component": "day", "offset": (start - today).days,
                              "position": "beginning"},
            "endDate": {"component": "day", "offset": (end - today).days,
                        "position": "end"},
        }],
        "filter": {"type": "and", "fields": [
            {"type": "selector", "dimension": "appID", "value": app_id},
            {"type": "selector", "dimension": "isTestMode", "value": "false"},
        ]},
        "aggregations": [
            {"type": "filtered",
             "filter": {"type": "selector", "dimension": "type", "value": signal},
             "aggregator": {"type": "userCount", "name": signal}}
            for signal, _ in STEPS
        ],
    }


def parse_counts(body: dict) -> dict[str, int] | None:
    """Signal → unique users from a timeseries answer, or None when the
    answer does not have one number per step."""
    result = body.get("result") if isinstance(body, dict) else None
    rows = result.get("rows") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        return None
    merged: dict = {}
    for row in rows:
        values = row.get("result") if isinstance(row, dict) else None
        if isinstance(values, dict):
            merged.update(values)
    counts: dict[str, int] = {}
    for signal, _ in STEPS:
        value = merged.get(signal)
        if not isinstance(value, (int, float)):
            return None
        counts[signal] = int(round(value))
    return counts


def format_lines(counts: dict[str, int]) -> list[str]:
    lines = ["🔻 <b>Funnel</b> (unique users, TelemetryDeck)"]
    # Shares of openers only: the steps are counted independently, so one
    # step against the one before can pass 100% (Settings opens the paywall).
    top = counts.get(STEPS[0][0]) or 0
    for signal, label in STEPS:
        n = counts[signal]
        share = f" · {n / top:.0%} of openers" if top and signal != STEPS[0][0] else ""
        lines.append(f"{label}: {n}{share}")
    return lines


async def funnel_lines(start: date, end: date) -> list[str]:
    """The weekly report's funnel block; empty when TelemetryDeck is not set
    up. Never raises: a failure is one line, so the rest of the report sends."""
    token = os.environ.get("TELEMETRYDECK_TOKEN", "").strip()
    if not token:
        return []
    app_id = os.environ.get("TELEMETRYDECK_APP_ID", "").strip() or DEFAULT_APP_ID
    source = os.environ.get("TELEMETRYDECK_DATA_SOURCE", "").strip() or DEFAULT_DATA_SOURCE
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(
                QUERY_URL, json=build_query(start, end, app_id, source),
                headers={"Authorization": f"Bearer {token}"})
        if response.status_code != 200:
            # The body can echo the query, never the token; the status is enough.
            return [f"🔻 Funnel: TelemetryDeck answered HTTP {response.status_code}"]
        counts = parse_counts(response.json())
    except Exception as exc:
        log.warning("TelemetryDeck funnel query failed: %s", type(exc).__name__)
        return [f"🔻 Funnel: TelemetryDeck did not answer ({type(exc).__name__})"]
    if counts is None:
        return ["🔻 Funnel: TelemetryDeck's answer had no count per step"]
    return format_lines(counts)
