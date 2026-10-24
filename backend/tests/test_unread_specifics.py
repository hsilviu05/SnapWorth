"""A size, style number or brand the photo does not show, and footwear filed as clothing.

From a customer's scans on 2026-10-20: one pair of sandals came back as sizes
7.5 and 8, a pair of boots as 9 and 6 with style numbers like "EMI-7857" no
photo showed, and two of the boots under "clothing", every one of them High.
The brand from the second-hand clothing eval (2026-10-23): L.L.Bean for a
Lindex sweater with no brand legible, under a High badge.
"""

from __future__ import annotations

from typing import Any

import pytest

from scan import confidence
from scan import valuation
from tests.test_ai_pipeline import V2_PAYLOAD
from tests.test_prompt_v21 import _scan


def _val(**fields):
    return valuation.normalise({"brand": "Myra", "category": "shoes",
                                "worst_case_price_usd": 10, "best_case_price_usd": 30,
                                **fields})


class TestSize:
    def test_a_size_the_evidence_does_not_show_is_dropped(self):
        v = _val(item_name="Roper Embossed Strap Block Heel Mules, Size 8", size="8",
                 listing_title="Roper Block Heel Mules Size 8",
                 visual_evidence=["Roper logo on the footbed"])
        assert v.size is None
        assert v.item_name == "Roper Embossed Strap Block Heel Mules"
        assert v.listing_title == "Roper Block Heel Mules"
        assert v.unread_specifics

    @pytest.mark.parametrize("name, size", [
        ("Roper Sandals, Size USA 7.5", "USA 7.5"),
        ("Myra Boots (Size US 9)", "US 9"),
        ("Roper Size 8 Women's Mules", "8"),
    ])
    def test_the_size_mention_goes_however_it_is_written(self, name, size):
        v = _val(item_name=name, size=size, visual_evidence=["logo on insole"])
        assert "size" not in v.item_name.lower()

    def test_a_size_the_evidence_reads_is_kept(self):
        v = _val(item_name="Myra Ankle Boots, Size US 9", size="US 9",
                 visual_evidence=["Myra logo on the insole", "insole stamp reads 9"])
        assert v.size == "US 9"
        assert v.item_name == "Myra Ankle Boots, Size US 9"
        assert not v.unread_specifics

    def test_only_the_dropped_size_leaves_the_name(self):
        v = _val(item_name="Kids Size 4 Tee, Size M", size="M",
                 visual_evidence=["Gap logo"])
        assert v.item_name == "Kids Size 4 Tee"


class TestStyleNumber:
    def test_a_style_number_the_evidence_does_not_show_is_dropped(self):
        v = _val(item_name="Myra EMI-7857 Embossed Ankle Strap Sandals",
                 model="EMI-7857", listing_title="Myra EMI-7857 Sandals",
                 visual_evidence=["Myra logo on the insole", "tooled floral leather"])
        assert v.item_name == "Myra Embossed Ankle Strap Sandals"
        assert v.listing_title == "Myra Sandals"
        assert v.model is None
        assert v.unread_specifics

    def test_a_style_number_the_evidence_reads_is_kept(self):
        v = _val(item_name="Myra S-0279 Ankle Boots", model="S-0279",
                 visual_evidence=["box label reads Myra S-0279"])
        assert v.model == "S-0279"
        assert "S-0279" in v.item_name
        assert not v.unread_specifics

    @pytest.mark.parametrize("name", [
        "Patagonia Better Sweater 1/4-Zip",   # one digit, not a number to read
        "Levi's 501 Jeans, 2015-2020",        # no letters in it
    ])
    def test_hyphenated_words_that_are_not_style_numbers_stay(self, name):
        v = _val(item_name=name, visual_evidence=["Myra wordmark on chest"])
        assert v.item_name == name
        assert not v.unread_specifics


