#!/usr/bin/env python3
"""The homepage's Thrift Flip fees must be the app's fees.

The calculator on index.html says "the fee rates are the ones the app actually
uses", and its FEES object is a hand copy of `MarketplaceFees.defaults` in
ios/SnapWorth/Models/MarketplaceFees.swift. The comment above FEES asks whoever
changes a rate in the app to change it here too. Nothing checked that, so a
missed edit would leave the site's calculator disagreeing with the app, on the
one number both call the real one.

Checked, per marketplace:
- the same set of marketplaces in the app's table, in FEES, and as calculator
  chips (a FEES entry without a chip is a rate nobody can select);
- percentage, fixed fee, and the low-price flat charge, compared exactly;
- the note the calculator prints under the arithmetic names those figures, or
  says "no seller fee" when there are none;
- the prose footnote under the calculator names the same figures;
- each generated /fees page (#228): its calculator's data attributes are the
  app's rate, its text names the app's figures, and its worked examples are
  the app's rule applied to their prices.

FEES is evaluated with node rather than parsed with a regex, so what is
compared is the object the page actually runs.

Run: python3 website/seo/check_fees.py   (needs node on PATH)
"""
from __future__ import annotations

import html
import json
import pathlib
import re
import shutil
import subprocess
import sys
from decimal import Decimal

HERE = pathlib.Path(__file__).resolve().parent
WEBSITE = HERE.parent
REPO = WEBSITE.parent
SWIFT = REPO / "ios/SnapWorth/Models/MarketplaceFees.swift"
INDEX = WEBSITE / "index.html"

AMOUNT = r'(?:Decimal\(string:\s*"([^"]+)"\)!|([0-9.]+))'


def percent(fraction: Decimal) -> str:
    # 0.1325 -> "13.25%", 0.20 -> "20%", 0.033 -> "3.3%"
    return f"{(fraction * 100).normalize():f}%"


def dollars(value: Decimal) -> str:
    # $0.40 and $2.95 as the page writes them; a threshold like $15 has no cents.
    return f"${value:.2f}" if value != value.to_integral() else f"${value:.0f}"


class Fee:
    def __init__(self, pct: Decimal, fixed: Decimal,
                 flat: tuple[Decimal, Decimal] | None) -> None:
        self.pct, self.fixed, self.flat = pct, fixed, flat

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, Fee) and self.pct == other.pct
                and self.fixed == other.fixed and self.flat == other.flat)

    def __repr__(self) -> str:
        flat = (f", or {dollars(self.flat[1])} under {dollars(self.flat[0])}"
                if self.flat else "")
        return f"{percent(self.pct)} + {dollars(self.fixed)}{flat}"


def amount(match: re.Match[str], group: int) -> Decimal:
    return Decimal(match.group(group) or match.group(group + 1))


def app_fees() -> dict[str, Fee]:
    source = SWIFT.read_text(encoding="utf-8")
    start = source.find("static let defaults: [Marketplace: MarketplaceFee] = [")
    if start < 0:
        sys.exit(f"MarketplaceFees.defaults not found in {SWIFT.relative_to(REPO)}")
    end = source.find("\n    ]\n", start)
    block = re.sub(r"//[^\n]*", "", source[start:end])

    fees: dict[str, Fee] = {}
    for entry in re.finditer(r"\.(\w+):\s*MarketplaceFee\(", block):
        # The arguments, by matching parentheses: `.init(…)` nests inside.
        depth, i = 1, entry.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(block[i], 0)
            i += 1
        args = block[entry.end():i - 1]
        pct = re.search(rf"sellingFeePercent:\s*{AMOUNT}", args)
        fixed = re.search(rf"fixedFee:\s*{AMOUNT}", args)
        flat = re.search(rf"lowPriceFlatFee:\s*\.init\(below:\s*{AMOUNT},\s*fee:\s*{AMOUNT}\)", args)
        if not pct or not fixed:
            sys.exit(f"could not read the fee for .{entry.group(1)}: {args.strip()}")
        fees[entry.group(1)] = Fee(
            amount(pct, 1), amount(fixed, 1),
            (amount(flat, 1), amount(flat, 3)) if flat else None)
    if len(fees) < 5:
        sys.exit(f"read only {len(fees)} fees from MarketplaceFees.defaults — "
                 "has the table changed shape?")
    return fees


def site_fees(page: str) -> dict[str, dict]:
    no_fee = re.search(r"var NO_FEE = [^\n]*;", page)
    table = re.search(r"var FEES = \{.*?\n  \};", page, re.S)
    if not no_fee or not table:
        sys.exit("FEES or NO_FEE not found in index.html")
    script = f"{no_fee.group(0)}\n{table.group(0)}\nprocess.stdout.write(JSON.stringify(FEES));"
    result = subprocess.run(["node", "-e", script], capture_output=True,
                            text=True, check=False)
    if result.returncode != 0:
        sys.exit(f"node could not evaluate FEES:\n{result.stderr}")
    return json.loads(result.stdout, parse_float=Decimal, parse_int=Decimal)


