#!/usr/bin/env python3
"""Does every App Store link on the site carry the campaign it should? (#204)

App Store Connect credits a bare product link to the referring domain only,
so an install from /worth, /guess, the homepage's hero or its pricing cards
was one undivided number. Every product link is now `app_store(ct)` from
campaigns.py, and this fails when one is not:

  - it lacks the provider token `PT` from campaigns.py (or carries another),
  - its `ct` is not in the campaign table in website/README.md, or the table
    puts that `ct` on a different page,
  - it pins a storefront (`/us/`), which a campaign link never does,
  - or it is otherwise not exactly the link `app_store(ct)` builds.

It also fails when a row of the table has no link on its page, so the table
the growth dashboard reads names only campaigns that exist, and when a link's
`data-ct` (how apply_campaigns.py finds the hand-written links) disagrees
with its `ct`.

Only product links are held to this (`campaigns.is_product_link`: a path
ending in `id<digits>`). Excluded, because it opens Apple's redeem sheet
rather than the product page and cannot carry a campaign: the offer-code
redeem URL backend/referral.py builds,
`https://apps.apple.com/redeem?ctx=offercodes&id=…&code=…`. So is any other
non-product store URL, such as Manage Subscriptions. The Smart App Banner's
`apple-itunes-app` meta holds no URL.

Run: python3 website/seo/check_store_links.py
"""
from __future__ import annotations

import html
import pathlib
import re
import sys
import urllib.parse

import campaigns

WEBSITE = campaigns.WEBSITE

# Any URL on Apple's store hosts, in an attribute or a script string. It stops
# at a quote, whitespace, `<`, `>` or a backslash, so a URL inside a JS string
# inside an attribute ends where the attribute does.
STORE_URL = re.compile(r"https?://(?:apps|itunes)\.apple\.com/[^\s\"'<>\\]*")
STOREFRONT = re.compile(r"^/([a-z]{2})(?:/|$)")
ANCHOR = re.compile(r"<a\b[^>]*>", re.IGNORECASE)
ATTR = re.compile(r"""\b(href|data-ct)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)


def pages() -> list[pathlib.Path]:
    return sorted(p for pattern in ("*.html", "*.js")
                  for p in WEBSITE.rglob(pattern) if p.is_file())


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def problems_with(url: str, page: str, table: dict[str, str]) -> tuple[list[str], str | None]:
    """What is wrong with one product link on `page`, and the `ct` it carries."""
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
    found: list[str] = []

    storefront = STOREFRONT.match(parts.path)
    if storefront:
        found.append(f"pins the /{storefront.group(1)}/ storefront "
                     "(a campaign link carries none)")

    pt = query.get("pt", [None])[-1]
    if campaigns.PT is None or pt is None:
        found.append("lacks pt")
    elif pt != campaigns.PT:
        found.append(f"pt={pt} is not PT from campaigns.py")

    cts = query.get("ct", [])
    ct = cts[-1] if cts else None
    if ct is None:
        found.append("has no ct")
    elif ct not in table:
        found.append(f"ct={ct} is not in the campaign table (website/README.md)")
    elif table[ct] != page:
        found.append(f"ct={ct} belongs to {table[ct]} in the campaign table, not {page}")

    if not found and ct is not None and url != campaigns.app_store(ct):
        found.append(f"is not the link app_store({ct!r}) builds: "
                     f"{campaigns.app_store(ct)}")
    return found, ct


def main() -> int:
    table = campaigns.campaign_table()
    failures: list[str] = []
    if campaigns.PT is None:
        # Every link fails on this alone; say why once, not per link.
        failures.append("PT in website/seo/campaigns.py is None: #204 is waiting "
                        "for the owner's provider token, so no link can carry it")
    checked = 0
    used: dict[str, set[str]] = {}

    for missing in sorted(set(table.values())):
        if not (WEBSITE / missing).is_file():
            failures.append(f"website/README.md: the campaign table names {missing}, "
                            "which does not exist under website/")

    for path in pages():
        page = path.relative_to(WEBSITE).as_posix()
        text = path.read_text(encoding="utf-8")
        for match in STORE_URL.finditer(text):
            url = html.unescape(match.group(0))
            if not campaigns.is_product_link(url):
                continue    # the redeem sheet, Manage Subscriptions
            checked += 1
            found, ct = problems_with(url, page, table)
            if ct is not None:
                used.setdefault(page, set()).add(ct)
            if found:
                failures.append(f"website/{page}:{line_of(text, match.start())}: "
                                f"{match.group(0)}\n    " + "; ".join(found))

        # A hand-written link's data-ct is where apply_campaigns.py reads its
        # campaign from; it must name the ct its href carries.
        for tag in ANCHOR.finditer(text):
            attrs = {m.group(1).lower(): html.unescape(m.group(2) if m.group(2) is not None
                                                        else m.group(3) or "")
                     for m in ATTR.finditer(tag.group(0))}
            if "data-ct" not in attrs:
                continue
            href_ct = urllib.parse.parse_qs(
                urllib.parse.urlsplit(attrs.get("href", "")).query).get("ct", [None])[-1]
            if href_ct is not None and href_ct != attrs["data-ct"]:
                failures.append(f"website/{page}:{line_of(text, tag.start())}: "
                                f"data-ct={attrs['data-ct']} but the link carries ct={href_ct}")

    unused = [f"{ct} ({page})" for ct, page in table.items()
              if ct not in used.get(page, set())]
    if unused:
        failures.append(f"{len(unused)} campaign table row(s) with no link on their page: "
                        + ", ".join(unused))

    if failures:
        print("\n".join(failures))
        print(f"\nFAIL: {len(failures)} problem(s) across {checked} App Store "
              f"product link(s). See website/README.md, 'App Store campaign links'.")
        return 1
    print(f"OK: {checked} App Store product link(s) carry pt and a ct from the "
          f"campaign table, on the page the table names; all {len(table)} rows are in use")
    return 0


if __name__ == "__main__":
    sys.exit(main())
