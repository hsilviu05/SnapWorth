#!/usr/bin/env python3
"""Every resale figure on the homepage must match the guide page it links to.

The homepage's hero demo, its four demo chips and both ticker tracks are
hand-written HTML. The guide pages under /worth are generated, and their
headline range comes from `condition_bounds` — the lowest and highest figure in
the item's own condition table.

Those were two independent sets of numbers and they had drifted on **13 of the
16 items**, always on the low bound and always in the same direction: the
homepage advertised a floor 11% to 60% above the one the page behind the link
published. A visitor reading "Patagonia Better Sweater $40–$85" in the ticker
clicked through to "$25–$85".

index.html says of its own demo: "Every range below is the one published on
this site's own /worth pages — the demo must never disagree with the rest of
the site." This is what makes that true, rather than hoping.
"""
from __future__ import annotations

import html
import importlib.util
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
INDEX = HERE.parent / "index.html"


def load_items():
    spec = importlib.util.spec_from_file_location("build_seo", HERE / "build_seo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {it["slug"]: module.condition_bounds(it) for it in module.ITEMS}


def main() -> int:
    truth = load_items()
    source = INDEX.read_text(encoding="utf-8")
    problems: list[str] = []
    checked = 0

    def expected(slug: str) -> str | None:
        if slug not in truth:
            return None
        low, high = truth[slug]
        return f"${low}–${high}"

    # Ticker rows: <a href="/worth/SLUG">Name <em>$low–$high</em></a>
    for match in re.finditer(
            r'href="/worth/([a-z0-9-]+)">[^<]*<em>([^<]+)</em>', source):
        slug, shown = match.group(1), html.unescape(match.group(2))
        want = expected(slug)
        if want is None:
            problems.append(f"ticker links /worth/{slug}, which is not a guide page")
            continue
        checked += 1
        if shown != want:
            problems.append(f"ticker: {slug} shows {shown}, the guide says {want}")

    # Demo chips: data-slug="SLUG" ... data-range="$low–$high"
    for match in re.finditer(
            r'data-slug="([a-z0-9-]+)"[^>]*data-range="([^"]+)"', source):
        slug, shown = match.group(1), html.unescape(match.group(2))
        want = expected(slug)
        if want is None:
            problems.append(f"demo chip names /worth/{slug}, which is not a guide page")
            continue
        checked += 1
        if shown != want:
            problems.append(f"chip: {slug} shows {shown}, the guide says {want}")

    # The statically rendered hero range, which must equal the first chip's.
    first_chip = re.search(r'data-slug="([a-z0-9-]+)"', source)
    hero = re.search(r'id="sd-range">([^<]+)<', source)
    if first_chip and hero:
        checked += 1
        want = expected(first_chip.group(1))
        shown = html.unescape(hero.group(1))
        if shown != want:
            problems.append(
                f"hero range shows {shown}; the first chip is "
                f"{first_chip.group(1)}, which the guide puts at {want}")

    if not checked:
        print("check_homepage_ranges: matched nothing — the markup has moved, "
              "and a check that matches nothing passes for the wrong reason",
              file=sys.stderr)
        return 1

    if problems:
        print(f"{len(problems)} figure(s) on the homepage disagree with /worth:",
              file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        print("\nRun website/seo/build_seo.py and copy the guide's own numbers.",
              file=sys.stderr)
        return 1

    print(f"check_homepage_ranges: {checked} figures agree with /worth")
    return 0


if __name__ == "__main__":
    sys.exit(main())
