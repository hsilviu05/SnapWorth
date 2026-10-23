"""eBay sold listings into the gold set, through the official API (#213).

    python -m eval.ebay_sold --probe             # can this app read sold data?
    python -m eval.ebay_sold --dry-run           # what the default plan would add
    python -m eval.ebay_sold                     # add them as drafts
    python -m eval.ebay_sold --plan "shoes|Nike|Air Max 97|nike air max 97" --per-query 6

A completed eBay sale, photo and final price, is a `high` gold label
(`schema.LabelConfidence.HIGH`). This reads them from eBay's **Marketplace
Insights API** (`item_sales/search`), which returns items sold in the last 90
days. It is a limited-release API: it answers only an app eBay has approved
for it, and it never touches ebay.com's pages, which eBay's user agreement
does not allow to be crawled without a written grant.

Credentials are the app's production keyset from developer.ebay.com,
`EBAY_CLIENT_ID` and `EBAY_CLIENT_SECRET`, exported or in `backend/.env`. They
are exchanged for an application token (client credentials) and never
printed. `--probe` makes one search and says plainly whether the keys work and
whether the app has the sold-data scope; run it first.

What it writes is what `eval.intake` writes, through the intake itself: draft
records with stable ids, the photo converted to production's 1568 px JPEG in
the private store, duplicates skipped by hash. Each draft's evidence is the
sale as eBay returned it, saved as `ebay/<itemId>.json` in the private store
and named `private:ebay/<itemId>.json`. The public `gold.jsonl` gets no item
id, title or link: those identify a third party's listing. Every record is a
draft; a person approves it with `python -m eval.review`.

The field names below follow eBay's published `ItemSales` contract
(`itemSales[].lastSoldPrice.value`, `.lastSoldDate`, `.image.imageUrl`,
`.conditionId`). `--probe` prints the keys of the first sale it gets back, so
a difference shows up before anything is written.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval import identity, intake, schema

API = "https://api.ebay.com"
TOKEN_PATH = "/identity/v1/oauth2/token"
SEARCH_PATH = "/buy/marketplace_insights/v1_beta/item_sales/search"
SCOPE = "https://api.ebay.com/oauth/api_scope/buy.marketplace.insights"
EVIDENCE_DIR = "ebay"

#: The country each eBay site sells in, for the record's `region`.
MARKETPLACES = {"EBAY_US": "US", "EBAY_GB": "GB", "EBAY_DE": "DE", "EBAY_FR": "FR",
                "EBAY_IT": "IT", "EBAY_ES": "ES", "EBAY_AU": "AU", "EBAY_CA": "CA"}

#: eBay condition ids → the app's condition grades (`scan/valuation.py`).
CONDITIONS = {1000: "new", 1500: "likeNew", 1750: "likeNew", 2750: "likeNew",
              2000: "good", 2010: "good", 2020: "good", 2030: "good", 2500: "good",
              3000: "used", 4000: "good", 5000: "good", 6000: "used", 7000: "used"}


@dataclass(frozen=True)
class PlanEntry:
    """One search: what it is in the app's terms, and what to ask eBay."""

    category: str
    brand: str
    model: str | None
    query: str

    @classmethod
    def parse(cls, text: str) -> PlanEntry:
        parts = [p.strip() for p in text.split("|")]
        if len(parts) != 4 or not parts[0] or not parts[3]:
            raise ValueError(f"plan entry {text!r} is not category|brand|model|query")
        return cls(parts[0], parts[1], parts[2] or None, parts[3])


#: A spread across what people scan, so the set isn't all one category.
DEFAULT_PLAN = [PlanEntry(*row) for row in (
    ("clothing", "Levi's", "501", "levis 501 jeans"),
    ("clothing", "Patagonia", "Better Sweater", "patagonia better sweater"),
    ("clothing", "Carhartt", "Detroit Jacket", "carhartt detroit jacket"),
    ("clothing", "Ralph Lauren", None, "polo ralph lauren shirt"),
    ("shoes", "Nike", "Air Max 97", "nike air max 97"),
    ("shoes", "Adidas", "Samba", "adidas samba"),
    ("shoes", "Dr. Martens", "1460", "dr martens 1460 boots"),
    ("shoes", "New Balance", "990", "new balance 990"),
    ("accessories", "Coach", None, "coach shoulder bag leather"),
    ("accessories", "Seiko", None, "seiko 5 automatic watch"),
    ("accessories", "Ray-Ban", "Wayfarer", "ray-ban wayfarer sunglasses"),
    ("electronics", "Nintendo", "Switch OLED", "nintendo switch oled console"),
    ("electronics", "Sony", "WH-1000XM4", "sony wh-1000xm4 headphones"),
    ("electronics", "Apple", "AirPods Pro", "apple airpods pro"),
    ("home", "Le Creuset", None, "le creuset dutch oven"),
    ("home", "Pyrex", None, "vintage pyrex bowl"),
    ("toys", "LEGO", None, "lego star wars set"),
    ("sports", "Callaway", None, "callaway driver golf club"),
)]


