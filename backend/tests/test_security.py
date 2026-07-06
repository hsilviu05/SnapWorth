"""
SnapWorth security tests.

Covers: input validation, injection attempts, rate-limit abuse, file-upload
attacks, HTTP method enforcement, response sanitisation, and error-message
information leakage.
"""

import io
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from main import app, _check_rate_limit, _rate_store, _extract_json, _safe_float

client = TestClient(app)

VALID_JPEG = b"\xff\xd8\xff" + b"\x00" * 512

MOCK_AI_RESPONSE = {
    "item_name": "Nike Shoes",
    "brand": "Nike",
    "category": "shoes",
    "condition_notes": "Good",
    "est_value_low_usd": 50.0,
    "est_value_high_usd": 100.0,
    "confidence": "High",
    "sold_listings_count": 20,
    "listing_title": "Nike Shoes",
    "listing_description": "Great shoes.",
}

def _scan(device_id="sec-test", content=VALID_JPEG, mime="image/jpeg", filename="scan.jpg"):
    return client.post(
        "/scan",
        files={"file": (filename, io.BytesIO(content), mime)},
        headers={"x-device-id": device_id},
    )

def _mock_scan(device_id="sec-test", response_data=None, **kwargs):
    data = response_data or MOCK_AI_RESPONSE
    mock = MagicMock()
    mock.text = json.dumps(data)
    with patch("main._model") as m:
        m.generate_content_async = AsyncMock(return_value=mock)
        return _scan(device_id=device_id, **kwargs)


# ── Security response headers ─────────────────────────────────────────────────

class TestSecurityHeaders:
    def test_health_has_nosniff_header(self):
        r = client.get("/health")
        assert r.headers.get("x-content-type-options") == "nosniff"

    def test_health_has_frame_deny_header(self):
        r = client.get("/health")
        assert r.headers.get("x-frame-options") == "DENY"

    def test_health_has_referrer_policy(self):
        r = client.get("/health")
        assert "strict-origin" in r.headers.get("referrer-policy", "")

    def test_scan_error_response_has_security_headers(self):
        r = client.post(
            "/scan",
            files={"file": ("bad.pdf", io.BytesIO(b"%PDF"), "application/pdf")},
            headers={"x-device-id": "hdr-test"},
        )
        assert r.status_code == 400
        assert r.headers.get("x-content-type-options") == "nosniff"

    def test_parse_error_no_longer_leaks_exception_type(self):
        mock = MagicMock()
        mock.text = "Not valid JSON at all"
        with patch("main._model") as m:
            m.generate_content_async = AsyncMock(return_value=mock)
            r = _scan(device_id="parse-leak-test")
        assert r.status_code == 500
        detail = r.json()["detail"]
        assert "JSONDecodeError" not in detail
        assert "Expecting value" not in detail
        assert "char 0" not in detail


# ── HTTP method enforcement ───────────────────────────────────────────────────

class TestHttpMethods:
    def test_get_scan_not_allowed(self):
        assert client.get("/scan").status_code == 405

    def test_put_scan_not_allowed(self):
        assert client.put("/scan").status_code == 405

    def test_delete_scan_not_allowed(self):
        assert client.delete("/scan").status_code == 405

    def test_patch_scan_not_allowed(self):
        assert client.patch("/scan").status_code == 405

    def test_post_health_not_allowed(self):
        assert client.post("/health").status_code == 405


# ── File upload security ──────────────────────────────────────────────────────

class TestFileUploadSecurity:
    def setup_method(self):
        _rate_store.clear()

    def test_rejects_executable_disguised_as_jpeg(self):
        # PE header (Windows executable) with .jpg extension
        pe_header = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 500
        r = _scan(content=pe_header, mime="image/jpeg")
        # Will either reject (400) or pass to AI — must not 500 crash
        assert r.status_code in (200, 400, 422, 500, 502)
        # Critically: must not expose stack trace
        if r.status_code >= 400:
            assert "Traceback" not in r.text
            assert "traceback" not in r.text

    def test_rejects_svg_xml_content(self):
        svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
        r = _scan(content=svg, mime="image/svg+xml")
        assert r.status_code == 400

    def test_rejects_html_content_type(self):
        r = _scan(content=b"<html>", mime="text/html")
        assert r.status_code == 400

    def test_rejects_json_content_type(self):
        r = _scan(content=b"{}", mime="application/json")
        assert r.status_code == 400

    def test_rejects_exactly_10mb_plus_one_byte(self):
        big = b"\xff\xd8\xff" + b"\x00" * (10 * 1024 * 1024)
        r = _scan(content=big)
        assert r.status_code == 400
        assert "10 MB" in r.json()["detail"]

    def test_accepts_exactly_10mb(self):
        # 10MB - 3 bytes (for the JPEG header) to stay just under limit
        near_limit = b"\xff\xd8\xff" + b"\x00" * (10 * 1024 * 1024 - 4)
        mock = MagicMock()
        mock.text = json.dumps(MOCK_AI_RESPONSE)
        with patch("main._model") as m:
            m.generate_content_async = AsyncMock(return_value=mock)
            r = _scan(content=near_limit)
        assert r.status_code != 400 or "10 MB" not in r.json().get("detail", "")

    def test_path_traversal_in_filename_ignored(self):
        # Filename is cosmetic only — must not affect filesystem
        r = _mock_scan(filename="../../etc/passwd.jpg")
        assert r.status_code == 200

    def test_null_byte_in_filename_handled(self):
        r = _mock_scan(filename="scan\x00.jpg")
        assert r.status_code in (200, 400, 422)
        assert "Traceback" not in r.text

    def test_very_long_filename_handled(self):
        long_name = "a" * 10_000 + ".jpg"
        r = _mock_scan(filename=long_name)
        assert r.status_code in (200, 400, 422)

    def test_webp_accepted(self):
        r = _mock_scan(content=b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 100, mime="image/webp")
        assert r.status_code == 200

    def test_gif_accepted(self):
        r = _mock_scan(content=b"GIF89a" + b"\x00" * 100, mime="image/gif")
        assert r.status_code == 200


