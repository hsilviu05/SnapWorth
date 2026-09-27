"""Every fixture in `contract/` is a response this server actually sends.

`contract/README.md` says what each file is for. These tests serve each case
through the real app and compare the result with its fixture: every key, and
the JSON type of every value.

The fixture used to be written by hand. It carried a `model` field the server
never sends, lacked six it does, covered only the Pro body, and was compared
with the server on the nine v1 fields alone — so changing a v2 field's type
(the client decodes those strictly; one mismatch fails the whole scan, after
the allowance was charged) would have passed both suites.

After an intended change — a new optional field, a copy edit — regenerate and
read the diff; a key that disappeared or changed type is a breaking change:

    REGENERATE_CONTRACT=1 pytest tests/test_contract.py
"""

from __future__ import annotations

import io
import json
import os
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import aiconfig
import main
import quota
from tests.conftest import build_deps
from tests.images import padded_image_bytes
from tests.test_ai_pipeline import V2_PAYLOAD, _client, _scan_with
from tests.test_main import _post_listing

CONTRACT = pathlib.Path(__file__).resolve().parents[2] / "contract"
REGENERATE = os.environ.get("REGENERATE_CONTRACT") == "1"


# ── Comparison ───────────────────────────────────────────────────────────────

def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    # Kept apart, deliberately stricter than the client: Swift's JSONDecoder
    # reads 37.0 as an Int and throws only on a truly fractional value such as
    # 37.5. A whole number turning into a float is the step before that.
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def shape_differences(fixture: dict, served: dict, path: str = "") -> list[str]:
    """Every key in one and not the other, and every value whose JSON type
    differs. Array elements are compared by type when both arrays have some;
    nested objects recursively."""
    problems: list[str] = []
    for key in sorted(set(fixture) | set(served)):
        where = f"{path}{key}"
        if key not in served:
            problems.append(f"{where}: in the fixture, not served")
            continue
        if key not in fixture:
            problems.append(f"{where}: served, not in the fixture")
            continue
        want, got = fixture[key], served[key]
        if _json_type(want) != _json_type(got):
            problems.append(f"{where}: fixture {_json_type(want)}, served {_json_type(got)}")
        elif isinstance(want, list) and want and got:
            want_types = sorted({_json_type(v) for v in want})
            got_types = sorted({_json_type(v) for v in got})
            if want_types != got_types:
                problems.append(f"{where}[]: fixture {want_types}, served {got_types}")
            elif want_types == ["object"]:
                problems += shape_differences(want[0], got[0], f"{where}[].")
        elif isinstance(want, dict):
            problems += shape_differences(want, got, f"{where}.")
    return problems


def _check(name: str, served: dict) -> dict:
    """Compare `served` with `contract/<name>`, or rewrite it when regenerating."""
    path = CONTRACT / name
    if REGENERATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(served, indent=2, ensure_ascii=False) + "\n")
    fixture = json.loads(path.read_text())
    problems = shape_differences(fixture, served)
    assert not problems, (
        f"contract/{name} no longer matches what the server sends:\n  "
        + "\n  ".join(problems)
        + "\nA removed key or a changed type breaks every installed client. "
          "If the change is additive, regenerate: see this module's docstring.")
    return fixture


def _error(response, *headers: str) -> dict:
    """An error response as its fixture records it: status, the headers the
    server sends that a client may rely on, and the body. (The app reads
    `Retry-After`; it does not read `X-Quota-Resets-At` yet.)"""
    return {
        "status": response.status_code,
        "headers": {h: response.headers[h] for h in headers},
        "body": response.json(),
    }


def _check_error(name: str, served: dict) -> dict:
    fixture = _check(name, served)
    assert served["status"] == fixture["status"]
    for header, value in served["headers"].items():
        # Seconds or an epoch: always a plain non-negative integer.
        assert value.isdigit(), f"{header}: {value!r} is not a number"
    return fixture


def _post_scan(device_id: str, payload: dict = V2_PAYLOAD):
    """One /scan, without `_scan_with`'s rate-limit reset between calls."""
    reply = MagicMock()
    reply.text = json.dumps(payload)
    with patch("main._model") as model:
        model.generate_content_async = AsyncMock(return_value=reply)
        return _client.post(
            "/scan", headers={"x-device-id": device_id},
            files={"file": ("s.jpg", io.BytesIO(padded_image_bytes("JPEG", 1024)),
                            "image/jpeg")})


