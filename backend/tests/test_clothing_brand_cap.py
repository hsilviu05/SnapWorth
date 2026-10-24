"""A garment's brand named without a label photo stays below High.

From the second-hand clothing eval (2026-10-23, 261 garments, front photo
only): every High answer named a brand, and three in four of those brands were
wrong — invented, with tag readings to match. See
`confidence.UNLABELLED_CLOTHING_BRAND_CEILING`.
"""

from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scan import confidence
from tests.test_ai_pipeline import V2_PAYLOAD, _client, _img, _pro_headers
from main import _ip_rate_store, _rate_store


STRONG: dict[str, Any] = dict(
    brand="Patagonia", category="clothing", identification_certainty="certain",
    authenticity="no_concerns", demand="high", supply="moderate",
    value_low=40, value_high=60)


class TestTheCap:
    def test_it_would_be_high_with_a_label_photo(self):
        assert confidence.compute(**STRONG, label_photo=True).band == "High"

    def test_without_one_it_is_medium_and_says_why(self):
        result = confidence.compute(**STRONG, label_photo=False)
        assert result.score == confidence.UNLABELLED_CLOTHING_BRAND_CEILING
        assert result.band == "Medium"
        assert result.reasons[0] == confidence.UNLABELLED_BRAND_REASON
        assert result.reason_codes[0] == confidence.UNLABELLED_BRAND_CODE

    def test_a_caller_that_does_not_say_is_not_capped(self):
        assert confidence.compute(**STRONG).band == "High"

    @pytest.mark.parametrize("category", ["shoes", "bags", "electronics"])
    def test_only_clothing_is_capped(self, category):
        """Clothing is what was measured; a logo on a shoe or a phone is not
        known to be guessed the same way."""
        result = confidence.compute(**{**STRONG, "category": category}, label_photo=False)
        assert confidence.UNLABELLED_BRAND_CODE not in {s.code for s in result.signals}

    def test_no_brand_is_nothing_to_cap(self):
        result = confidence.compute(**{**STRONG, "brand": "Unknown"}, label_photo=False)
        assert confidence.UNLABELLED_BRAND_CODE not in {s.code for s in result.signals}

    def test_a_score_already_below_high_gets_no_extra_reason(self):
        """Another cap bound first; this one did not lower the badge, so it is
        not the reason given for it."""
        result = confidence.compute(**{**STRONG, "identification_certainty": "uncertain"},
                                    label_photo=False)
        assert result.band != "High"
        assert confidence.UNLABELLED_BRAND_CODE not in result.reason_codes

    def test_the_lower_caps_still_bind(self):
        result = confidence.compute(**STRONG, label_photo=False, was_clamped=True)
        assert result.score <= 30


def _label_photo_passed(*, tag: bool, pro: bool = True):
    """What `/scan` told `confidence.compute` about the label photo."""
    _rate_store.clear()
    _ip_rate_store.clear()
    mock = MagicMock()
    mock.text = json.dumps(V2_PAYLOAD)
    files = {"file": ("s.jpg", io.BytesIO(_img("JPEG")), "image/jpeg")}
    if tag:
        files["tag"] = ("t.jpg", io.BytesIO(_img("JPEG")), "image/jpeg")
    headers = {"x-device-id": "brand-cap"}
    if pro:
        headers |= _pro_headers("brand-cap-pro")
    with patch("main._model") as m, \
         patch("main.confidence_module.compute", wraps=confidence.compute) as spy:
        m.generate_content_async = AsyncMock(return_value=mock)
        assert _client.post("/scan", files=files, headers=headers).status_code == 200
    return spy.call_args.kwargs["label_photo"]


class TestServed:
    def test_a_scan_of_the_item_alone_says_no_label_photo(self):
        assert _label_photo_passed(tag=False) is False

    def test_a_pro_scan_with_its_label_photo_says_so(self):
        assert _label_photo_passed(tag=True) is True

    def test_a_free_callers_label_photo_is_not_counted(self):
        """The server ignores a tag photo from a free caller, so the model
        never saw it."""
        assert _label_photo_passed(tag=True, pro=False) is False
