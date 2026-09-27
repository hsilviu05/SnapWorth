#!/usr/bin/env python3
"""Put the campaign links on the site, once PT is set in campaigns.py (#204).

1. Rewrites every App Store link in the hand-written pages, index.html and
   invite.html, to `app_store(ct)`, taking `ct` from the link's own `data-ct`
   attribute. A store link without `data-ct` stops the run: it would need a
   row in the campaign table first.
2. Regenerates /guess and then the /worth guides, the hub and the sitemap,
   in the order website.yml's staleness message gives, so the sitemap stamps
   the pages this run changed.
3. Runs check_store_links.py.

It changes files and commits nothing. Safe to run again: the rewrite matches
any App Store product link, so a second run with the same PT changes nothing.

Run: python3 website/seo/apply_campaigns.py
"""
from __future__ import annotations

import html
import re
import subprocess
import sys

import campaigns

HAND_WRITTEN = ("index.html", "invite.html")
ANCHOR = re.compile(r"<a\b[^>]*>", re.IGNORECASE)
HREF = re.compile(r"""\bhref\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
DATA_CT = re.compile(r'\bdata-ct\s*=\s*"([^"]*)"', re.IGNORECASE)


def rewrite(name: str) -> tuple[str, int]:
    """`name`'s text with its App Store links set from data-ct, and how many."""
    text = (campaigns.WEBSITE / name).read_text(encoding="utf-8")
    count = 0

    def one(tag: re.Match[str]) -> str:
        nonlocal count
        source = tag.group(0)
        href = HREF.search(source)
        if href is None:
            return source
        url = html.unescape(href.group(1) if href.group(1) is not None else href.group(2))
        # The same test check_store_links.py applies: a product page, not the
        # redeem sheet or Manage Subscriptions.
        if not campaigns.is_product_link(url):
            return source
        ct = DATA_CT.search(source)
        if ct is None:
            line = text.count("\n", 0, tag.start()) + 1
            sys.exit(f"website/{name}:{line}: an App Store link with no data-ct. "
                     "Give it a campaign row in website/README.md and a "
                     "data-ct attribute naming it, then run this again.")
        count += 1
        link = html.escape(campaigns.app_store(ct.group(1)))
        return f'{source[:href.start()]}href="{link}"{source[href.end():]}'

    return ANCHOR.sub(one, text), count


def run(script: str) -> None:
    subprocess.run([sys.executable, str(campaigns.WEBSITE / "seo" / script)], check=True)


def main() -> int:
    try:
        campaigns.provider_token()
    except campaigns.MissingProviderToken as missing:
        sys.exit(str(missing))
    # Both pages are rewritten in memory first, so a link with no data-ct in
    # either stops the run before anything is written.
    rewritten = {name: rewrite(name) for name in HAND_WRITTEN}
    for name, (text, count) in rewritten.items():
        path = campaigns.WEBSITE / name
        if path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
        print(f"{name}: {count} App Store link(s) set from data-ct", flush=True)
    run("build_guess.py")
    run("build_seo.py")
    result = subprocess.run([sys.executable,
                             str(campaigns.WEBSITE / "seo" / "check_store_links.py")])
    if result.returncode == 0:
        print("\nNext: review `git diff`, commit, and push. Commit on the day you "
              "ran this, or CI's sitemap check will ask for build_seo.py again.")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