# ── The 200 bodies ───────────────────────────────────────────────────────────

class TestSuccessBodies:
    def test_the_pro_body(self):
        """`scan-response.json`: the full v2 payload. What the iOS decode
        test and the "Why this price" panel are checked against."""
        r = _scan_with(V2_PAYLOAD, pro=True)
        assert r.status_code == 200
        fixture = _check("scan-response.json", r.json())
        # Pro has no count; the client falls back to its own (I-3).
        assert fixture["free_scans_remaining"] is None

    def test_the_free_body(self):
        """The common case. Most of the detail is blanked (`_strip_pro_detail`)
        but no key is removed. Served under the production allowance, so
        `free_scans_remaining` is what a real free user sees after the day's
        scan."""
        build_deps(free_scans=quota.FREE_SCANS_PER_DAY)
        r = _scan_with(V2_PAYLOAD)
        assert r.status_code == 200
        fixture = _check("scan-response-free.json", r.json())
        assert fixture["free_scans_remaining"] == quota.FREE_SCANS_PER_DAY - 1

    def test_free_and_pro_carry_the_same_keys(self):
        """The Pro split blanks values; it never drops a key, so one decoder
        serves both tiers."""
        pro = json.loads((CONTRACT / "scan-response.json").read_text())
        free = json.loads((CONTRACT / "scan-response-free.json").read_text())
        assert set(pro) == set(free)

    def test_a_type_change_is_caught(self):
        """The failure this file exists for: `confidence_reasons` gaining codes
        as objects, or a score turning fractional, passes a keys-only check."""
        served = _scan_with(V2_PAYLOAD, pro=True).json()
        fixture = json.loads((CONTRACT / "scan-response.json").read_text())
        # A truly fractional score: the client reads 37.0 as an Int, not 37.5.
        changed = {**served,
                   "confidence_reasons": [{"code": "soft_photo", "text": "soft"}],
                   "confidence_score": served["confidence_score"] + 0.5}
        problems = shape_differences(fixture, changed)
        assert any(p.startswith("confidence_reasons[]") for p in problems)
        assert any(p.startswith("confidence_score") for p in problems)


# ── The error bodies ─────────────────────────────────────────────────────────
#
# The client reads `detail` (and FastAPI's validation list), `Retry-After` on a
# 429, and routes a 402 to the paywall by its wording — `AppError.from` tests
# whether `detail` contains "pro feature" to tell "Pro only" from "allowance
# spent". So for the 402s the wording is contract too.

class TestErrorBodies:
    def test_the_quota_402(self):
        build_deps(free_scans=quota.FREE_SCANS_PER_DAY)
        assert _scan_with(V2_PAYLOAD).status_code == 200
        r = _scan_with(V2_PAYLOAD)
        assert r.status_code == 402
        fixture = _check_error("errors/scan-402-quota.json",
                               _error(r, "X-Quota-Resets-At"))
        assert "pro feature" not in fixture["body"]["detail"].lower(), (
            "the client would route a spent allowance to the Pro-only message")

    def test_the_listing_402(self):
        r = _post_listing(pro=False)
        assert r.status_code == 402
        fixture = _check_error("errors/listing-402-pro.json", _error(r))
        assert "pro feature" in fixture["body"]["detail"].lower(), (
            "the client tells this 402 from a spent allowance by these words")

    def test_the_unusable_photo_422(self):
        with patch("main._generate_with_retry",
                   AsyncMock(side_effect=aiconfig.ModelBlocked("SAFETY"))):
            r = _scan_with(V2_PAYLOAD)
        assert r.status_code == 422
        _check_error("errors/scan-422-unusable-photo.json", _error(r))

    def test_the_rate_limit_429(self):
        # The real per-device limit, so the wording is production's.
        main._rate_store.clear()
        main._ip_rate_store.clear()
        for _ in range(main.RATE_MAX_REQUESTS):
            assert _post_scan("contract-429").status_code == 200
        r = _post_scan("contract-429")
        assert r.status_code == 429
        _check_error("errors/scan-429-rate-limited.json", _error(r, "Retry-After"))


def test_every_fixture_is_checked_here():
    """A fixture nothing regenerates or compares is a hand-written one again."""
    source = pathlib.Path(__file__).read_text()
    for path in CONTRACT.rglob("*.json"):
        name = str(path.relative_to(CONTRACT))
        assert f'"{name}"' in source, f"contract/{name} is not checked against the server"
