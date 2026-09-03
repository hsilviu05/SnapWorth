"""
SnapWorth backend tests.

Run with:
    cd backend
    pip install -r requirements-dev.txt
    pytest tests/ -v
"""

import asyncio
import json
import io
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import auth
from entitlements import Entitlement
import main
from main import app, _extract_json, _check_rate_limit, _rate_store, _ip_rate_store
from tests.images import VALID_PNG, padded_image_bytes

client = TestClient(app)


# ── _extract_json ─────────────────────────────────────────────────────────────

class TestExtractJson:
    def test_plain_json(self):
        raw = '{"item_name": "Nike Shoes", "brand": "Nike"}'
        result = _extract_json(raw)
        assert result["item_name"] == "Nike Shoes"

    def test_markdown_json_fence(self):
        raw = '```json\n{"item_name": "Levi\'s Jeans"}\n```'
        result = _extract_json(raw)
        assert result["item_name"] == "Levi's Jeans"

    def test_markdown_plain_fence(self):
        raw = '```\n{"brand": "Patagonia"}\n```'
        result = _extract_json(raw)
        assert result["brand"] == "Patagonia"

    def test_json_embedded_in_text(self):
        raw = 'Here is the analysis:\n{"confidence": "High"}\nHope that helps!'
        result = _extract_json(raw)
        assert result["confidence"] == "High"

    def test_whitespace_trimmed(self):
        raw = '   \n  {"est_value_low_usd": 12.0}  \n  '
        result = _extract_json(raw)
        assert result["est_value_low_usd"] == 12.0

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json("not json at all")

    def test_empty_string_raises(self):
        with pytest.raises((json.JSONDecodeError, ValueError)):
            _extract_json("")


# ── Rate limiting ─────────────────────────────────────────────────────────────

class TestRateLimit:
    def setup_method(self):
        _rate_store.clear()
        _ip_rate_store.clear()

    def test_allows_requests_under_limit(self):
        for _ in range(5):
            _check_rate_limit("test-device-001")

    def test_blocks_at_limit(self):
        from fastapi import HTTPException
        device = "test-device-002"
        for _ in range(20):
            _check_rate_limit(device)
        with pytest.raises(HTTPException) as exc_info:
            _check_rate_limit(device)
        assert exc_info.value.status_code == 429

    def test_different_devices_independent(self):
        for _ in range(20):
            _check_rate_limit("device-a")
        # device-b should still be allowed
        _check_rate_limit("device-b")

    def test_device_id_truncated_to_64_chars(self):
        long_id = "x" * 128
        _check_rate_limit(long_id)
        assert long_id[:64] in _rate_store
        assert long_id not in _rate_store


# ── GET /health ───────────────────────────────────────────────────────────────

class TestHealthEndpoint:
    def test_returns_200(self):
        response = client.get("/health")
        assert response.status_code == 200

    def test_response_shape(self):
        data = client.get("/health").json()
        assert "status" in data
        assert "version" in data
        assert "commit" in data
        assert "ai_key_set" in data

    def test_status_ok(self):
        assert client.get("/health").json()["status"] == "ok"

    def test_version_present(self):
        """Asserts a version is reported, not which one.

        This used to pin the literal "1.2.0", which meant the test had to be
        edited on every release and quietly enforced that the value stay
        hardcoded — the exact property that made /health useless for telling
        which build was live.
        """
        version = client.get("/health").json()["version"]
        assert isinstance(version, str) and version, "no version reported"


