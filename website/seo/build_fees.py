#!/usr/bin/env python3
"""Generate the marketplace fee pages: /fees/<marketplace> (#228).

"<marketplace> fee calculator" is searched by exactly the people SnapWorth is
for, and the app already carries the fee table those searches want, with a
source for every rate (ios/SnapWorth/Models/MarketplaceFees.swift). These pages
publish it: the rule in words, worked examples at three prices, a small
calculator, the source, and the date the rate was last checked.

**Every figure comes from the app's table.** The rates are read with
`check_fees.app_fees()`, which parses the Swift file, and the words are built
from those numbers, so a page cannot state a rate the app does not use. The
calculator's arithmetic is `SnapFlip.feesOn` in /flip.js, the homepage's own
rule, not a copy. `check_fees.py` holds the generated pages to the table too.

**A page is published only once its rate has been re-checked** against the
marketplace's own page (`PUBLISHED`, with the date). A fee page repeats its
rate on the one page built to rank for it, so a stale rate does the most harm
here. Marketplaces with no seller fee get no page: one sentence is thin
content, and the homepage calculator already covers them.

Built by `build_seo.py` (which also writes the sitemap); run that, not this.
"""
from __future__ import annotations

import html
import json
import pathlib
from decimal import Decimal

from campaigns import app_store
from check_fees import Fee, app_fees, dollars, percent

ROOT = pathlib.Path(__file__).resolve().parents[1]          # website/
OUT = ROOT / "fees"
SITE = "https://www.snapworth.eu"

# Published pages: the rate re-checked against the source, and when. Add a
# marketplace here only after reading its own fee page on the day.
#
# eBay is held back. On 2026-10-06 its help page would not load, and current
# third-party guides cite 13.6% for most categories, with $0.30 per order up
# to $10 and $0.40 above, against the app's 13.25% + $0.40. Confirm on eBay's
# page, fix MarketplaceFees.swift first if the app is stale, then add it here.
PUBLISHED: dict[str, dict] = {
    "poshmark": dict(
        name="Poshmark", checked="2026-10-06",
        source="https://poshmark.com/fees", source_name="Poshmark's fee page",
        extra="The buyer pays for the prepaid shipping label, so shipping is not "
              "part of the fee.",
        calc_note=None),
    "mercari": dict(
        name="Mercari", checked="2026-10-06",
        source="https://www.mercari.com/us/help_center/article/169",
        source_name="Mercari's help center",
        extra="Since 6 January 2025 there is no separate payment processing "
              "fee, and the fee is charged on the item price plus any shipping "
              "the buyer pays.",
        calc_note="The calculator applies the fee to the sale price. If your "
                  "buyer pays shipping, Mercari takes 10% of that too."),
    "depop": dict(
        name="Depop", checked="2026-10-06",
        source="https://www.depop.com/sell/fees/", source_name="Depop's fee page",
        extra="Depop dropped its 10% selling fee for US sellers in July 2024. "
              "What remains is payment processing, charged on the item price "
              "and shipping together.",
        calc_note="The calculator applies the processing fee to the sale price. "
                  "On a shipped sale it is 3.3% of the postage higher."),
}

EXAMPLE_PRICES = (Decimal(10), Decimal(40), Decimal(120))


def fee_on(fee: Fee, resale: Decimal) -> Decimal:
    """`MarketplaceFee.fees(on:)`, in Decimal: the same rule as /flip.js."""
    if resale <= 0:
        return Decimal(0)
    if fee.flat and resale < fee.flat[0]:
        return fee.flat[1]
    return (resale * fee.pct + fee.fixed).quantize(Decimal("0.01"))


def rule(name: str, fee: Fee) -> str:
    """The fee in one sentence, from the numbers alone."""
    if fee.flat:
        below, flat = fee.flat
        return (f"{name} takes a flat {dollars(flat)} on sales under {dollars(below)}, "
                f"and {percent(fee.pct)} of the sale price at {dollars(below)} and above.")
    if fee.fixed:
        return (f"{name} takes {percent(fee.pct)} of the sale price plus "
                f"{dollars(fee.fixed)} per sale.")
    return f"{name} takes {percent(fee.pct)} of the sale price."


def money(d: Decimal) -> str:
    return f"${d:,.2f}"


def links(here: str) -> str:
    others = "".join(f'<a href="/fees/{k}">{html.escape(v["name"])} fees</a>'
                     for k, v in PUBLISHED.items() if k != here)
    return others


