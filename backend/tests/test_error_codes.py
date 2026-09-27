"""Every error body from /scan, /listing, /trends and /auth carries a `code`.

The client used to have only `detail`, an English sentence: it showed it as
written in an app translated into four other languages, and told the two 402s
apart by whether the sentence contained "pro feature". A code is what it can
translate and route on instead. `contract/` pins the bodies the client decodes;
these cover the rest of the paths, so a new `raise` without a code — which
would still get a generic one from its status — shows up as the wrong code
here rather than as an English sentence on a Romanian phone.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import aiconfig
import apierrors
import auth
import confidence
import imagevalidation
import main
from imagequality import ImageQuality
from tests.conftest import build_deps
from tests.images import VALID_JPEG, padded_image_bytes
from tests.test_ai_pipeline import V2_PAYLOAD, _pro_headers, _scan_with
from tests.test_main import _listing_body

client = TestClient(main.app)

_TOKEN = re.compile(r"[a-z]+(?:_[a-z]+)*")


@pytest.fixture(autouse=True)
def _fresh_limits():
    main._rate_store.clear()
    main._ip_rate_store.clear()


def _code(response) -> str:
    return response.json()["code"]


def _scan(image: bytes = VALID_JPEG, content_type: str = "image/jpeg", **headers):
    return client.post("/scan", headers={"x-device-id": "codes"} | headers,
                       files={"file": ("s.jpg", io.BytesIO(image), content_type)})


class TestScanErrors:
    def test_a_file_that_is_not_an_image(self):
        r = _scan(b"not an image at all, just text")
        assert r.status_code == 400
        assert _code(r) == apierrors.IMAGE_UNREADABLE

    def test_an_unsupported_declared_type(self):
        r = _scan(VALID_JPEG, "application/pdf")
        assert r.status_code == 400
        assert _code(r) == apierrors.IMAGE_TYPE_UNSUPPORTED

    def test_an_upload_past_the_cap(self):
        # Driven directly with a small cap, rather than by sending 10 MB.
        upload = MagicMock()
        upload.read = AsyncMock(side_effect=[b"x" * 2048, b""])
        with pytest.raises(apierrors.APIError) as exc:
            asyncio.run(main._read_capped(upload, limit=1024))
        assert exc.value.status_code == 400
        assert exc.value.code == apierrors.IMAGE_TOO_LARGE

    def test_no_file_at_all_is_fastapis_list_with_a_code(self):
        r = client.post("/scan", headers={"x-device-id": "codes"})
        assert r.status_code == 422
        body = r.json()
        assert body["code"] == apierrors.INVALID_REQUEST
        assert isinstance(body["detail"], list), (
            "`detail` stays the list every build already parses")

    def test_an_unreadable_model_reply(self):
        reply = MagicMock()
        reply.text = "not json"
        with patch("main._model") as model, \
                patch("main._retry_as_json", AsyncMock(return_value=None)):
            model.generate_content_async = AsyncMock(return_value=reply)
            r = _scan(padded_image_bytes("JPEG", 1024))
        assert r.status_code == 502
        assert _code(r) == apierrors.AI_UNREADABLE

    def test_a_reply_with_no_price(self):
        truncated = {k: v for k, v in V2_PAYLOAD.items() if not k.endswith("_usd")}
        r = _scan_with(truncated)
        assert r.status_code == 502
        assert _code(r) == apierrors.AI_NO_PRICE

    def test_a_paused_device(self, monkeypatch):
        monkeypatch.setattr(main, "_safety_block_count",
                            AsyncMock(return_value=main.SAFETY_BLOCKS_BEFORE_PAUSE))
        r = _scan(padded_image_bytes("JPEG", 1024))
        assert r.status_code == 422
        assert _code(r) == apierrors.DEVICE_PAUSED

    def test_a_quota_store_that_cannot_be_read(self, monkeypatch):
        from quota import QuotaUnavailable
        monkeypatch.setattr(auth.deps.quota, "reserve",
                            AsyncMock(side_effect=QuotaUnavailable("down")))
        r = _scan(padded_image_bytes("JPEG", 1024))
        assert r.status_code == 503
        assert _code(r) == apierrors.QUOTA_UNAVAILABLE


class TestListingAndTrendsErrors:
    def test_an_unknown_marketplace(self):
        r = client.post("/listing", json=_listing_body(marketplace="etsy"),
                        headers={"x-device-id": "codes"} | _pro_headers("codes-pro"))
        assert r.status_code == 400
        assert _code(r) == apierrors.UNSUPPORTED_MARKETPLACE

    def test_a_listing_model_outage(self):
        with patch("main._generate_with_retry",
                   AsyncMock(side_effect=aiconfig.ModelUnavailable("down"))):
            r = client.post("/listing", json=_listing_body(),
                            headers={"x-device-id": "codes"} | _pro_headers("codes-pro"))
        assert r.status_code == 502
        assert _code(r) == apierrors.AI_UNAVAILABLE

    def test_trends_without_a_token_when_one_is_required(self):
        build_deps(enforce=True)
        r = client.get("/trends")
        assert r.status_code == 401
        assert _code(r) == apierrors.AUTH_REQUIRED
        assert r.headers["WWW-Authenticate"] == "Bearer", "headers survive the handler"


class TestAuthErrors:
    def test_a_token_that_does_not_verify(self):
        build_deps(enforce=True)
        r = client.get("/trends", headers={"Authorization": "Bearer not-a-token"})
        assert r.status_code == 401
        assert _code(r) == apierrors.TOKEN_INVALID

    def test_an_unknown_challenge(self):
        r = client.post("/auth/refresh", json={
            "challenge": "never-issued", "key_id": "AAAA", "assertion": "AAAA"})
        assert r.status_code == 400
        assert _code(r) == apierrors.CHALLENGE_INVALID

    def test_a_malformed_assertion(self):
        challenge = client.post("/auth/challenge").json()["challenge"]
        r = client.post("/auth/refresh", json={
            "challenge": challenge, "key_id": "not base64!", "assertion": "AAAA"})
        assert r.status_code == 400
        assert _code(r) == apierrors.ASSERTION_MALFORMED

    def test_an_unknown_key(self):
        challenge = client.post("/auth/challenge").json()["challenge"]
        r = client.post("/auth/refresh", json={
            "challenge": challenge, "key_id": "AAAA", "assertion": "AAAA"})
        assert r.status_code == 401
        assert _code(r) == apierrors.KEY_UNKNOWN

    def test_a_rejected_transaction(self):
        r = client.post("/auth/entitlement",
                        json={"signed_transaction": "a.b.c"},
                        headers={"x-device-id": "codes"})
        assert r.status_code == 400, r.text
        assert _code(r) == apierrors.ENTITLEMENT_REJECTED


class TestEveryBodyHasOne:
    def test_the_routers_own_404_gets_a_generic_code(self):
        r = client.get("/no-such-route")
        assert r.status_code == 404
        assert r.json() == {"detail": "Not Found", "code": "not_found"}

    def test_a_body_refused_before_it_is_read(self):
        r = client.post("/listing", content=b"{}",
                        headers={"content-length": str(main.MAX_JSON_BODY_BYTES + 1),
                                 "content-type": "application/json"})
        assert r.status_code == 413
        assert _code(r) == apierrors.PAYLOAD_TOO_LARGE

    def test_a_malformed_content_length(self):
        r = client.post("/listing", content=b"{}",
                        headers={"content-length": "lots",
                                 "content-type": "application/json"})
        assert r.status_code == 400
        assert _code(r) == apierrors.BAD_CONTENT_LENGTH

    def test_every_specific_code_is_a_snake_case_token(self):
        codes = [v for k, v in vars(apierrors).items()
                 if k.isupper() and isinstance(v, str)]
        assert codes
        for code in codes:
            assert _TOKEN.fullmatch(code), code

    def test_every_image_validation_error_has_a_code(self):
        for data, declared in [(b"", "image/jpeg"), (VALID_JPEG, "text/plain"),
                               (b"x" * 64, "image/jpeg")]:
            with pytest.raises(imagevalidation.ImageValidationError) as exc:
                imagevalidation.validate(data, declared)
            assert exc.value.code.startswith("image_"), (data[:8], exc.value.code)


class TestConfidenceReasonCodes:
    """`confidence_reason_codes`: one token per reason, so the client can word
    the reasons in the Pro panel in its own language."""

    CASES = [
        dict(brand="Patagonia", category="clothing", identification_certainty="certain",
             authenticity="no_concerns", demand="high", supply="scarce",
             value_low=40, value_high=60),
        dict(brand="Unknown", category="collectibles", identification_certainty="uncertain",
             authenticity="likely_replica", demand=None, supply=None,
             value_low=5, value_high=200, was_clamped=True,
             image_quality=ImageQuality(sharpness=0.1, exposure=0.2, detail=0.2,
                                        contrast=0.1)),
        dict(brand="Levi's", category="other", identification_certainty="probable",
             authenticity="cannot_verify", demand="low", supply=None,
             value_low=10, value_high=30, range_synthesised=True,
             model_field_count=2, expected_field_count=10),
    ]

    @pytest.mark.parametrize("case", CASES)
    def test_one_code_per_reason(self, case):
        result = confidence.compute(**case)
        assert result.reasons
        assert len(result.reason_codes) == len(result.reasons)
        for code in result.reason_codes:
            assert _TOKEN.fullmatch(code), code

    def test_a_code_always_means_the_same_words(self):
        """Except the category's two, which name the category: the client's
        wording for those leaves the name out."""
        words: dict[str, set[str]] = {}
        for case in self.CASES:
            for signal in confidence.compute(**case).signals:
                words.setdefault(signal.code, set()).add(signal.explanation)
        for code, said in words.items():
            if code.startswith("category_"):
                continue
            assert len(said) == 1, (code, said)

    def test_the_replica_verdict_has_its_own_code(self):
        # Everything else strong, so the replica read is the weakest signal.
        result = confidence.compute(**{**self.CASES[0], "authenticity": "likely_replica"})
        at = result.reasons.index(confidence.REPLICA_REASON)
        assert result.reason_codes[at] == confidence.REPLICA_CODE

    def test_the_codes_reach_a_pro_scan_and_not_a_free_one(self):
        pro = _scan_with(V2_PAYLOAD, pro=True).json()
        assert len(pro["confidence_reason_codes"]) == len(pro["confidence_reasons"]) > 0
        free = _scan_with(V2_PAYLOAD).json()
        assert free["confidence_reason_codes"] == [], (
            "the codes are the reasons in another form — `likely_replica` is the "
            "authenticity verdict — so they are Pro detail too")

    def test_every_branch_produces_exactly_the_published_codes(self):
        """`REASON_CODES` is what `contract/` hands the client, whose
        `ConfidenceReason` must word each one. Every branch of `compute` is
        driven here, so a new code fails this until it is published."""
        blurry = ImageQuality(sharpness=0.1, exposure=0.1, detail=0.1, contrast=0.1)
        base = dict(brand="Patagonia", category="clothing", identification_certainty="certain",
                    authenticity="no_concerns", demand="high", supply="scarce",
                    value_low=40, value_high=60, model_field_count=10,
                    expected_field_count=10,
                    image_quality=ImageQuality(sharpness=0.9, exposure=0.9, detail=0.9,
                                               contrast=0.9))
        variants = [
            {}, {"brand": "Unknown"},
            {"value_low": 0}, {"value_high": 400}, {"value_high": 120},
            {"range_synthesised": True},
            {"image_quality": blurry},
            {"image_quality": ImageQuality(sharpness=0.9, exposure=0.1)},
            {"image_quality": ImageQuality(sharpness=0.9, detail=0.1)},
            {"image_quality": ImageQuality(sharpness=0.9, contrast=0.1)},
            {"category": "furniture"}, {"category": "made-up"},
            {"identification_certainty": "probable"},
            {"identification_certainty": "uncertain"},
            {"authenticity": "cannot_verify"}, {"authenticity": "likely_replica"},
            {"demand": None}, {"model_field_count": 1},
            {"was_clamped": True},
        ]
        produced: set[str] = set()
        for variant in variants:
            produced |= {s.code for s in confidence.compute(**(base | variant)).signals}
        assert produced == confidence.REASON_CODES, (
            f"produced but unpublished: {produced - confidence.REASON_CODES}; "
            f"published but never produced: {confidence.REASON_CODES - produced}")

    def test_image_issues_keep_their_order_and_words(self):
        quality = ImageQuality(sharpness=0.1, exposure=0.1, detail=0.1, contrast=0.1)
        assert [text for _, text in quality.coded_issues()] == quality.issues()
        assert len({code for code, _ in quality.coded_issues()}) == 4


def test_nothing_here_sends_an_error_without_detail():
    """`code` is beside `detail`, never instead of it: installed builds read
    only `detail`."""
    r = client.get("/no-such-route")
    assert list(json.loads(r.text)) == ["detail", "code"]
