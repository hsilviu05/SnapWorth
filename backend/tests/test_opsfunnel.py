"""The weekly report's TelemetryDeck funnel."""

from __future__ import annotations

import os
import sys
from datetime import date

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ops import opsfunnel  # noqa: E402

START, END, TODAY = date(2026, 10, 28), date(2026, 11, 3), date(2026, 11, 4)
COUNTS = {"app_opened": 200, "scan_result_shown": 150, "free_scan_limit_hit": 40,
          "paywall_viewed": 50, "purchase_started": 10, "purchase_completed": 4}


def answer(values: dict) -> dict:
    return {"result": {"type": "timeseriesResult",
                       "rows": [{"timestamp": "2026-10-28T00:00:00Z", "result": values}]}}


class TestQuery:
    def test_counts_unique_users_per_signal_over_the_week(self):
        q = opsfunnel.build_query(START, END, "APP", "SRC", today=TODAY)
        assert q["relativeIntervals"] == [{
            "beginningDate": {"component": "day", "offset": -7, "position": "beginning"},
            "endDate": {"component": "day", "offset": -1, "position": "end"}}]
        names = [a["aggregator"]["name"] for a in q["aggregations"]]
        assert names == [s for s, _ in opsfunnel.STEPS]
        assert all(a["aggregator"]["type"] == "userCount" for a in q["aggregations"])
        assert {"type": "selector", "dimension": "isTestMode", "value": "false"} in q["filter"]["fields"]


class TestParse:
    def test_reads_one_number_per_step(self):
        assert opsfunnel.parse_counts(answer({**COUNTS, "paywall_viewed": 50.0})) == COUNTS

    def test_a_missing_step_is_none(self):
        partial = {k: v for k, v in COUNTS.items() if k != "paywall_viewed"}
        assert opsfunnel.parse_counts(answer(partial)) is None
        assert opsfunnel.parse_counts({"error": "x"}) is None

    def test_lines(self):
        lines = opsfunnel.format_lines(COUNTS)
        assert lines[1] == "Opened the app: 200"
        assert lines[4] == "Saw the paywall: 50 · 25% of openers"


class TestFetch:
    @pytest.mark.asyncio
    async def test_off_without_a_token(self, monkeypatch):
        monkeypatch.delenv("TELEMETRYDECK_TOKEN", raising=False)
        assert await opsfunnel.funnel_lines(START, END) == []

    @pytest.mark.asyncio
    async def test_answer_and_failures(self, monkeypatch):
        monkeypatch.setenv("TELEMETRYDECK_TOKEN", "tdpat_test")
        replies = iter([httpx.Response(200, json=answer(COUNTS)),
                        httpx.Response(401, json={}),
                        httpx.ConnectTimeout("slow")])
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers["authorization"]
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply

        real = httpx.AsyncClient
        monkeypatch.setattr(opsfunnel.httpx, "AsyncClient",
                            lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
        assert (await opsfunnel.funnel_lines(START, END))[1] == "Opened the app: 200"
        assert seen["auth"] == "Bearer tdpat_test"
        assert await opsfunnel.funnel_lines(START, END) == [
            "🔻 Funnel: TelemetryDeck answered HTTP 401"]
        assert await opsfunnel.funnel_lines(START, END) == [
            "🔻 Funnel: TelemetryDeck did not answer (ConnectTimeout)"]


class TestWeeklyReport:
    @pytest.mark.asyncio
    async def test_the_funnel_rides_the_weekly_report(self, monkeypatch):
        from datetime import datetime, timezone

        from core.cache import InMemoryCache, ResilientCache
        from ops import notify

        async def lines(start, end):
            return ["🔻 <b>Funnel</b>", f"{start}→{end}"]

        monkeypatch.setattr(opsfunnel, "funnel_lines", lines)
        notify.configure(ResilientCache(None, InMemoryCache()))
        try:
            text = await notify._weekly_text(datetime(2026, 11, 4, 9, tzinfo=timezone.utc))
        finally:
            await notify.aclose()
        assert "🔻 <b>Funnel</b>\n2026-10-28→2026-11-03" in text