class EbayError(Exception):
    """Something the person running this has to fix; the message says what."""


Get = Callable[..., httpx.Response]
Post = Callable[..., httpx.Response]


def credentials() -> tuple[str, str]:
    identity.load_env()
    client_id = os.environ.get("EBAY_CLIENT_ID", "").strip()
    secret = os.environ.get("EBAY_CLIENT_SECRET", "").strip()
    if not client_id or not secret:
        raise EbayError("EBAY_CLIENT_ID and EBAY_CLIENT_SECRET are not set. Put the app's "
                        "production keyset (developer.ebay.com → Application Keys) in "
                        "backend/.env, then run --probe.")
    return client_id, secret


def app_token(client_id: str, secret: str, post: Post) -> str:
    basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    response = post(API + TOKEN_PATH,
                    headers={"Authorization": f"Basic {basic}",
                             "Content-Type": "application/x-www-form-urlencoded"},
                    data={"grant_type": "client_credentials", "scope": SCOPE})
    if response.status_code == 401:
        raise EbayError("eBay rejected the Client ID / Client Secret. Check they are the "
                        "Production keyset, not Sandbox, and copied whole.")
    if response.status_code == 400 and "scope" in response.text.lower():
        raise EbayError("eBay accepted the keys but would not grant the Marketplace Insights "
                        "scope: this app is not approved for sold-items data. The approval you "
                        "have covers something else (the Browse API returns asking prices, "
                        "not sales). Apply for Marketplace Insights in the developer portal.")
    if response.status_code != 200:
        raise EbayError(f"eBay's token endpoint answered {response.status_code}.")
    token = response.json().get("access_token")
    if not token:
        raise EbayError("eBay's token endpoint answered without a token.")
    return token


def search(token: str, query: str, limit: int, marketplace: str, get: Get) -> list[dict]:
    response = get(API + SEARCH_PATH,
                   headers={"Authorization": f"Bearer {token}",
                            "X-EBAY-C-MARKETPLACE-ID": marketplace},
                   params={"q": query, "limit": str(limit)})
    if response.status_code == 403:
        raise EbayError("eBay refused the sold-items search (403): this app has no Marketplace "
                        "Insights access. The approval covers something else.")
    if response.status_code == 429:
        raise EbayError("eBay's call limit for today is used up (429). Try again tomorrow.")
    if response.status_code != 200:
        raise EbayError(f"eBay's sold-items search answered {response.status_code} for {query!r}.")
    return list(response.json().get("itemSales") or [])


def price_of(sale: dict) -> tuple[float, str] | None:
    price = sale.get("lastSoldPrice") or {}
    try:
        value = float(str(price.get("value")))
    except ValueError:
        return None
    currency = str(price.get("currency") or "").upper()
    return (value, currency) if math.isfinite(value) and value > 0 and len(currency) == 3 else None


def condition_of(sale: dict) -> str | None:
    try:
        return CONDITIONS.get(int(str(sale.get("conditionId"))))
    except ValueError:
        return None


def to_draft(sale: dict, entry: PlanEntry, marketplace: str, photo: Path) -> intake.Draft:
    """A gold draft for one sale. Nothing that identifies the listing goes in
    a field that reaches the public file."""
    value, currency = price_of(sale) or (0.0, "")
    item_id = str(sale.get("itemId"))
    return intake.Draft(
        source=f"eBay sale {item_id}", photo=photo,
        tags=["ebay", "listing_photo"],
        fields={
            "actual_sale_price": value, "currency": currency,
            "category": entry.category, "brand": entry.brand, "model": entry.model,
            "condition": condition_of(sale),
            "sold_date": str(sale.get("lastSoldDate") or "")[:10] or None,
            "marketplace": "ebay", "region": MARKETPLACES.get(marketplace, "unknown"),
            "label_confidence": "high",
            "evidence": f"private:{EVIDENCE_DIR}/{safe(item_id)}.json",
            "difficulty": "easy",
            "human_notes": f"eBay sold listing, search {entry.query!r}. Check the photo shows "
                           f"{entry.brand}{' ' + entry.model if entry.model else ''} before "
                           "approving; the title is in the evidence file.",
        })


def safe(item_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in item_id)