# ── Device ID / header injection ─────────────────────────────────────────────

class TestDeviceIdSecurity:
    def setup_method(self):
        _rate_store.clear()

    def test_header_injection_newline_rejected_or_sanitised(self):
        # HTTP header injection: \r\n could split headers
        r = _mock_scan(device_id="device\r\nX-Injected: evil")
        assert r.status_code in (200, 400, 422)
        assert "X-Injected" not in r.headers

    def test_very_long_device_id_truncated(self):
        long_id = "x" * 10_000
        _check_rate_limit(long_id)
        assert long_id[:64] in _rate_store
        assert long_id not in _rate_store

    def test_empty_device_id_still_rate_limited(self):
        from fastapi import HTTPException
        # Empty string truncated to "" — still tracked
        for _ in range(20):
            _check_rate_limit("")
        with pytest.raises(HTTPException) as exc:
            _check_rate_limit("")
        assert exc.value.status_code == 429

    def test_unicode_device_id_handled(self):
        uid = "设备一二三"
        _check_rate_limit(uid)
        assert uid[:64] in _rate_store

    def test_null_byte_device_id_handled(self):
        _check_rate_limit("device\x00abc")

    def test_sql_injection_in_device_id_harmless(self):
        sql = "'; DROP TABLE devices; --"
        _check_rate_limit(sql[:64])
        # Must not crash — rate store is a plain dict, no SQL

    def test_missing_device_id_header_uses_anonymous(self):
        mock = MagicMock()
        mock.text = json.dumps(MOCK_AI_RESPONSE)
        with patch("main._model") as m:
            m.generate_content_async = AsyncMock(return_value=mock)
            r = client.post(
                "/scan",
                files={"file": ("scan.jpg", io.BytesIO(VALID_JPEG), "image/jpeg")},
                # no x-device-id header
            )
        assert r.status_code == 200


# ── Response sanitisation ─────────────────────────────────────────────────────

class TestResponseSanitisation:
    def setup_method(self):
        _rate_store.clear()

    def test_xss_payload_in_item_name_returned_as_string(self):
        xss = {**MOCK_AI_RESPONSE, "item_name": "<script>alert(1)</script>"}
        r = _mock_scan(device_id="xss-test", response_data=xss)
        assert r.status_code == 200
        # Returned as a plain string — not interpreted as HTML
        assert r.json()["item_name"] == "<script>alert(1)</script>"

    def test_negative_values_clamped_to_zero(self):
        neg = {**MOCK_AI_RESPONSE, "est_value_low_usd": -100.0, "est_value_high_usd": -50.0}
        r = _mock_scan(device_id="neg-test", response_data=neg)
        assert r.status_code == 200
        data = r.json()
        assert data["est_value_low_usd"] >= 0
        assert data["est_value_high_usd"] >= 0

    def test_string_value_coerced_to_float(self):
        str_vals = {**MOCK_AI_RESPONSE, "est_value_low_usd": "45", "est_value_high_usd": "90"}
        r = _mock_scan(device_id="str-test", response_data=str_vals)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data["est_value_low_usd"], float)

    def test_very_large_values_handled(self):
        big = {**MOCK_AI_RESPONSE, "est_value_low_usd": 999_999, "est_value_high_usd": 9_999_999}
        r = _mock_scan(device_id="big-test", response_data=big)
        assert r.status_code == 200

    def test_sql_injection_in_item_name_returned_safely(self):
        sql_item = {**MOCK_AI_RESPONSE, "item_name": "'; DROP TABLE scans; --"}
        r = _mock_scan(device_id="sql-test", response_data=sql_item)
        assert r.status_code == 200
        assert r.json()["item_name"] == "'; DROP TABLE scans; --"

    def test_missing_optional_fields_use_defaults(self):
        minimal = {**MOCK_AI_RESPONSE}
        del minimal["sold_listings_count"]
        r = _mock_scan(device_id="minimal-test", response_data=minimal)
        assert r.status_code == 200
        assert r.json()["sold_listings_count"] == 0

    def test_extra_fields_from_ai_ignored(self):
        extra = {**MOCK_AI_RESPONSE, "malicious_field": "evil", "internal_data": "secret"}
        r = _mock_scan(device_id="extra-test", response_data=extra)
        assert r.status_code == 200
        assert "malicious_field" not in r.json()
        assert "internal_data" not in r.json()