class TestBrand:
    def test_a_brand_the_evidence_does_not_name_is_dropped(self):
        v = _val(brand="L.L.Bean", category="clothing",
                 item_name="L.L.Bean Women's Cable Knit Sweater, Navy",
                 listing_title="L.L.Bean Cable Knit Sweater Navy Wool",
                 visual_evidence=["cable-knit pattern on front", "ribbed cuffs and hem"])
        assert v.brand == "Unknown"
        assert v.item_name == "Women's Cable Knit Sweater, Navy"
        assert v.listing_title == "Cable Knit Sweater Navy Wool"
        assert v.unread_specifics

    @pytest.mark.parametrize("brand, evidence", [
        ("Vero Moda", "neck label reads VERO MODA"),
        ("H&M", "H&M care label"),
        ("Levi's", "Levi's red tab on back pocket"),
        ("The North Face", "North Face logo on left chest"),
        ("Ellie & Mae", "label reads ELLIE AND MAE"),
        ("Polarn O. Pyret", "Polarn O. Pyret label at neck"),
    ])
    def test_a_brand_the_evidence_names_is_kept(self, brand, evidence):
        v = _val(brand=brand, item_name=f"{brand} Top", visual_evidence=[evidence])
        assert v.brand == brand
        assert v.item_name == f"{brand} Top"
        assert not v.unread_specifics

    def test_every_word_of_the_brand_has_to_be_there(self):
        """"Navy" in a colour is not "Old Navy"."""
        v = _val(brand="Old Navy", item_name="Old Navy Cardigan",
                 visual_evidence=["navy blue knit cardigan"])
        assert v.brand == "Unknown"
        assert v.item_name == "Cardigan"

    def test_a_possessive_goes_with_the_brand(self):
        v = _val(brand="Tommy Hilfiger", item_name="Tommy Hilfiger's Check Shirt",
                 visual_evidence=["red and navy check"])
        assert v.item_name == "Check Shirt"

    def test_a_name_that_would_be_empty_is_kept(self):
        v = _val(brand="Gap", item_name="Gap", visual_evidence=["grey hoodie"])
        assert v.brand == "Unknown"
        assert v.item_name == "Gap"

    @pytest.mark.parametrize("brand", ["Unknown", "Unbranded", "Generic", ""])
    def test_no_brand_is_nothing_to_check(self, brand):
        v = _val(brand=brand, item_name="Black Wool Coat", visual_evidence=["wool coat"])
        assert v.item_name == "Black Wool Coat"
        assert not v.unread_specifics

    def test_a_brand_inside_another_word_stays(self):
        v = _val(brand="Gap", item_name="Gapless Seam Tee", visual_evidence=["seams"])
        assert v.item_name == "Gapless Seam Tee"


class TestNothingToCheckAgainst:
    def test_a_reply_without_evidence_is_left_alone(self):
        """v1 asks for no `visual_evidence`; an empty list is no proof of
        anything unread."""
        v = _val(item_name="Myra EMI-7857 Boots, Size 9", size="9")
        assert v.size == "9" and "EMI-7857" in v.item_name
        assert not v.unread_specifics

    def test_the_standard_reply_is_untouched(self):
        v = valuation.normalise(V2_PAYLOAD)
        assert v.size == V2_PAYLOAD["size"]
        assert v.item_name == V2_PAYLOAD["item_name"]
        assert not v.unread_specifics


class TestFootwearCategory:
    @pytest.mark.parametrize("name", [
        "Myra Embossed Open Toe Ankle Boots",
        "Myra Leather Open-Toe Block Heel Sandals",
        "Mvboo Open-Toe Slingback Block Heel Shoes, Brown Embossed",
        "Myra Tooled Open Toe Ankle Booties (Size 6)",
    ])
    def test_footwear_filed_as_clothing_is_shoes(self, name):
        assert _val(item_name=name, category="clothing").category == "shoes"

    @pytest.mark.parametrize("name", [
        "Levi's Boot Cut Jeans", "Wool Boot Socks", "Sandal Print Sundress",
    ])
    def test_clothing_that_mentions_footwear_stays_clothing(self, name):
        assert _val(item_name=name, category="clothing").category == "clothing"

    def test_only_clothing_is_moved(self):
        assert _val(item_name="Boot Tray", category="home").category == "home"


class TestConfidence:
    _STRONG: dict[str, Any] = dict(brand="Myra", category="shoes", identification_certainty="certain",
                   authenticity="no_concerns", demand="high", supply="moderate",
                   value_low=20, value_high=30)

    def test_it_would_be_high_without_the_flag(self):
        assert confidence.compute(**self._STRONG).band == "High"

    def test_unread_specifics_keep_it_below_high_and_say_why(self):
        result = confidence.compute(**self._STRONG, unread_specifics=True)
        assert result.score <= confidence.UNCERTAIN_ID_CEILING
        assert result.band != "High"
        assert "model_unconfirmed" in result.reason_codes
        assert "item_recognised" not in result.reason_codes

    def test_an_uncertain_identification_still_says_so(self):
        result = confidence.compute(**{**self._STRONG,
                                       "identification_certainty": "uncertain"},
                                    unread_specifics=True)
        assert "item_uncertain" in result.reason_codes


class TestServed:
    def test_scan_drops_an_unread_brand_and_is_not_high(self, monkeypatch):
        payload = {**V2_PAYLOAD,
                   "visual_evidence": ["navy quarter-zip fleece", "interior tag reads M"]}
        body = _scan(monkeypatch, payload, version="v2")[0].json()
        assert body["brand"] == "Unknown"
        assert "Patagonia" not in body["item_name"]
        assert body["confidence"] != "High"

    def test_scan_drops_the_unread_size_and_is_not_high(self, monkeypatch):
        payload = {**V2_PAYLOAD,
                   "item_name": "Patagonia Better Sweater 1/4-Zip, Size L", "size": "L",
                   "visual_evidence": ["Patagonia wordmark on left chest"]}
        body = _scan(monkeypatch, payload, version="v2")[0].json()
        assert body["item_name"] == "Patagonia Better Sweater 1/4-Zip"
        assert body["size"] is None
        assert body["confidence"] != "High"
