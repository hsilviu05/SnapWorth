# App Store screenshot compliance

**Status:** ✅ The sold-listings blocker is resolved: the live set was
replaced on 2026-09-01 (#35) by `screenshots/store_1..4`, which make no
sold-listings, comps or market-data claim. 1.5.1 replaces those in turn with
the v3 set (#203, `SCREENSHOT-SPEC.md`).
**Still true:** `screenshots/screenshot_1.png` and `screenshot_2.png` remain
in the repo with the false claim below. They are a record, never an upload.

This file is the history of that blocker and the rule it left behind. The
rules every frame follows now are `SCREENSHOT-SPEC.md` §0 and the listing's
*Claims this listing does not make* (`app_store_listing.md`).

---

## The problem (2026-07-28)

Two shipped screenshots made a factual claim about a data source SnapWorth
does not have.

| Asset | Claim on the asset |
|---|---|
| `screenshots/screenshot_1.png` | "AI checks **real sold listings** and gives you an instant valuation." |
| `screenshots/screenshot_2.png` | "• **Real sold listings, not guesses**" (badge) |
| `screenshots/screenshot_2.png` | "See what your item actually sells for **based on recent marketplace data**." |
| `screenshots/screenshot_2.png` | Mock UI reads "**38 sold listings**" |

### What the product actually does

The estimate is the model's: the scan prompt asks for a typical secondhand
resale range from the model's general market knowledge, not from any
marketplace lookup. A comparable-sales engine now exists in
`backend/comps/`, but it runs in shadow mode behind `COMPS_ENABLED`, which
production keeps `false` until a provider grants sold data in writing
(`backend/main.py:204-208`, `backend/comps/shadow.py:22-24`). With it off the
engine is never called, and every result reads **AI estimate**.

`sold_listings_count` — the field behind the retired "38 sold listings" claim —
was a hardcoded `0` kept only so clients below 1.2 could decode the response.
Those installs have aged out and the field was removed from the response
entirely (#49). The name is retired for good: a real comparable-sales count,
when it exists, ships under its own name (see `docs/COMPS-ARCHITECTURE.md`).

The "38" came from the mock fixture in `ScanAPIClient.mockScan()`, which is
what ran when those screenshots were captured with `Config.mockMode = true`.
The fixture has since been zeroed, and `mockMode` stays `false` under a test.
**Screenshots are no longer captured from mocks at all:** since 1.5.1 every
device frame comes from the submitted TestFlight build against production
(`SCREENSHOT-HANDOFF.md` §2), so a canned figure cannot reach the store again.

---

## Why this matters

**App Store Review.** Guideline 2.3.1 requires metadata — explicitly including
screenshots — to accurately reflect the app; 2.3.7 covers screenshot accuracy
specifically. Reviewers read screenshot captions.

**Consumer protection.** SnapWorth operates under `snapworth.eu`. The EU Unfair
Commercial Practices Directive (2005/29/EC) treats a false claim about a
product's characteristics as a misleading action regardless of intent; the US
FTC Act §5 analysis is equivalent. "Real sold listings" is specific,
falsifiable, and material.

**Trust.** The in-app legal copy already says estimates are not guarantees of
actual sale prices (`ios/SnapWorth/Views/LegalView.swift`). Honest legal copy
beside overclaiming marketing copy is the worst combination, because it shows
the discrepancy was known.

---

## Claims

### Safe to make in 1.5.1

- "AI resale estimate"
- "In seconds"
- "Confidence level" — High, Medium or Low: how strongly the photo and the
  identification back the estimate. **Not "confidence score"** and never the
  0–100 number: the listing names a level, and the number is Pro detail
  inside *Why this price* (`app_store_listing.md`, *Confidence as a score or a
  number*)
- "Profit after marketplace fees", before you buy
- "Ready-to-paste listing", with Pro
- "Photos are never stored on our servers"
- "Track what you paid, what it sold for, and what you made"

### Need the comps pipeline live first

- anything containing "sold listings", "comps", "recent sales", "marketplace data"
- "what it actually sells for"
- any specific count of listings, sales, or data points

The app has a caption for a comps-backed result, "Based on recent sales"
(`ScanAPIClient.swift:26`). No screenshot may show it while `COMPS_ENABLED` is
off, and none should be taken of it until a comps-backed estimate is the
normal case rather than the exception.

---

## Before each submission

- [ ] Run `SCREENSHOT-HANDOFF.md` §8's gate on every frame
- [ ] `python3 marketing/build_screenshots.py --check` passes
- [ ] `marketing/app_store_listing*.md` still follow *Claims this listing does
      not make*
