#!/usr/bin/env python3
"""Check every App Store listing field against Apple's limits, and print the counts.

Reads `marketing/app_store_listing.md` and every `app_store_listing.<locale>.md`
beside it. Each file keeps its fields under the same headings, so the values
are found by heading rather than copied here:

  * **App name** and **Subtitle**: the first non-empty line under the heading.
  * **Keywords**: the first non-empty line under the heading.
  * **Promotional Text**: every paragraph that directly follows a bold label
    paragraph (`**Primary — …**`, `**Live — …**`, `**Alternative — …**`).
  * **Description**: everything under the heading up to a line that is
    exactly `---`.

The limits, from App Store Connect Help (read 2026-10-24):

  * Name: at least 2 and no more than 30 characters (*App information*).
  * Subtitle: no more than 30 characters (*App information*).
  * Promotional text: no more than 170 characters, and description: 4000
    (*Platform version information*).
  * Keywords: "up to 100 bytes", each keyword "greater than two characters",
    not repeating the app name, and "Names of other apps or companies aren't
    allowed" (*Platform version information*). Bytes, not characters: one
    `ä` is two bytes and one Chinese character three.

A failure is something App Store Connect refuses or App Review rejects. A
warning is a field that is legal but wastes space. Exit status 1 on any
failure.

    python3 tools/check_listing_limits.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKETING = ROOT / "marketing"

NAME_MAX = 30
SUBTITLE_MAX = 30
PROMO_MAX = 170
DESCRIPTION_MAX = 4000
KEYWORDS_MAX_BYTES = 100

#: Other apps and companies a reseller might search for. Apple does not allow
#: them in the keyword field (Guideline 2.3.7). Naming a marketplace the app
#: supports in the *description* is a feature, not a keyword, and is fine.
OTHER_COMPANIES = {
    "ebay", "poshmark", "mercari", "depop", "vinted", "goodwill", "etsy",
    "grailed", "thredup", "kleinanzeigen", "olx", "wallapop", "facebook",
    "marketplace", "vestiaire", "leboncoin", "subito", "amazon", "craigslist",
    "okazii", "闲鱼", "转转", "得物",
}

#: Words too common to be worth a keyword slot, so a repeat of them in the
#: name or subtitle is not reported.
STOPWORDS = {"a", "an", "and", "the", "of", "for", "in", "it", "s", "&", "your",
             "de", "la", "el", "y", "und", "der", "die", "das", "si", "și"}


def section(text: str, heading: str) -> str | None:
    """The body under the first `## <heading>…` line, up to the next `## `."""
    match = re.search(rf"^## {re.escape(heading)}[^\n]*\n(.*?)(?=^## |\Z)",
                      text, flags=re.M | re.S)
    return match.group(1) if match else None


def first_line(body: str | None) -> str | None:
    if body is None:
        return None
    for line in body.splitlines():
        if line.strip():
            return line.strip()
    return None


def paragraphs(body: str) -> list[str]:
    return [p.strip("\n") for p in re.split(r"\n\s*\n", body) if p.strip()]


def promo_texts(body: str | None) -> list[tuple[str, str]]:
    """(label, text) for each paste-ready promotional text."""
    if body is None:
        return []
    found = []
    paras = paragraphs(body)
    for label, text in zip(paras, paras[1:]):
        if label.startswith("**") and not text.startswith("**") and text != "---":
            found.append((label.split("**")[1].strip(" .—"), text))
    return found


def description(body: str | None) -> str | None:
    if body is None:
        return None
    lines = body.split("\n")
    end = next((i for i, line in enumerate(lines) if line.strip() == "---"), len(lines))
    return "\n".join(lines[:end]).strip("\n")


def words(text: str) -> set[str]:
    """Latin-script words, lower-cased. CJK has no spaces to split on."""
    return {w for w in re.findall(r"[^\W\d_]+", text.lower()) if w not in STOPWORDS}


def check(path: Path) -> tuple[list[str], list[str]]:
    text = path.read_text(encoding="utf-8")
    failures: list[str] = []
    warnings: list[str] = []

    def row(field: str, value: int, limit: int, unit: str = "chars") -> None:
        mark = "ok " if value <= limit else "OVER"
        print(f"  {mark} {field:<34} {value:>5} / {limit} {unit}")
        if value > limit:
            failures.append(f"{path.name}: {field} is {value} {unit}, limit {limit}")

    print(path.relative_to(ROOT))
    name = first_line(section(text, "App name"))
    subtitle = first_line(section(text, "Subtitle"))
    keywords = first_line(section(text, "Keywords"))
    for field, value in (("App name", name), ("Subtitle", subtitle), ("Keywords", keywords)):
        if not value:
            failures.append(f"{path.name}: no {field} found")
    if name:
        row(f"Name  {name!r}", len(name), NAME_MAX)
        if len(name) < 2:
            failures.append(f"{path.name}: name shorter than 2 characters")
    if subtitle:
        row(f"Subtitle  {subtitle!r}", len(subtitle), SUBTITLE_MAX)
    if name and subtitle:
        both = sorted(words(name) & words(subtitle))
        if both:
            warnings.append(f"{path.name}: the subtitle repeats the name: {', '.join(both)}")

    for label, promo in promo_texts(section(text, "Promotional Text")):
        row(f"Promotional text ({label[:20]})", len(promo), PROMO_MAX)

    desc = description(section(text, "Description"))
    if desc:
        row("Description", len(desc), DESCRIPTION_MAX)
    else:
        failures.append(f"{path.name}: no Description found")

    if keywords:
        row("Keywords", len(keywords.encode("utf-8")), KEYWORDS_MAX_BYTES, "bytes")
        print(f"       ({len(keywords)} characters)")
        terms = keywords.split(",")
        if any(t != t.strip() for t in terms):
            failures.append(f"{path.name}: a space beside a comma in Keywords wastes a byte")
        terms = [t.strip().lower() for t in terms]
        if len(set(terms)) != len(terms):
            failures.append(f"{path.name}: a keyword is repeated")
        companies = sorted(t for t in terms if t in OTHER_COMPANIES)
        if companies:
            failures.append(f"{path.name}: other companies in Keywords (2.3.7): {', '.join(companies)}")
        short = [t for t in terms if len(t) <= 2]
        if short:
            warnings.append(f"{path.name}: keywords of two characters or fewer: {', '.join(short)}")
        title_words = words(f"{name or ''} {subtitle or ''}")
        repeats = sorted(t for t in terms if t in title_words)
        if repeats:
            warnings.append(f"{path.name}: already in the name or subtitle: {', '.join(repeats)}")
    print()
    return failures, warnings


def main() -> int:
    paths = sorted(MARKETING.glob("app_store_listing*.md"))
    failures: list[str] = []
    warnings: list[str] = []
    for path in paths:
        f, w = check(path)
        failures += f
        warnings += w
    for w in warnings:
        print(f"warning: {w}")
    for f in failures:
        print(f"FAIL: {f}")
    print(f"{len(paths)} listings checked, {len(failures)} failure(s), {len(warnings)} warning(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
