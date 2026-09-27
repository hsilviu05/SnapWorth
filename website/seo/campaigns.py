"""App Store campaign links for every App Store link on snapworth.eu (#204).

A bare product link is credited in App Store Connect to the referring domain
and nothing finer, so the homepage's eight buttons, the /worth guides, /guess
and the invite page were one number. A campaign link names its surface:

    https://apps.apple.com/app/apple-store/id6788521307?pt=<PT>&ct=<ct>&mt=8

and ASC -> App Analytics -> Campaigns then lists impressions, downloads and
proceeds per `ct`. The form drops the storefront and the slug, which the old
links pinned to `/us/app/snapworth-resale-scanner/`: the App Store sends each
visitor to their own storefront.

`PT` is the provider token, the same for every campaign link the account
generates. It is public (it is in every link a visitor clicks), but it is not
derivable from the repo, and it is the only value here that is not ours to
choose. Until the owner posts it on #204 it is None, and `app_store` refuses
to build a link, so no page can ship a link that credits nobody.

Each `ct` and the page it belongs to is the campaign table in
website/README.md. check_store_links.py holds every link on the site to that
table and to `PT`; apply_campaigns.py writes the links into the hand-written
pages and regenerates the generated ones.
"""
from __future__ import annotations

import pathlib
import re

# TODO(owner): #204. The provider token from App Store Connect -> App Analytics
# -> Campaigns -> generate any campaign link: the value after `pt=`. Set it here,
# as a string, then run `python3 website/seo/apply_campaigns.py`.
PT: str | None = None

APP_ID = "6788521307"

WEBSITE = pathlib.Path(__file__).resolve().parents[1]
README = WEBSITE / "README.md"

# Lower-case letters, digits, `_` and `-`: the table's values, and nothing that
# needs escaping in a query string or an HTML attribute.
CT_SHAPE = re.compile(r"[a-z0-9][a-z0-9_-]*")
PT_SHAPE = re.compile(r"[A-Za-z0-9]+")

# The campaign table sits between these two lines of website/README.md.
TABLE_BEGIN = "<!-- campaign-table:begin -->"
TABLE_END = "<!-- campaign-table:end -->"
_ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|")


class MissingProviderToken(RuntimeError):
    pass


def provider_token() -> str:
    if PT is None:
        raise MissingProviderToken(
            "PT in website/seo/campaigns.py is None: #204 is waiting for the "
            "owner to post the App Store Connect provider token (pt). No App "
            "Store link can be built without it, and a link without it credits "
            "no campaign. Set PT, then run website/seo/apply_campaigns.py.")
    if not PT_SHAPE.fullmatch(PT):
        raise ValueError(f"PT {PT!r} is not a provider token: it should be the "
                         "letters and digits after `pt=` in an ASC campaign link")
    return PT


def app_store(ct: str) -> str:
    """The App Store link for campaign `ct`, a value from the campaign table."""
    if not CT_SHAPE.fullmatch(ct):
        raise ValueError(f"campaign token {ct!r} is not lower-case letters, "
                         "digits, `_` and `-`")
    return (f"https://apps.apple.com/app/apple-store/id{APP_ID}"
            f"?pt={provider_token()}&ct={ct}&mt=8")


def campaign_table() -> dict[str, str]:
    """ct -> the page it appears on (a path under website/), from README.md."""
    text = README.read_text(encoding="utf-8")
    try:
        body = text.split(TABLE_BEGIN, 1)[1].split(TABLE_END, 1)[0]
    except IndexError:
        raise SystemExit(f"{README}: the campaign table must sit between "
                         f"{TABLE_BEGIN} and {TABLE_END}") from None
    table: dict[str, str] = {}
    for line in body.splitlines():
        match = _ROW.match(line.strip())
        if not match:
            continue
        ct, page = match.groups()
        if not CT_SHAPE.fullmatch(ct):
            raise SystemExit(f"{README}: campaign token {ct!r} is not lower-case "
                             "letters, digits, `_` and `-`")
        if ct in table:
            raise SystemExit(f"{README}: campaign token {ct!r} is in the table twice")
        table[ct] = page
    if not table:
        raise SystemExit(f"{README}: no rows found in the campaign table")
    return table
