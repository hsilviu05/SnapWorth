/* Flip math shared by the homepage's Thrift Flip calculator and the /fees
 * pages (#228): one copy of the fee rule and of the money parser, not one
 * per page.
 *
 * `feesOn` mirrors MarketplaceFee.fees(on:) in the app. The rates themselves
 * stay with each page (index.html's FEES, each fee page's data attributes),
 * and website/seo/check_fees.py holds every one of them to
 * ios/SnapWorth/Models/MarketplaceFees.swift. `num` is checked against the
 * app's MoneyInput test cases by website/seo/check_money_parsers.py.
 */
(function (root) {
  "use strict";

  /* Mirrors MarketplaceFee.fees(on:) — a marketplace with a low-price flat
     charge pays that and nothing else below the threshold. A zero resale pays
     nothing at all, so an empty box doesn't read as an instant loss. */
  function feesOn(fee, resale) {
    if (resale <= 0) return 0;
    if (fee.flatBelow && resale < fee.flatBelow) return fee.flatFee;
    return resale * fee.pct + fee.fixed;
  }

  /* Accepts "$18", "18 usd", "18", and — the reason this is not a one-liner —
     "12,50". With both separators present the last one is the decimal. With
     only one kind, exactly three digits after the last one make it a
     thousands separator, whichever mark it is: "12,50" and "12.50" are twelve
     fifty, "$1,250" and "1.250" are one thousand two hundred and fifty.

     This used to be `replace(/[^0-9.]/g, "")`, which deleted the comma and
     read "12,50" as 1250 — so a visitor in France, Germany, Romania or Brazil
     typing their own decimal separator was told "Skip it, −$1187" on a find
     worth flipping, on a calculator this page says is the app's. The app has
     parsed it correctly since MoneyInput shipped.

     Then the three-digit test was applied to the comma only, and a lone point
     was always a decimal: "1.250", how a German visitor writes the price, was
     1.25. The app had the same asymmetry and fixed it in da0b242; this copy
     and /guess kept it until the check below existed.

     Third copy of this rule: ios MarketplaceFees.MoneyInput and
     website/seo/build_guess.py's parse() are the others. They must agree, and
     website/seo/check_money_parsers.py runs the app's MoneyInput test cases
     through both web copies in CI.

     Negative and non-finite input still reads as 0 rather than throwing —
     render() assumes a number, so an empty Shipping field must not be null. */
  function num(v) {
    var kept = String(v).replace(/[^0-9.,]/g, "");
    if (!/[0-9]/.test(kept)) return 0;
    var lastComma = kept.lastIndexOf(","), lastDot = kept.lastIndexOf(".");
    var last = Math.max(lastComma, lastDot);
    var cut = -1;
    if (lastComma >= 0 && lastDot >= 0) cut = last;
    else if (last >= 0) cut = (kept.length - last - 1) === 3 ? -1 : last;
    var out = "";
    for (var i = 0; i < kept.length; i++) {
      var c = kept.charAt(i);
      if (c === "," || c === ".") { if (i === cut) out += "."; } else out += c;
    }
    var n = parseFloat(out);
    return isFinite(n) && n > 0 ? n : 0;
  }

  function money(n) { return (n < 0 ? "−$" : "$") + Math.abs(n).toFixed(2); }

  root.SnapFlip = { feesOn: feesOn, num: num, money: money };
})(window);
