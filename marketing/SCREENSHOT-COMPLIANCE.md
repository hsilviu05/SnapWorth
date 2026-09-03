# App Store screenshot compliance — BLOCKER before next submission

**Status:** 🔴 Unresolved. Requires regenerating two PNG design assets.
**Owner:** design (cannot be fixed in code — see "Why this isn't a code fix" below).

---

## The problem

Two shipped screenshots make a factual claim about a data source SnapWorth does
not have.

| Asset | Claim on the asset |
|---|---|
| `screenshots/screenshot_1.png` | "AI checks **real sold listings** and gives you an instant valuation." |
| `screenshots/screenshot_2.png` | "• **Real sold listings, not guesses**" (badge) |
| `screenshots/screenshot_2.png` | "See what your item actually sells for **based on recent marketplace data**." |
| `screenshots/screenshot_2.png` | Mock UI reads "**38 sold listings**" |

### What the product actually does

`backend/main.py` — the scan prompt instructs the model to estimate from its own
training knowledge, not from any marketplace lookup:

```
- Estimate the typical secondhand resale range from your general market knowledge —
  reflect what these items usually resell for, not inflated retail or asking prices
```

There is no eBay Browse API call, no Terapeak integration, no scraper, and no
comps table anywhere in `backend/`. A scan is one Gemini vision call.

`sold_listings_count` — the field behind the retired "38 sold listings" claim —
was a hardcoded `0` kept only so clients below 1.2 could decode the response.
Those installs have aged out and the field was removed from the response
entirely (#49). The name is retired for good: a real comparable-sales count,
when it exists, ships under its own name (see `docs/COMPS-ARCHITECTURE.md`).

### Verified scope

Confirmed by source review on 2026-07-28: **no shipped UI surface renders a
sold-listings count.** The in-app experience is honest. The false claim exists
only in these two marketing PNGs.

The "38" in screenshot 2 came from the mock fixture in `ScanAPIClient.mockScan()`,
which is what runs when screenshots are captured (`Config.mockMode = true`).
**That fixture has now been zeroed**, and `ProductionHardeningTests.swift`
asserts the default stays `0`, so the number cannot be re-manufactured by
recapturing screenshots. But the existing PNGs still carry it.

---

## Why this matters

**App Store Review.** Guideline 2.3.1 requires metadata — explicitly including
screenshots — to accurately reflect the app; 2.3.7 covers screenshot accuracy
specifically. Reviewers read screenshot captions. Expect a rejection and a lost
review cycle.

**Consumer protection.** SnapWorth operates under `snapworth.eu`. The EU Unfair
Commercial Practices Directive (2005/29/EC) treats a false claim about a
product's characteristics as a misleading action regardless of intent; the US
FTC Act §5 analysis is equivalent. "Real sold listings" is specific, falsifiable,
and material — it is the reason a user picks SnapWorth over a free image search.

**Trust.** The product's entire proposition is "trust this number." Note that
`ios/SnapWorth/Views/LegalView.swift:49` already states estimates are "not
guarantees of actual sale prices." Honest legal copy plus overclaiming marketing
copy is the worst combination, because it demonstrates the discrepancy was known.

---

## Replacement copy

Do not water the claim down into vagueness. Replace it with a claim that is both
true **and** more differentiating — speed and place are what SnapWorth actually
wins on. eBay's own app has comps; it does not work standing in a Goodwill aisle
in four seconds.

| Asset | Remove | Use instead |
|---|---|---|
| Screenshot 1 badge | "Scan any secondhand item" | *keep — already accurate* |
| Screenshot 1 subhead | "AI checks real sold listings and gives you an instant valuation." | **"Point your camera. Get a resale range in seconds."** |
| Screenshot 2 badge | "• Real sold listings, not guesses" | **"• Instant · On the shelf"** |
| Screenshot 2 subhead | "See what your item actually sells for based on recent marketplace data." | **"An AI resale estimate with an honest confidence read."** |
| Screenshot 2 mock UI | "38 sold listings" chip | **Thrift Flip verdict chip — "+$32 after fees"** |

### Claims that are safe to make today

- "AI resale estimate" / "AI-powered valuation"
- "In seconds" / "instant"
- "Confidence score — see how clearly the AI identified your item"
- "Ready-to-paste listing draft"
- "Photos are never stored on our servers"
- "Track what you paid, listed, and sold for"

### Claims that require building the comps pipeline first

- anything containing "sold listings", "comps", "recent sales", "marketplace data"
- "what it actually sells for"
- any specific count of listings, sales, or data points

---

## Earning the claim properly

The claim is worth having. To make it true, `/scan` needs a real comps path:

1. Gemini identifies brand + model + size (it already does).
2. Query eBay Browse / Marketplace Insights for **sold** items, 90-day window.
3. If ≥5 comps: return the p25–p75 range and set `valuation.source = "comps"`.
4. Otherwise fall back to the model estimate with `valuation.source = "model"`.
5. Label the two differently in the UI: *"Based on 38 sold listings (median $62)"*
   vs *"AI estimate — no recent sales found."*

Cache aggressively — comps for a given normalised item identity change slowly, so
a 24h Redis TTL gives a high hit rate at near-zero marginal cost.

Once `source == "comps"` is real, this becomes the strongest honest claim in the
category, and no competitor built on a raw vision model can follow.

---

## Checklist before next submission

- [ ] Regenerate `screenshot_1.png` with replacement subhead
- [ ] Regenerate `screenshot_2.png` with replacement badge, subhead, and verdict chip
- [ ] Crop both tighter — currently ~40% of each canvas is empty space below the
      device frame, and the App Store gallery preview crops the bottom
- [ ] Re-read every remaining screenshot for comps language
- [ ] Confirm `marketing/app_store_listing.md` stays clean (it currently is)
- [ ] Confirm `website/index.html` carries no comps claim (verified clean 2026-07-28)
