"""How often a listing draft claims what its scan cannot show (#353, step 1).

`/listing` writes a title and description from a scan's fields alone: item,
brand, category, condition and a price range. Nothing in that request says
the item is authentic or new, so a draft that says "100% authentic" or "new
with tags" on a "Good" item is a claim the user cannot stand behind — and now
that Legit check ships, one that reads like a certificate. Marketplaces pull
listings for it. Nothing checks a draft before it reaches the user.

This measures the rate before anything is built for it. It writes drafts with
the shipping pipeline (`main._listing_prompt`, the same model call and
`_validate_listing`) for a fixed set of synthetic scans across every
marketplace, so German (Kleinanzeigen) and Chinese (Xianyu) drafts are
included. Then it asks TypeSafe's Jev, per draft, two yes/no questions about
those claims, and reports the rate per claim and per marketplace with the
drafts most likely to carry one.

    cd backend
    python -m eval.listing_claims --out claims.json            # ~200 drafts
    python -m eval.listing_claims --repeats 1 --limit 3        # a cheap look

Needs GEMINI_API_KEY and TYPESAFE_API_KEY, exported or in `backend/.env`.
No photo and no user data: the scans below are written here.

`/listing` asks the same questions when LISTING_CLAIM_CHECK is on (step 2,
`listingclaims.py`); whether to turn it on is decided from this run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import listingclaims  # noqa: E402
from eval import identity  # noqa: E402
# The questions and the reading of their answers are the ones `/listing` asks
# when its check is on, so this measures what that check would do.
from listingclaims import CLAIMS, FLAG_THRESHOLD, parse_claims  # noqa: E402

log = logging.getLogger("snapworth.eval.listing_claims")


@dataclass(frozen=True)
class Scan:
    """What `/listing` is sent: a scan's fields, never a photo."""
    item_name: str
    brand: str
    category: str
    condition: str          # main._VALID_CONDITIONS
    low: float
    likely: float
    high: float


#: Synthetic scans weighted toward where an unsupported claim would cost most:
#: designer goods (authenticity) and worn items (condition). None is "new", so
#: any "new" in a draft is above its scan.
SCANS: tuple[Scan, ...] = (
    Scan("Speedy 30 Monogram Canvas Handbag", "Louis Vuitton", "accessories", "used", 350, 480, 620),
    Scan("Air Jordan 1 Retro High OG Chicago", "Nike", "shoes", "good", 180, 240, 320),
    Scan("Box Logo Hoodie", "Supreme", "clothing", "good", 220, 300, 400),
    Scan("Submariner Date Wristwatch", "Rolex", "accessories", "used", 7000, 8500, 10000),
    Scan("Classic Flap Bag", "Chanel", "accessories", "good", 3500, 4500, 5500),
    Scan("501 Original Fit Jeans", "Levi's", "clothing", "used", 18, 28, 40),
    Scan("Better Sweater Quarter-Zip Fleece", "Patagonia", "clothing", "likeNew", 45, 60, 80),
    Scan("Yeezy Boost 350 V2 Zebra", "Adidas", "shoes", "good", 150, 210, 280),
    Scan("AirPods Pro 2nd Generation", "Apple", "electronics", "used", 70, 95, 120),
    Scan("Vintage Band T-Shirt", "Unknown", "clothing", "used", 15, 25, 45),
    Scan("Le Creuset Dutch Oven 5.5 qt", "Le Creuset", "home", "good", 120, 160, 210),
)


def build_request(scan: Scan, title: str, description: str,
                  model: str = identity.MODEL) -> dict[str, Any]:
    """One `/v1/systemone` body asking both claims about one draft."""
    import main
    return listingclaims.build_request(
        scan.item_name, scan.brand, scan.category,
        main._CONDITION_LABEL.get(scan.condition, "Good"), title, description, model)


def summarise(rows: list[dict[str, Any]], top: int = 10) -> dict[str, Any]:
    """Rates per claim, overall and per marketplace, and the `top` drafts most
    likely to make either claim. A draft whose claim went unanswered is left
    out of that claim's rate, never counted as clean; a draft the model never
    wrote is counted as failed and nowhere else. Pure."""
    failed = sum(1 for r in rows if r.get("failed"))
    rows = [r for r in rows if not r.get("failed")]

    def rate(subset: list[dict[str, Any]], claim: str) -> dict[str, Any]:
        judged = [r["claims"][claim] for r in subset if r["claims"].get(claim) is not None]
        flagged = sum(1 for p in judged if p >= FLAG_THRESHOLD)
        return {"flagged": flagged, "n": len(judged),
                "rate": flagged / len(judged) if judged else None,
                "unjudged": len(subset) - len(judged)}

    by_marketplace = {
        m: {claim: rate([r for r in rows if r["marketplace"] == m], claim) for claim in CLAIMS}
        for m in sorted({r["marketplace"] for r in rows})}

    def worst(row: dict[str, Any]) -> float:
        return max((p for p in row["claims"].values() if p is not None), default=-1.0)

    flagged = sorted((r for r in rows if worst(r) >= FLAG_THRESHOLD), key=worst, reverse=True)
    return {
        "drafts": len(rows),
        "failed_drafts": failed,
        "fallback_drafts": sum(1 for r in rows if r.get("fallback")),
        "overall": {claim: rate(rows, claim) for claim in CLAIMS},
        "by_marketplace": by_marketplace,
        "top": flagged[:top],
    }


