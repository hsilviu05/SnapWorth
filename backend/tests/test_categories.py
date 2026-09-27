"""The item-category table, and everything that used to keep its own copy.

The eleven names were restated in the prompts, the price bands, the confidence
weights and the operator feed, and the server never enforced them — an
off-list answer from the model was passed to the client verbatim and read
three different ways on the way. These pin the single table and the one
normalisation point.
"""

from __future__ import annotations

import re

import pytest

import categories
import confidence
import main
import notify
import prompts
import promptsafety
import valuation
from tests.test_ai_pipeline import V2_PAYLOAD, _scan_with


class TestOneTable:
    def test_every_prompt_offers_exactly_the_table(self):
        for version, text in prompts.PROMPTS.items():
            match = re.search(r'"category": "One of: ([^"]+)"', text)
            assert match, f"prompt {version} lost its category line"
            assert tuple(match.group(1).split(", ")) == categories.NAMES, version

    def test_the_prompt_text_did_not_change(self):
        """Interpolating the list must leave the prompt byte-identical, or the
        eval baselines measured against it silently stop applying."""
        assert ('"category": "One of: clothing, shoes, accessories, electronics, '
                'books, furniture, home, sports, toys, collectibles, other"'
                in prompts.SCAN_PROMPT_V2)

    @pytest.mark.parametrize("view", [
        promptsafety._CATEGORY_BANDS,
        confidence._CATEGORY_FAMILIARITY,
        notify.CATEGORY_EMOJI,
    ])
    def test_every_consumer_covers_exactly_the_table(self, view):
        assert tuple(view) == categories.NAMES

    def test_the_unused_v1_copy_in_main_is_gone(self):
        """It was byte-identical to `prompts.SCAN_PROMPT_V1` and unreferenced
        since the first commit: an edit there passed review and changed
        nothing in production."""
        assert not hasattr(main, "SCAN_PROMPT")

    def test_other_is_the_default_everywhere(self):
        assert promptsafety.DEFAULT_BAND == categories.BY_NAME["other"].band
        assert confidence._UNFAMILIAR == categories.BY_NAME["other"].familiarity


class TestNormalise:
    @pytest.mark.parametrize("raw, expected", [
        ("clothing", "clothing"),
        ("  Shoes ", "shoes"),
        ("COLLECTIBLES", "collectibles"),
        ("Vintage", "other"),
        ("home decor", "other"),
        ("", "other"),
        (None, "other"),
        (7, "other"),
    ])
    def test_resolves_to_a_table_name(self, raw, expected):
        assert categories.normalise(raw) == expected

    def test_normalise_applies_it_to_the_model_reply(self):
        assert valuation.normalise({"category": "Electronics"}).category == "electronics"
        assert valuation.normalise({"category": "Antiques"}).category == "other"
        assert valuation.normalise({}).category == "other"


class TestScanServesATableName:
    def test_an_off_list_category_is_served_as_other(self):
        body = _scan_with(dict(V2_PAYLOAD, category="Outerwear")).json()
        assert body["category"] == "other"

    def test_a_differently_cased_category_is_served_canonically(self):
        body = _scan_with(dict(V2_PAYLOAD, category="Clothing")).json()
        assert body["category"] == "clothing"
