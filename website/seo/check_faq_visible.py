#!/usr/bin/env python3
"""Every FAQPage answer must be visible on the page that declares it.

Google's requirement for FAQPage is that the full question and answer text be
present for the user on the source page. The generator built `mainEntity` from
`[(question, answer)] + item["faqs"]` while the visible accordion iterated
`item["faqs"]` alone, so on all sixteen item pages the synthesised pair was
declared as an on-page FAQ with its answer nowhere in the rendered body — the
string occurred exactly once per file, inside the ld+json block.

Both now come from one list, and this is the check that keeps them there.

Note on the comparison: the body is HTML-unescaped before matching. The first
version of this check reported `levis-501-vintage.html` as still broken, and
the page was fine — the apostrophe in "Levi's" is `&#x27;` in the body and a
raw `'` in the JSON. The check was wrong, not the page.
"""
from __future__ import annotations

import html
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent / "worth"
LD_JSON = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
SCRIPTS = re.compile(r"<script.*?</script>", re.S)
TAGS = re.compile(r"<[^>]+>")


def visible_text(doc: str) -> str:
    body = doc.split("</head>", 1)[-1]
    body = SCRIPTS.sub("", body)
    return html.unescape(TAGS.sub(" ", body))


def faq_pairs(doc: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for block in LD_JSON.findall(doc):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for node in data.get("@graph", [data]):
            if node.get("@type") != "FAQPage":
                continue
            for entry in node.get("mainEntity", []):
                pairs.append((entry.get("name", ""),
                              entry.get("acceptedAnswer", {}).get("text", "")))
    return pairs


def main() -> int:
    pages = sorted(ROOT.glob("*.html"))
    if not pages:
        print(f"no pages under {ROOT} — did the generator run?")
        return 1

    problems = 0
    for page in pages:
        doc = page.read_text(encoding="utf-8")
        pairs = faq_pairs(doc)
        text = visible_text(doc)
        for question, answer in pairs:
            for label, value in (("question", question), ("answer", answer)):
                if value and value not in text:
                    print(f"{page.name}: FAQ {label} is markup-only — "
                          f"{value[:70]!r}")
                    problems += 1

    print(f"{len(pages)} pages checked, {problems} markup-only FAQ field(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