async def write_draft(scan: Scan, marketplace: str) -> tuple[str, str, bool] | None:
    """A draft from the shipping `/listing` pipeline: (title, description,
    whether it is the deterministic fallback rather than the model's), or None
    when the model was unavailable — where `/listing` answers 502, not a draft."""
    import aiconfig
    import main

    req = main.ListingRequest(
        item_name=scan.item_name, brand=scan.brand, category=scan.category,
        condition=scan.condition, price_low_usd=scan.low,
        price_likely_usd=scan.likely, price_high_usd=scan.high, marketplace=marketplace)
    try:
        raw, _ = await main._generate_with_retry(
            main._listing_prompt(req), label="listing", record_health=False,
            max_tokens=aiconfig.LISTING_MAX_OUTPUT_TOKENS,
            thinking_budget=aiconfig.TEXT_THINKING_BUDGET)
        result = main._validate_listing(main._extract_json(raw), req)
        return result.title, result.description, False
    except (aiconfig.ModelBlocked, json.JSONDecodeError, ValueError):
        # What `/listing` serves in these cases, so it is what the user sees.
        fallback = main._fallback_listing(req)
        return fallback.title, fallback.description, True
    except aiconfig.ModelUnavailable as exc:
        log.warning("draft not written (%s, %s): %s", scan.item_name, marketplace,
                    type(exc).__name__)
        return None


async def run(scans: tuple[Scan, ...], marketplaces: list[str], repeats: int,
              concurrency: int) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(max(1, concurrency))
    client = identity.make_client()

    async def one(scan: Scan, marketplace: str) -> dict[str, Any]:
        row: dict[str, Any] = {"item": scan.item_name, "brand": scan.brand,
                               "condition": scan.condition, "marketplace": marketplace}
        async with semaphore:
            draft = await write_draft(scan, marketplace)
            if draft is None:
                return row | {"failed": True}
            title, description, fallback = draft
            body = await identity.post(client, build_request(scan, title, description))
        return row | {"title": title, "description": description,
                      "fallback": fallback, "claims": parse_claims(body)}

    try:
        jobs = [one(scan, m) for _ in range(max(1, repeats))
                for scan in scans for m in marketplaces]
        return list(await asyncio.gather(*jobs))
    finally:
        await client.aclose()


def _format(report: dict[str, Any]) -> str:
    def pct(entry: dict[str, Any]) -> str:
        if entry["rate"] is None:
            return "n/a"
        return f"{entry['rate'] * 100:5.1f}% ({entry['flagged']}/{entry['n']})"

    lines = ["", "═══ Listing draft claims ═══",
             f"{report['drafts']} drafts, {report['fallback_drafts']} from the fallback, "
             f"{report['failed_drafts']} not written (model unavailable)", ""]
    for claim, entry in report["overall"].items():
        lines.append(f"  {claim:<13} {pct(entry)}"
                     + (f"  unjudged {entry['unjudged']}" if entry["unjudged"] else ""))
    lines += ["", "By marketplace"]
    for marketplace, claims in report["by_marketplace"].items():
        lines.append(f"  {marketplace:<14} " + "   ".join(
            f"{claim} {pct(entry)}" for claim, entry in claims.items()))
    if report["top"]:
        lines += ["", "Most likely to carry a claim"]
    for row in report["top"]:
        probabilities = ", ".join(f"{c}={p:.2f}" for c, p in row["claims"].items()
                                  if p is not None)
        lines.append(f"  [{row['marketplace']}] {row['title']}  ({probabilities})")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    import main as app

    parser = argparse.ArgumentParser(description="Measure unsupported claims in listing drafts")
    parser.add_argument("--repeats", type=int, default=2,
                        help="drafts per scan and marketplace (default 2: ~200 drafts)")
    parser.add_argument("--limit", type=int, help="use only the first N scans")
    parser.add_argument("--marketplaces",
                        help="comma-separated, default every marketplace /listing supports")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--out", help="write every draft, its answers and the summary here")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    identity.load_env()
    missing = [name for name in ("GEMINI_API_KEY", identity.API_KEY_ENV)
               if not os.environ.get(name, "").strip()]
    if missing:
        print(f"needs {' and '.join(missing)}, exported or in backend/.env")
        return 2

    marketplaces = (sorted(app.SUPPORTED_MARKETPLACES) if not args.marketplaces
                    else [m.strip().lower() for m in args.marketplaces.split(",")])
    unknown = sorted(set(marketplaces) - app.SUPPORTED_MARKETPLACES)
    if unknown:
        parser.error(f"unknown marketplace(s): {', '.join(unknown)}")
    scans = SCANS[: args.limit] if args.limit else SCANS

    rows = asyncio.run(run(scans, marketplaces, args.repeats, args.concurrency))
    report = summarise(rows)
    print(_format(report))
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": report, "drafts": rows},
                                             indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
