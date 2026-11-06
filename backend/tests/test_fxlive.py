"""GET /rates and `fxlive`: ECB reference rates as per-dollar multipliers."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

import main
from core import fxlive

client = TestClient(main.app)

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01">
<Cube><Cube time='2026-11-05'>
<Cube currency='USD' rate='1.0800'/><Cube currency='RON' rate='4.9770'/>
<Cube currency='GBP' rate='0.8400'/><Cube currency='JPY' rate='160.10'/>
</Cube></Cube></gesmes:Envelope>"""


@pytest.fixture(autouse=True)
def _fresh():
    fxlive.reset()
    main._rate_store.clear()
    main._ip_rate_store.clear()
    yield
    fxlive.reset()


def run(coro):
    return asyncio.run(coro)


class TestParse:
    def test_rates_are_per_dollar(self):
        table = fxlive.parse(FEED)
        assert table["date"] == "2026-11-05"
        assert table["rates"]["EUR"] == pytest.approx(1 / 1.08, rel=1e-5)
        assert table["rates"]["RON"] == pytest.approx(4.977 / 1.08, rel=1e-5)

    def test_only_the_apps_currencies(self):
        assert "JPY" not in fxlive.parse(FEED)["rates"]
        assert "USD" not in fxlive.parse(FEED)["rates"]

    def test_the_lev_uses_its_fixed_rate(self):
        assert fxlive.parse(FEED)["rates"]["BGN"] == pytest.approx(1.95583 / 1.08, rel=1e-5)

    def test_no_dollar_rate_is_no_table(self):
        assert fxlive.parse(FEED.replace("USD", "XXX")) is None
        assert fxlive.parse("not xml at all") is None


class TestCache:
    def test_fetches_once_per_ttl(self):
        calls = []

        async def fetch():
            calls.append(1)
            return FEED

        run(fxlive.rates(fetch, now=lambda: 1000.0))
        run(fxlive.rates(fetch, now=lambda: 1000.0 + fxlive.TTL - 1))
        assert len(calls) == 1
        run(fxlive.rates(fetch, now=lambda: 1000.0 + fxlive.TTL + 1))
        assert len(calls) == 2

    def test_a_failure_keeps_the_last_table_until_stale(self):
        async def good():
            return FEED

        async def bad():
            raise httpx.ConnectError("down")

        run(fxlive.rates(good, now=lambda: 0.0))
        assert run(fxlive.rates(bad, now=lambda: fxlive.TTL + 1.0)) is not None
        assert run(fxlive.rates(bad, now=lambda: fxlive.STALE_AFTER + fxlive.TTL * 2)) is None


class TestRoute:
    def test_serves_the_table(self):
        async def table():
            return fxlive.parse(FEED)

        with patch("main.fxlive.rates", table):
            r = client.get("/rates", headers={"x-device-id": "rates-test"})
        assert r.status_code == 200
        assert r.json()["rates"]["RON"] == pytest.approx(4.977 / 1.08, rel=1e-4)

    def test_no_table_is_a_503(self):
        async def none():
            return None

        with patch("main.fxlive.rates", none):
            r = client.get("/rates", headers={"x-device-id": "rates-none"})
        assert r.status_code == 503