class TestBuildIdentity:
    """Precedence rules for the version and commit reported by /health.

    Exercised as pure functions over an env mapping. The obvious alternative —
    monkeypatching os.environ and reloading `main` — rebuilds the FastAPI app
    object and invalidates every TestClient already bound to the old one, which
    fails ~40 unrelated tests in other modules.
    """

    def test_version_defaults_when_unset(self):
        assert main.resolve_api_version({}) == main.DEFAULT_API_VERSION

    def test_release_version_overrides_the_default(self):
        assert main.resolve_api_version({"RELEASE_VERSION": "9.9.9"}) == "9.9.9"

    def test_blank_release_version_falls_back(self):
        """An env var set to empty string is the same as unset — Railway makes
        it easy to create a variable and leave the value blank."""
        assert main.resolve_api_version({"RELEASE_VERSION": "   "}) == main.DEFAULT_API_VERSION

    def test_explicit_git_commit_wins(self):
        env = {"GIT_COMMIT": "1111111111111111", "RAILWAY_GIT_COMMIT_SHA": "2222222222222222"}
        assert main.resolve_git_commit(env) == "111111111111"

    def test_falls_back_to_railway_injected_sha(self):
        """Only Railway's GitHub integration sets this — never `railway up`,
        which is how this project deploys. Kept because the variable is still
        the right second choice if the deploy method ever changes."""
        assert main.resolve_git_commit(
            {"RAILWAY_GIT_COMMIT_SHA": "abcdef0123456789deadbeef"}) == "abcdef012345"

    def test_truncated_to_twelve_characters(self):
        assert len(main.resolve_git_commit({"GIT_COMMIT": "a" * 40})) == 12

    def test_build_commit_file_used_when_env_is_empty(self):
        """The path that actually matters in production.

        Both env vars are empty under `railway up`: GIT_COMMIT is not set, and
        RAILWAY_GIT_COMMIT_SHA is only populated by Railway's GitHub
        integration. Before the file fallback existed, /health therefore
        reported commit "unknown" in production for the entire time this
        feature was believed to be working.
        """
        assert main.resolve_git_commit({}, "abcdef0123456789") == "abcdef012345"

    def test_env_takes_precedence_over_the_file(self):
        assert main.resolve_git_commit(
            {"GIT_COMMIT": "1" * 40}, "abcdef0123456789") == "1" * 12

    def test_blank_build_commit_file_is_not_a_commit(self):
        """A file that exists but is empty — a truncated write, or a CI step
        that ran but produced nothing — must read as unknown, not as ""."""
        assert main.resolve_git_commit({}, "   \n") == "unknown"

    def test_missing_build_commit_file_is_not_fatal(self, tmp_path):
        """Reporting build identity must never be able to stop the service."""
        assert main.read_build_commit_file(tmp_path / "absent") == ""

    def test_build_commit_file_is_read_and_stripped(self, tmp_path):
        f = tmp_path / "BUILD_COMMIT"
        f.write_text("  deadbeefcafe0123  \n", encoding="utf-8")
        assert main.read_build_commit_file(f) == "deadbeefcafe0123"

    def test_unknown_rather_than_blank_when_unset(self):
        """Never an empty string — blank reads as a rendering bug at exactly the
        moment someone is trying to trust this endpoint."""
        # Empty string for the file, not None: None would read the real
        # filesystem, so this test would break on any machine that had run a
        # deploy and left a BUILD_COMMIT behind.
        assert main.resolve_git_commit({}, "") == "unknown"
        assert main.resolve_git_commit(
            {"GIT_COMMIT": "", "RAILWAY_GIT_COMMIT_SHA": ""}, "") == "unknown"


# ── GET /privacy and /terms ───────────────────────────────────────────────────

class TestLegalEndpoints:
    def test_privacy_returns_html(self):
        r = client.get("/privacy")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "Privacy Policy" in r.text

    def test_terms_returns_html(self):
        r = client.get("/terms")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "Terms of Service" in r.text


# ── POST /scan ────────────────────────────────────────────────────────────────

MOCK_RESPONSE_JSON = {
    "item_name": "Patagonia Better Sweater",
    "brand": "Patagonia",
    "category": "clothing",
    "condition_notes": "Good — light pilling",
    "est_value_low_usd": 45.0,
    "est_value_high_usd": 90.0,
    "confidence": "High",
    "sold_listings_count": 38,
    "listing_title": "Patagonia Better Sweater Fleece",
    "listing_description": "Great used condition.",
}

