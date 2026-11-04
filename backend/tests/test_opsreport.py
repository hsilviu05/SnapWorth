"""`GET /ops/report`: the bot's read-only reports over HTTP, behind a token."""

from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from main import app  # noqa: E402
from ops import opsreport  # noqa: E402

# A phrase, not a credential shape, so the secret scanner has nothing to flag.
TOKEN = "test-report-token-not-a-real-secret"

client = TestClient(app)


@pytest.fixture
def auth(monkeypatch):
    monkeypatch.setenv("OPS_REPORT_TOKEN", TOKEN)
    return {"Authorization": f"Bearer {TOKEN}"}


class TestNames:
    def test_empty_means_all(self):
        assert opsreport.requested("") == list(opsreport.REPORTS)

    def test_picks_in_report_order(self):
        assert opsreport.requested("Trials, /subs") == ["subs", "trials"]

    @pytest.mark.parametrize("name", ["lever", "sub", "checkup", "post", "nope", "revenue backfill"])
    def test_anything_else_is_refused(self, name):
        assert opsreport.requested(f"trials,{name}") is None

    def test_writing_and_paid_commands_are_not_allowed(self):
        for name in ("lever", "sub", "minbuild", "referrals", "clear", "feed", "checkup",
                     "post", "caption", "hooks", "reply", "price", "trend", "user", "social"):
            assert name not in opsreport.REPORTS

    def test_html_becomes_text(self):
        assert opsreport.to_text("<b>Trials</b> &lt;3 <pre>a &amp; b</pre>") == "Trials <3 a & b"


class TestAccess:
    def test_unconfigured_serves_nothing(self, monkeypatch):
        monkeypatch.delenv("OPS_REPORT_TOKEN", raising=False)
        assert client.get("/ops/report").status_code == 404

    def test_anonymous_and_wrong_token_are_refused(self, auth):
        assert client.get("/ops/report").status_code == 404
        assert client.get("/ops/report",
                          headers={"Authorization": "Bearer wrong"}).status_code == 404
        assert client.get("/ops/report",
                          headers={"Authorization": "Bearer ä".encode()})  # pyright: ignore[reportArgumentType].status_code == 404

    def test_metrics_token_does_not_open_it(self, auth, monkeypatch):
        monkeypatch.setenv("METRICS_TOKEN", "a-different-metrics-phrase")
        assert client.get("/ops/report", headers={
            "Authorization": "Bearer a-different-metrics-phrase"}).status_code == 404

    def test_unknown_name_is_404(self, auth):
        assert client.get("/ops/report?names=lever", headers=auth).status_code == 404

    def test_serves_plain_text(self, auth):
        response = client.get("/ops/report?names=trials,lifetime", headers=auth)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert response.headers["cache-control"] == "no-store"
        assert "===== /lifetime =====" in response.text
        assert "===== /trials =====" in response.text
        assert "<b>" not in response.text


class TestFailures:
    @pytest.mark.asyncio
    async def test_one_failing_report_does_not_sink_the_rest(self, monkeypatch):
        async def handler(text):
            if text == "/subs":
                raise RuntimeError("boom")
            return f"<b>{text}</b>", []
        monkeypatch.setattr(opsreport.notify, "handle_command_with_buttons", handler)
        out = await opsreport.report(["subs", "trials"])
        assert "/subs: failed (RuntimeError)" in out
        assert "===== /trials =====\n/trials" in out