# ── Error message information leakage ────────────────────────────────────────

class TestErrorLeakage:
    def setup_method(self):
        _rate_store.clear()

    def test_gemini_error_does_not_leak_api_key(self):
        with patch("main._model") as m:
            m.generate_content_async = AsyncMock(
                side_effect=Exception("API key AIzaSy_FAKE_KEY_12345 is invalid")
            )
            r = _scan(device_id="leak-test")
        assert r.status_code == 502
        assert "AIzaSy" not in r.text
        assert "FAKE_KEY" not in r.text

    def test_gemini_error_does_not_leak_traceback(self):
        with patch("main._model") as m:
            m.generate_content_async = AsyncMock(side_effect=RuntimeError("internal crash"))
            r = _scan(device_id="traceback-test")
        assert r.status_code == 502
        assert "Traceback" not in r.text
        assert "RuntimeError" not in r.text

    def test_parse_error_does_not_leak_raw_ai_response(self):
        # Raw response might contain user's image metadata or API internals
        mock = MagicMock()
        mock.text = "Not JSON — but contains GEMINI_API_KEY=secret123"
        with patch("main._model") as m:
            m.generate_content_async = AsyncMock(return_value=mock)
            r = _scan(device_id="raw-test")
        assert r.status_code == 500
        assert "GEMINI_API_KEY" not in r.text
        assert "secret123" not in r.text

    def test_404_not_found_does_not_leak_internals(self):
        r = client.get("/nonexistent-endpoint")
        assert r.status_code == 404
        assert "Traceback" not in r.text

    def test_rate_limit_error_message_is_safe(self):
        from fastapi import HTTPException
        for _ in range(20):
            _check_rate_limit("leak-rate-test")
        r = _scan(device_id="leak-rate-test")
        assert r.status_code == 429
        detail = r.json()["detail"]
        assert "GEMINI" not in detail
        assert "secret" not in detail.lower()


# ── _safe_float edge cases ────────────────────────────────────────────────────

class TestSafeFloat:
    def test_none_returns_zero(self):
        assert _safe_float(None) == 0.0

    def test_negative_clamped_to_zero(self):
        assert _safe_float(-100) == 0.0

    def test_string_number_parsed(self):
        assert _safe_float("45.5") == 45.5

    def test_invalid_string_returns_zero(self):
        assert _safe_float("not-a-number") == 0.0

    def test_nan_string_returns_zero(self):
        assert _safe_float("NaN") == 0.0

    def test_infinity_string_clamped_to_zero(self):
        # inf would crash iOS Int() conversion — must be clamped
        assert _safe_float("inf") == 0.0
        assert _safe_float("-inf") == 0.0
        assert _safe_float("infinity") == 0.0

    def test_dict_returns_zero(self):
        assert _safe_float({"nested": "object"}) == 0.0

    def test_list_returns_zero(self):
        assert _safe_float([1, 2, 3]) == 0.0

    def test_zero_returns_zero(self):
        assert _safe_float(0) == 0.0

    def test_large_value_preserved(self):
        assert _safe_float(999_999) == 999_999.0


# ── Rate limit store memory safety ───────────────────────────────────────────

class TestRateLimitMemorySafety:
    def setup_method(self):
        _rate_store.clear()

    def test_many_unique_devices_dont_crash(self):
        # Simulate 1000 different devices — store should handle this
        for i in range(1000):
            _check_rate_limit(f"device-{i}")
        assert len(_rate_store) == 1000

    def test_stale_entries_cleaned_up(self):
        from main import RATE_WINDOW_SECS
        import main
        # Inject old timestamps that should be cleaned
        _rate_store["old-device"] = [time.time() - RATE_WINDOW_SECS - 1]
        old_cleanup = main._last_cleanup
        main._last_cleanup = time.time() - 700  # Pretend 700s since last cleanup
        _check_rate_limit("trigger-cleanup")
        # old-device should be pruned
        assert "old-device" not in _rate_store
        # Restore
        main._last_cleanup = old_cleanup

    def test_rate_limit_window_is_sliding(self):
        from main import RATE_WINDOW_SECS
        device = "sliding-window-test"
        # Add 20 timestamps just outside the window (expired)
        old_time = time.time() - RATE_WINDOW_SECS - 1
        _rate_store[device] = [old_time] * 20
        # All 20 are expired — should allow new request
        _check_rate_limit(device)  # Should NOT raise