def _make_scan_request(content_type="image/jpeg", size=1024, device_id="test-device"):
    image_data = padded_image_bytes("JPEG", size)
    return client.post(
        "/scan",
        files={"file": ("scan.jpg", io.BytesIO(image_data), content_type)},
        headers={"x-device-id": device_id},
    )

class TestScanEndpoint:
    def setup_method(self):
        _rate_store.clear()
        _ip_rate_store.clear()

    def test_rejects_unsupported_file_type(self):
        r = client.post(
            "/scan",
            files={"file": ("doc.pdf", io.BytesIO(b"%PDF"), "application/pdf")},
            headers={"x-device-id": "test-device"},
        )
        assert r.status_code == 400
        assert "Unsupported" in r.json()["detail"]

    def test_rejects_empty_file(self):
        r = client.post(
            "/scan",
            files={"file": ("empty.jpg", io.BytesIO(b""), "image/jpeg")},
            headers={"x-device-id": "test-device"},
        )
        assert r.status_code == 400
        assert "Empty" in r.json()["detail"]

    def test_rejects_oversized_file(self):
        big = io.BytesIO(b"\xff\xd8\xff" + b"\x00" * (11 * 1024 * 1024))
        r = client.post(
            "/scan",
            files={"file": ("big.jpg", big, "image/jpeg")},
            headers={"x-device-id": "test-device"},
        )
        assert r.status_code == 400
        assert "10 MB" in r.json()["detail"]

    def test_rate_limited_after_20_requests(self):
        mock_response = MagicMock()
        mock_response.text = json.dumps(MOCK_RESPONSE_JSON)
        with patch("main._model") as mock_model:
            mock_model.generate_content_async = AsyncMock(return_value=mock_response)
            for _ in range(20):
                _make_scan_request(device_id="rate-limit-test")
        r = _make_scan_request(device_id="rate-limit-test")
        assert r.status_code == 429

    def test_successful_scan_returns_correct_shape(self):
        mock_response = MagicMock()
        mock_response.text = json.dumps(MOCK_RESPONSE_JSON)
        with patch("main._model") as mock_model:
            mock_model.generate_content_async = AsyncMock(return_value=mock_response)
            r = _make_scan_request(device_id="shape-test")
        assert r.status_code == 200
        data = r.json()
        for field in ["item_name", "brand", "category", "condition_notes",
                      "est_value_low_usd", "est_value_high_usd",
                      "confidence", "listing_title", "listing_description"]:
            assert field in data, f"missing field: {field}"
        # Retired with #49: never real, never to return under this name.
        assert "sold_listings_count" not in data

    def test_inverted_values_are_swapped(self):
        inverted = {**MOCK_RESPONSE_JSON, "est_value_low_usd": 90.0, "est_value_high_usd": 45.0}
        mock_response = MagicMock()
        mock_response.text = json.dumps(inverted)
        with patch("main._model") as mock_model:
            mock_model.generate_content_async = AsyncMock(return_value=mock_response)
            r = _make_scan_request(device_id="swap-test")
        data = r.json()
        assert data["est_value_low_usd"] < data["est_value_high_usd"]
        assert data["est_value_low_usd"] == 45.0
        assert data["est_value_high_usd"] == 90.0

    def test_equal_values_are_spread(self):
        equal = {**MOCK_RESPONSE_JSON, "est_value_low_usd": 50.0, "est_value_high_usd": 50.0}
        mock_response = MagicMock()
        mock_response.text = json.dumps(equal)
        with patch("main._model") as mock_model:
            mock_model.generate_content_async = AsyncMock(return_value=mock_response)
            r = _make_scan_request(device_id="spread-test")
        data = r.json()
        assert data["est_value_low_usd"] < data["est_value_high_usd"]

    def test_gemini_failure_returns_502(self):
        with patch("main._model") as mock_model:
            mock_model.generate_content_async = AsyncMock(side_effect=Exception("API error"))
            r = _make_scan_request(device_id="fail-test")
        assert r.status_code == 502

    def test_malformed_json_from_gemini_returns_502(self):
        # An unparseable upstream reply is an upstream failure, not a bug in this
        # service, so it surfaces as 502. One reformat retry runs first.
        mock_response = MagicMock()
        mock_response.text = "I cannot identify this item."
        with patch("main._model") as mock_model:
            mock_model.generate_content_async = AsyncMock(return_value=mock_response)
            r = _make_scan_request(device_id="malformed-test")
        assert r.status_code == 502

    def test_accepts_png(self):
        mock_response = MagicMock()
        mock_response.text = json.dumps(MOCK_RESPONSE_JSON)
        with patch("main._model") as mock_model:
            mock_model.generate_content_async = AsyncMock(return_value=mock_response)
            r = client.post(
                "/scan",
                files={"file": ("scan.png", io.BytesIO(VALID_PNG), "image/png")},
                headers={"x-device-id": "png-test"},
            )
        assert r.status_code == 200