def figures(fee: Fee) -> list[str]:
    out = []
    if fee.pct:
        out.append(percent(fee.pct))
    if fee.fixed:
        out.append(dollars(fee.fixed))
    if fee.flat:
        out += [dollars(fee.flat[1]), dollars(fee.flat[0])]
    return out


def fee_page_problems(app: dict[str, Fee]) -> list[str]:
    """The /fees pages against the app's table (#228)."""
    from build_fees import PUBLISHED, fee_on
    problems: list[str] = []
    folder = WEBSITE / "fees"
    found = {p.stem for p in folder.glob("*.html")} if folder.is_dir() else set()
    if missing := sorted(set(PUBLISHED) - found):
        problems.append(f"fee pages not built: {', '.join(missing)} (run build_seo.py)")
    if extra := sorted(found - set(PUBLISHED)):
        problems.append(f"fee pages not in build_fees.PUBLISHED: {', '.join(extra)}")
    for key in sorted(found & set(PUBLISHED)):
        page = (folder / f"{key}.html").read_text(encoding="utf-8")
        where = f"fees/{key}.html"
        if key not in app:
            problems.append(f"{where}: {key} is not in MarketplaceFees.defaults")
            continue
        fee = app[key]
        calc = re.search(r'id="fee-calc"([^>]*)>', page)
        attrs = dict(re.findall(r'data-([\w-]+)="([^"]*)"', calc.group(1))) if calc else {}
        flat = ((Decimal(attrs["flat-below"]), Decimal(attrs["flat-fee"]))
                if "flat-below" in attrs else None)
        try:
            web = Fee(Decimal(attrs["pct"]), Decimal(attrs["fixed"]), flat)
        except (KeyError, ArithmeticError):
            problems.append(f"{where}: the calculator carries no readable rate")
            continue
        if web != fee:
            problems.append(f"{where}: the app charges {fee!r}, the page's calculator {web!r}")
        text = html.unescape(re.sub(r"<[^>]+>", " ", page))
        if missing := [f for f in figures(fee) if f not in text]:
            problems.append(f"{where}: does not say {', '.join(missing)}")
        for price, shown_fee in re.findall(
                r"<tr><td>\$([\d,.]+)</td><td class='val'>\$([\d,.]+)</td>", page):
            want = fee_on(fee, Decimal(price.replace(",", "")))
            if Decimal(shown_fee.replace(",", "")) != want:
                problems.append(f"{where}: the example at ${price} shows a ${shown_fee} "
                                f"fee; the app's rule gives ${want}")
    return problems


def main() -> int:
    if not shutil.which("node"):
        print("node is not on PATH")
        return 1

    page = INDEX.read_text(encoding="utf-8")
    app = app_fees()
    site = site_fees(page)
    chips = set(re.findall(r'class="tf-chip" data-mp="(\w+)"', page))
    foot = re.search(r'<div class="tf-foot-note">(.*?)</div>', page, re.S)
    footnote = html.unescape(re.sub(r"<[^>]+>", "", foot.group(1))) if foot else ""
    problems: list[str] = []

    for name, where in ((set(site), "the FEES object"), (chips, "the calculator chips")):
        if missing := sorted(set(app) - name):
            problems.append(f"in the app but not in {where}: {', '.join(missing)}")
        if extra := sorted(name - set(app)):
            problems.append(f"in {where} but not in the app: {', '.join(extra)}")

    for key in sorted(set(app) & set(site)):
        entry = site[key]
        flat = ((entry["flatBelow"], entry["flatFee"])
                if entry.get("flatBelow") else None)
        web = Fee(entry["pct"], entry["fixed"], flat)
        if web != app[key]:
            problems.append(f"{key}: the app charges {app[key]!r}, the site {web!r}")
            continue
        note = entry["note"]
        wanted = figures(app[key])
        if wanted:
            missing = [f for f in wanted if f not in note]
            if missing:
                problems.append(f"{key}: the calculator's note {note!r} does not "
                                f"say {', '.join(missing)}")
            missing = [f for f in wanted if f not in footnote]
            if missing:
                problems.append(f"{key}: the footnote under the calculator does "
                                f"not say {', '.join(missing)}")
        else:
            if "no seller fee" not in note:
                problems.append(f"{key}: charges nothing, but its note is {note!r}")
            if entry["name"] not in footnote:
                problems.append(f"{key}: charges nothing, and the footnote under "
                                f"the calculator does not name {entry['name']!r}")

    problems += fee_page_problems(app)

    for problem in problems:
        print(problem)
    print(f"{len(app)} marketplaces compared with MarketplaceFees.defaults, "
          f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