def page(key: str, fee: Fee, style: str, header, footer, analytics: str,
         banner: str) -> str:
    meta = PUBLISHED[key]
    name, e = meta["name"], html.escape
    ct = f"fees_{key}"
    url = f"{SITE}/fees/{key}"
    sentence = rule(name, fee)
    title = f"{name} Fee Calculator: What You Keep From a Sale"
    desc = f"{sentence} Work out your {name} fee and what you keep, with examples."
    rows = "".join(
        f"<tr><td>{money(p)}</td><td class='val'>{money(fee_on(fee, p))}</td>"
        f"<td class='val'>{money(p - fee_on(fee, p))}</td></tr>" for p in EXAMPLE_PRICES)
    flat_attrs = (f' data-flat-below="{fee.flat[0]}" data-flat-fee="{fee.flat[1]}"'
                  if fee.flat else "")
    calc_note = (f"<p class='disclaimer'>{e(meta['calc_note'])}</p>"
                 if meta["calc_note"] else "")
    ld = json.dumps({"@context": "https://schema.org", "@type": "BreadcrumbList",
                     "itemListElement": [
                         {"@type": "ListItem", "position": 1, "name": "Home",
                          "item": f"{SITE}/"},
                         {"@type": "ListItem", "position": 2, "name": f"{name} fees",
                          "item": url}]}, ensure_ascii=False)
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{banner}<title>{e(title)}</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="article"><meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}"><meta property="og:url" content="{url}">
<meta property="og:image" content="{SITE}/og-image.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="/favicon-32.png" sizes="32x32">
<style>{style}
.fee-calc{{background:var(--card);border-radius:16px;padding:22px 24px;margin:20px 0;}}
.fee-calc label{{font-weight:600;display:block;margin-bottom:8px;}}
.fee-calc input{{font:inherit;font-size:20px;width:100%;max-width:220px;padding:10px 14px;border:1px solid var(--border);border-radius:10px;background:var(--surface);color:var(--ink);}}
.fee-out{{display:flex;gap:28px;flex-wrap:wrap;margin-top:16px;font-variant-numeric:tabular-nums;}}
.fee-out div span{{display:block;font-size:13px;text-transform:uppercase;letter-spacing:.5px;color:var(--warm-gray);}}
.fee-out div strong{{font-family:'Fraunces',serif;font-size:28px;color:var(--ink);}}
.fee-out .keep strong{{color:var(--sage-text);}}
.checked{{font-size:14px;color:var(--warm-gray);}}
</style>
<script type="application/ld+json">{ld}</script>
</head><body>
{header(ct)}
<main><div class="wrap">
<nav class="crumbs" aria-label="Breadcrumb"><a href="/">Home</a> › {e(name)} fees</nav>
<h1>{e(name)} fees: what you keep from a sale</h1>
<p class="lede">{e(sentence)} {e(meta['extra'])}</p>

<div class="fee-calc" id="fee-calc" data-mp="{key}" data-pct="{fee.pct}" data-fixed="{fee.fixed}"{flat_attrs}>
<label for="fee-price">Sale price</label>
<input id="fee-price" inputmode="decimal" value="$40" aria-describedby="fee-result">
<div class="fee-out" id="fee-result" aria-live="polite">
<div><span>{e(name)} fee</span><strong id="fee-fee">{money(fee_on(fee, Decimal(40)))}</strong></div>
<div class="keep"><span>You keep</span><strong id="fee-keep">{money(Decimal(40) - fee_on(fee, Decimal(40)))}</strong></div>
</div>
{calc_note}
</div>

<h2>{e(name)} fee examples</h2>
<table><thead><tr><th>Sale price</th><th style="text-align:right">{e(name)} fee</th><th style="text-align:right">You keep</th></tr></thead>
<tbody>{rows}</tbody></table>
<p class="disclaimer">“You keep” is before what you paid for the item and any shipping you cover.</p>

<h2>Is it worth flipping?</h2>
<p>The fee is only half the answer. The <a href="/#thrift-flip">Thrift Flip calculator</a> takes what you paid and shipping too, across nine marketplaces, and tells you whether a find is worth buying. The SnapWorth app runs the same numbers on a photo, and estimates what the item resells for.</p>

<div class="cta"><h3>Know the resale value before you pay the fee</h3>
<p>Snap a photo and SnapWorth's AI estimates a resale range for your item and its condition, then works out your profit after {e(name)}'s fee.</p>
<a href="{e(app_store(ct))}">Download SnapWorth — free</a></div>

<p class="checked">Rate checked {meta['checked']} against <a href="{e(meta['source'])}" rel="nofollow">{e(meta['source_name'])}</a>. Fees change: this page is generated from the same fee table the SnapWorth app uses.</p>

<div class="related"><h2>More</h2>{links(key)}
<a href="/worth">What popular secondhand items resell for</a>
<a href="/guess">Guess the price: test your eye on ten real items</a></div>
</div></main>
{footer()}
<script src="/flip.js"></script>
<script>
(function () {{
  "use strict";
  var box = document.getElementById("fee-calc");
  if (!box || !window.SnapFlip) return;
  var d = box.dataset;
  var fee = {{ pct: parseFloat(d.pct), fixed: parseFloat(d.fixed),
              flatBelow: d.flatBelow ? parseFloat(d.flatBelow) : 0,
              flatFee: d.flatFee ? parseFloat(d.flatFee) : 0 }};
  var input = document.getElementById("fee-price");
  function render() {{
    var price = SnapFlip.num(input.value);
    var f = SnapFlip.feesOn(fee, price);
    document.getElementById("fee-fee").textContent = SnapFlip.money(f);
    document.getElementById("fee-keep").textContent = SnapFlip.money(price - f);
  }}
  input.addEventListener("input", render);
  render();
}})();
</script>
{analytics}</body></html>"""


def build(style: str, header, footer, analytics: str, banner: str) -> list[tuple[str, pathlib.Path]]:
    """Write the published pages; return (url, file) for the sitemap."""
    fees = app_fees()
    OUT.mkdir(parents=True, exist_ok=True)
    written = []
    for key in PUBLISHED:
        if key not in fees:
            raise SystemExit(f"build_fees: {key} is published but not in MarketplaceFees.defaults")
        fee = fees[key]
        if not fee.pct and not fee.fixed and not fee.flat:
            raise SystemExit(f"build_fees: {key} charges nothing; it gets no page")
        path = OUT / f"{key}.html"
        path.write_text(page(key, fee, style, header, footer, analytics, banner),
                        encoding="utf-8")
        written.append((f"{SITE}/fees/{key}", path))
    # A page from a marketplace no longer published must not linger.
    for stale in OUT.glob("*.html"):
        if stale.stem not in PUBLISHED:
            stale.unlink()
    return written
