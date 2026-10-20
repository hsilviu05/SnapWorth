"""Prompt v2.2: v2.1 with a naming rule and a condition rule.

What these pin:

* v2.2 is selectable and v2.1 is untouched, so the two compare cleanly and
  `SCAN_PROMPT_VERSION=v2.1` stays a rollback.
* v2.2 differs from v2.1 by the two rules and the price rule's wording and by
  nothing else, so a comparison between them measures exactly those.
* The rules sit in the honesty block, which overrides the steps above it, and
  the price rule no longer tells the model the item is used.
* A reply to v2.2 serves the same shape as v2.1, since the schema is v2.1's.
"""

from __future__ import annotations

import pytest

from scan import prompts
from eval import runner
from tests.test_prompt_v21 import V21, V21_PAYLOAD, _rule, _scan, _schema_keys

V22 = prompts.SCAN_PROMPT_V2_2


class TestRegistry:
    def test_v22_is_selectable(self):
        assert prompts.get_prompt("v2.2") == (V22, "v2.2")
        assert prompts.get_prompt(" V2.2 ") == (V22, "v2.2")

    def test_the_eval_accepts_it_as_an_arm(self):
        assert runner.parse_arm("v2.2") == ("v2.2", None)

    def test_the_default_is_unchanged(self):
        """Serving v2.2 is the owner's switch (`SCAN_PROMPT_VERSION`), as it
        was for v2.1."""
        assert prompts.DEFAULT_PROMPT_VERSION == "v2"


class TestOnlyTheRulesChanged:
    def test_removing_the_changes_gives_back_v21(self):
        undone = (V22.replace(prompts._NAMING_RULE + "\n", "")
                     .replace(prompts._CONDITION_RULE + "\n", "")
                     .replace("for a *secondhand* sale in the condition you graded",
                              "for a *used* item in the stated condition")
                     .replace("not retail (even for a new item),", "not retail,"))
        assert undone == V21

    def test_the_schema_is_v21s(self):
        assert _schema_keys(V22) == _schema_keys(V21)


class TestTheRules:
    def test_both_rules_are_honesty_rules(self):
        """In the block that overrides the steps, and ahead of the prices."""
        honesty = V22.index("## Honesty rules")
        output = V22.index("## Output")
        for rule in (prompts._NAMING_RULE, prompts._CONDITION_RULE):
            assert honesty < V22.index(rule) < output

    def test_naming_asks_for_a_generic_name_and_a_null_model(self):
        rule = _rule(V22, "- **Name a style only when you can read it.**")
        assert "brand, type, material and colour" in rule
        assert "set `model` to null" in rule

    def test_condition_names_factory_features_and_the_no_wear_grades(self):
        rule = _rule(V22, "- **Grade only the wear you can see.**")
        assert "moulded toe and heel shape" in rule
        assert "grade `new`" in rule and "`likeNew` otherwise" in rule
        assert "never `good` or `used`" in rule

    def test_the_price_rule_no_longer_says_used(self):
        rule = _rule(V22, "- **Prices are the ")
        assert "*used*" not in rule
        assert "condition you graded" in rule and "not retail" in rule

    @pytest.mark.parametrize("grade", ["new", "likeNew", "good", "used"])
    def test_the_grades_it_names_are_the_schemas(self, grade):
        assert grade in V22[V22.index('"condition_grade"'):].split("\n", 1)[0]


class TestServedAsV22:
    def test_the_switch_sends_v22_and_says_so(self, monkeypatch):
        result, calls = _scan(monkeypatch, V21_PAYLOAD, version="v2.2")
        assert result.status_code == 200
        assert calls[0].args[0][0] == V22
        assert result.json()["prompt_version"] == "v2.2"