def collect(plan: list[PlanEntry], per_query: int, marketplace: str, token: str,
            get: Get, workdir: Path, evidence: Path | None, out
            ) -> list[intake.Draft]:
    """Search each entry, download each usable sale's photo into `workdir`,
    and (unless `evidence` is None) save the sale as returned."""
    drafts: list[intake.Draft] = []
    for entry in plan:
        sales = search(token, entry.query, per_query, marketplace, get)
        kept = 0
        for sale in sales:
            image = (sale.get("image") or {}).get("imageUrl")
            if not sale.get("itemId") or not image or not price_of(sale) \
                    or not sale.get("lastSoldDate"):
                continue
            picture = get(image)
            if picture.status_code != 200 or not picture.content:
                continue
            photo = workdir / f"{safe(str(sale['itemId']))}{Path(image.split('?')[0]).suffix or '.jpg'}"
            photo.write_bytes(picture.content)
            if evidence is not None:
                evidence.mkdir(parents=True, exist_ok=True)
                (evidence / f"{safe(str(sale['itemId']))}.json").write_text(
                    json.dumps(sale, indent=2, sort_keys=True), encoding="utf-8")
            drafts.append(to_draft(sale, entry, marketplace, photo))
            kept += 1
        print(f"  {entry.query!r}: {len(sales)} sold, {kept} usable", file=out)
    return drafts


def probe(token: str, marketplace: str, get: Get, out) -> int:
    sales = search(token, DEFAULT_PLAN[0].query, 1, marketplace, get)
    print("✓ The keys work and this app can read eBay sold-items data.", file=out)
    if sales:
        print(f"  First sale's fields: {', '.join(sorted(sales[0]))}", file=out)
        ok = price_of(sales[0]) and (sales[0].get("image") or {}).get("imageUrl")
        print("  " + ("Price and photo are where this tool reads them." if ok else
                      "⚠ Price or photo is not where this tool expects it; send these "
                      "field names to Claude before collecting."), file=out)
    else:
        print("  (No sale came back for the test search.)", file=out)
    return 0


def main(argv: list[str] | None = None, get: Get | None = None, post: Post | None = None,
         convert: intake.Converter = intake.sips_converter, out=None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="python -m eval.ebay_sold", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--probe", action="store_true",
                        help="one search: do the keys work, and is sold data allowed?")
    parser.add_argument("--dry-run", action="store_true", help="show what would be added")
    parser.add_argument("--plan", action="append", default=None,
                        help="category|brand|model|query, repeatable (default: a built-in spread)")
    parser.add_argument("--per-query", type=int, default=4,
                        help="sales to take per search (default 4)")
    parser.add_argument("--marketplace", default="EBAY_US", choices=sorted(MARKETPLACES))
    parser.add_argument("--gold", default=str(intake.DEFAULT_GOLD))
    args = parser.parse_args(argv)

    gold = Path(args.gold)
    images = gold.parent / "images"
    try:
        plan = [PlanEntry.parse(p) for p in args.plan] if args.plan else DEFAULT_PLAN
    except ValueError as error:
        print(error, file=out)
        return 2
    if not args.probe and not args.dry_run and not (images / ".git").exists():
        print(f"{images} is not the private photo store (no clone of snapworth-gold there). "
              "Clone it first; listing photos must never land anywhere public.", file=out)
        return 1

    with httpx.Client(timeout=30, follow_redirects=True) as client:
        get = get or client.get
        post = post or client.post
        try:
            token = app_token(*credentials(), post=post)
            if args.probe:
                return probe(token, args.marketplace, get, out)
            with tempfile.TemporaryDirectory() as work:
                print(f"Searching {len(plan)} queries on {args.marketplace}…", file=out)
                drafts = collect(plan, args.per_query, args.marketplace, token, get,
                                 Path(work), None if args.dry_run else images / EVIDENCE_DIR,
                                 out)
                records, skipped = intake.run(drafts, gold, images, convert,
                                              dry_run=args.dry_run)
        except EbayError as error:
            print(f"✗ {error}", file=out)
            return 1
        except intake.IntakeError as error:
            print(f"Nothing written. {len(error.problems)} problem(s):", file=out)
            for problem in error.problems:
                print(f"  - {problem}", file=out)
            return 1

    verb = "Would add" if args.dry_run else "Added"
    print(f"{verb} {len(records)} draft(s)"
          + (f"; {len(skipped)} already in the set" if skipped else "") + ".", file=out)
    by_category: dict[str, int] = {}
    for item in records:
        by_category[item.category] = by_category.get(item.category, 0) + 1
    if by_category:
        print("  " + ", ".join(f"{k} {v}" for k, v in sorted(by_category.items())), file=out)
    if records and not args.dry_run:
        print("Next: python -m eval.review --reviewer <your name>. For each, check the photo "
              f"shows the item named, then approve as '{schema.LabelConfidence.HIGH.value}'.",
              file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
