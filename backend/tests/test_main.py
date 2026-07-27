"""
SnapWorth backend tests.

Run with:
    cd backend
    pip install -r requirements-dev.txt
    pytest tests/ -v
"""

import json
import io
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from main import app, _extract_json, _check_rate_limit, _rate_store, _ip_rate_store

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
        assert "ai_key_set" in data

    def test_status_ok(self):
        assert client.get("/health").json()["status"] == "ok"

    def test_version_present(self):
        assert client.get("/health").json()["version"] == "1.0.0"


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
    image_data = b"\xff\xd8\xff" + b"\x00" * size  # fake JPEG header
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
                      "confidence", "sold_listings_count",
                      "listing_title", "listing_description"]:
            assert field in data, f"missing field: {field}"

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

    def test_malformed_json_from_gemini_returns_500(self):
        mock_response = MagicMock()
        mock_response.text = "I cannot identify this item."
        with patch("main._model") as mock_model:
            mock_model.generate_content_async = AsyncMock(return_value=mock_response)
            r = _make_scan_request(device_id="malformed-test")
        assert r.status_code == 500

    def test_accepts_png(self):
        mock_response = MagicMock()
        mock_response.text = json.dumps(MOCK_RESPONSE_JSON)
        with patch("main._model") as mock_model:
            mock_model.generate_content_async = AsyncMock(return_value=mock_response)
            r = client.post(
                "/scan",
                files={"file": ("scan.png", io.BytesIO(b"\x89PNG\r\n" + b"\x00" * 512), "image/png")},
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


def _post_listing(device_id="listing-test", **overrides):
    return client.post(
        "/listing", json=_listing_body(**overrides), headers={"x-device-id": device_id}
    )


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
        for mkt in ["ebay", "vinted", "facebook", "olx"]:
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
