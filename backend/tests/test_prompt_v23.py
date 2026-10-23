"""Prompt v2.3: v2.2 with a brand rule.

What these pin:

* v2.3 is selectable, v2.2 is untouched and the default is unchanged, so the
  two compare cleanly and serving v2.3 stays the owner's switch.
* v2.3 differs from v2.2 by the brand rule and nothing else, so a comparison
  between them measures exactly that.
* The rule sits in the honesty block, which overrides the steps above it.
* A reply to v2.3 serves the same shape as v2.2, since the schema is the same.
"""

from __future__ import annotations

from scan import prompts
from eval import runner
from tests.test_prompt_v21 import V21_PAYLOAD, _rule, _scan, _schema_keys

V22 = prompts.SCAN_PROMPT_V2_2
V23 = prompts.SCAN_PROMPT_V2_3


class TestRegistry:
    def test_v23_is_selectable(self):
        assert prompts.get_prompt("v2.3") == (V23, "v2.3")
        assert prompts.get_prompt(" V2.3 ") == (V23, "v2.3")

    def test_v22_still_serves_itself(self):
        assert prompts.get_prompt("v2.2") == (V22, "v2.2")

    def test_the_eval_accepts_it_as_an_arm(self):
        assert runner.parse_arm("v2.3") == ("v2.3", None)

    def test_the_default_is_unchanged(self):
        assert prompts.DEFAULT_PROMPT_VERSION == "v2"


class TestOnlyTheRuleChanged:
    def test_removing_the_rule_gives_back_v22(self):
        assert V23.replace(prompts._BRAND_RULE + "\n", "") == V22

    def test_the_schema_is_v22s(self):
        assert _schema_keys(V23) == _schema_keys(V22)


class TestTheRule:
    def test_it_is_an_honesty_rule_after_the_naming_rule(self):
        honesty = V23.index("## Honesty rules")
        output = V23.index("## Output")
        naming = V23.index(prompts._NAMING_RULE)
        assert honesty < naming < V23.index(prompts._BRAND_RULE) < output

    def test_a_brand_needs_legible_evidence(self):
        rule = _rule(V23, "- **Name a brand only when you can read it.**")
        assert "legible" in rule and "`visual_evidence`" in rule
        assert "A label you can see but not read is not a brand" in rule
        assert 'set `brand` to "Unknown"' in rule

    def test_a_look_is_not_a_brand(self):
        rule = _rule(V23, "- **Name a brand only when you can read it.**")
        assert "familiar look is not evidence of a brand" in rule

    def test_letter_sizes_read_as_adult(self):
        rule = _rule(V23, "- **Name a brand only when you can read it.**")
        assert "adult size unless the label itself gives a child's age or height" in rule


class TestServedAsV23:
    def test_the_switch_sends_v23_and_says_so(self, monkeypatch):
        result, calls = _scan(monkeypatch, V21_PAYLOAD, version="v2.3")
        assert result.status_code == 200
        assert calls[0].args[0][0] == V23
        assert result.json()["prompt_version"] == "v2.3"