# ── POST /listing (Snap → Sell) ───────────────────────────────────────────────

MOCK_LISTING_JSON = {
    "title": "Patagonia Better Sweater Fleece 1/4-Zip Medium",
    "description": "Classic Patagonia in good used condition. Light pilling, no stains.",
    "listing_price": 75.0,
    "negotiation_floor": 55.0,
    "category": "Men's Clothing",
}


def _listing_body(**overrides):
    body = {
        "item_name": "Patagonia Better Sweater",
        "brand": "Patagonia",
        "category": "clothing",
        "condition": "good",
        "price_low_usd": 45.0,
        "price_likely_usd": 68.0,
        "price_high_usd": 90.0,
        "marketplace": "ebay",
        "currency": "USD",
    }
    body.update(overrides)
    return body


def _pro_headers(subject: str) -> dict:
    """Auth header for a subject the entitlement cache reports as Pro.

    `/listing` is a Pro-only endpoint, and `require_auth` re-reads the tier from
    the entitlement cache rather than trusting the token claim, so the cache has
    to be seeded — minting a `tier="pro"` token alone is not enough.
    """
    ent = Entitlement(
        tier="pro",
        product_id="com.snapworth.yearly",
        expires_at=int(time.time()) + 86_400,
        original_transaction_id=f"txn-{subject}",
        environment="Production",
    )
    asyncio.run(auth.deps.cache.set(f"ent:{subject}", ent.to_json(), 3600))
    assert auth.deps.signer is not None   # set by the build_deps/conftest fixture
    token, _ = auth.deps.signer.mint(subject, tier="pro")
    return {"Authorization": f"Bearer {token}"}


def _post_listing(device_id="listing-test", *, pro=True, **overrides):
    headers = {"x-device-id": device_id}
    if pro:
        # Rate limits key on `principal.subject`, so the subject must track the
        # device id for the per-caller rate-limit test to still isolate.
        headers |= _pro_headers(f"pro-{device_id}")
    return client.post("/listing", json=_listing_body(**overrides), headers=headers)


class TestListingEndpoint:
    def setup_method(self):
        _rate_store.clear()
        _ip_rate_store.clear()

    @staticmethod
    def _mock(text):
        m = MagicMock()
        m.text = text
        return m

    def test_successful_listing_shape(self):
        with patch("main._model") as mm:
            mm.generate_content_async = AsyncMock(return_value=self._mock(json.dumps(MOCK_LISTING_JSON)))
            r = _post_listing()
        assert r.status_code == 200
        data = r.json()
        for f in ["title", "description", "listing_price", "negotiation_floor", "category"]:
            assert f in data, f"missing field: {f}"

    def test_all_supported_marketplaces_accepted(self):
        for mkt in ["ebay", "poshmark", "mercari", "depop", "vinted", "facebook", "olx"]:
            _rate_store.clear()
            _ip_rate_store.clear()
            with patch("main._model") as mm:
                mm.generate_content_async = AsyncMock(return_value=self._mock(json.dumps(MOCK_LISTING_JSON)))
                r = _post_listing(marketplace=mkt)
            assert r.status_code == 200, mkt

    def test_unsupported_marketplace_rejected(self):
        r = _post_listing(marketplace="craigslist")
        assert r.status_code == 400
        assert "Unsupported" in r.json()["detail"]

    # ── Entitlement gate ─────────────────────────────────────────────────────
    # Snap → Sell is a Pro feature. Before this gate existed the endpoint was
    # reachable by any caller who could attest, making the paywall client-side
    # only and leaving an unmetered path to the AI provider.

    def test_free_tier_denied_with_402(self):
        with patch("main._model") as mm:
            mm.generate_content_async = AsyncMock(
                return_value=self._mock(json.dumps(MOCK_LISTING_JSON)))
            r = _post_listing(pro=False)
        assert r.status_code == 402
        assert "Pro" in r.json()["detail"]

    def test_free_tier_never_reaches_the_model(self):
        """The gate must run *before* the paid dependency, not after."""
        with patch("main._model") as mm:
            mm.generate_content_async = AsyncMock(
                return_value=self._mock(json.dumps(MOCK_LISTING_JSON)))
            _post_listing(pro=False)
            mm.generate_content_async.assert_not_awaited()

    def test_validation_errors_still_precede_the_gate(self):
        """A malformed request is a 400 regardless of tier — don't leak the
        paywall as the answer to every bad request."""
        r = _post_listing(pro=False, marketplace="craigslist")
        assert r.status_code == 400

    def test_marketplace_case_insensitive(self):
        with patch("main._model") as mm:
            mm.generate_content_async = AsyncMock(return_value=self._mock(json.dumps(MOCK_LISTING_JSON)))
            r = _post_listing(marketplace="eBay")
        assert r.status_code == 200

    def test_gemini_failure_returns_502(self):
        with patch("main._model") as mm:
            mm.generate_content_async = AsyncMock(side_effect=Exception("API down"))
            r = _post_listing()
        assert r.status_code == 502

    def test_malformed_json_falls_back_not_errors(self):
        # A garbled model reply must yield a usable listing, never a blank or 500.
        with patch("main._model") as mm:
            mm.generate_content_async = AsyncMock(return_value=self._mock("sorry, I can't do that"))
            r = _post_listing()
        assert r.status_code == 200
        data = r.json()
        assert data["title"]
        assert data["listing_price"] > 0
        assert "Patagonia" in data["title"]

    def test_floor_never_exceeds_ask(self):
        bad = {**MOCK_LISTING_JSON, "listing_price": 40.0, "negotiation_floor": 80.0}
        with patch("main._model") as mm:
            mm.generate_content_async = AsyncMock(return_value=self._mock(json.dumps(bad)))
            r = _post_listing()
        data = r.json()
        assert data["negotiation_floor"] <= data["listing_price"]

    def test_missing_prices_repaired_from_request(self):
        partial = {"title": "Nice item", "description": "Good stuff", "category": "x",
                   "listing_price": 0, "negotiation_floor": 0}
        with patch("main._model") as mm:
            mm.generate_content_async = AsyncMock(return_value=self._mock(json.dumps(partial)))
            r = _post_listing()
        data = r.json()
        assert data["listing_price"] > 0
        assert data["negotiation_floor"] > 0

    def test_invalid_condition_defaults_to_good(self):
        with patch("main._model") as mm:
            mm.generate_content_async = AsyncMock(return_value=self._mock(json.dumps(MOCK_LISTING_JSON)))
            r = _post_listing(condition="pristine")
        assert r.status_code == 200

    def test_missing_required_field_is_422(self):
        body = _listing_body()
        del body["item_name"]
        r = client.post("/listing", json=body, headers={"x-device-id": "listing-422"})
        assert r.status_code == 422

    def test_rate_limited_after_20_requests(self):
        with patch("main._model") as mm:
            mm.generate_content_async = AsyncMock(return_value=self._mock(json.dumps(MOCK_LISTING_JSON)))
            for _ in range(20):
                _post_listing(device_id="listing-rate")
            r = _post_listing(device_id="listing-rate")
        assert r.status_code == 429
