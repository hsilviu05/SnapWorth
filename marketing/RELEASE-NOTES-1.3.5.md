# What's New — SnapWorth 1.3.5

**Version:** 1.3.5 (build 12) · **Previous release:** 1.3.4 (build 11, commit `aff1004`) · **Status:** in development

## Scope so far

| Change | Visible? | PR |
|---|---|---|
| Guess the price — question and reveal share cards, plus the in-app game (#95) | **yes — headline** | #100 |
| Why this price — the price ladder, confidence reasons, value drivers and "sharpen this estimate", Pro-gated (#87) | **yes** | #101 |
| In the Simulator, scan errors say why instead of suggesting a reinstall | developer only | #98 |

Planned for this release, not yet merged: Trending at the thrift (#96), a second photo of the tag (#88).

---

## Primary — paste into App Store Connect (draft)

```
Guess the price.

Turn any find into a story: share the question card with the estimate
hidden, then the reveal. Or play it yourself — type a guess, tap to reveal,
and see how close you were.

Why this price (Pro): every result now shows the four price points — floor,
quick sale, expected, best case — the reasons behind the confidence score,
what drives the value, and how to sharpen the estimate with a better photo.
```

## Copy constraints

Same as 1.3.4: "estimate", never "worth" or "sells for"; nothing about sold
listings or market data; authenticity is an observation about the photo,
never a certification.

## Testing in the Simulator

App Attest does not exist in the Simulator, so against production every scan
fails with "Scanning needs a real iPhone". To exercise the whole flow offline:
Xcode → **Product → Scheme → Edit Scheme…** → Run → Arguments → *Arguments
Passed On Launch* → add `-mock-scans`. Scans then return three canned results
(Patagonia fleece, Levi's 501, Air Max 90) that carry the full "Why this
price" detail, so the result sheet, the Pro panel, Guess the price and the
streak all work. Remove the argument to go back to the live backend. An App
Store build can never receive a launch argument, so this cannot ship on.

## Testing on a device (Xcode → your iPhone)

- Scan anything → the **Why this price** card sits under the value. On a
  Pro account it opens fully; on free it shows the lock and the paywall.
- Under "What did you pay?" → **🎯 Guess the price** → type a guess, tap the
  covered estimate. Try both share buttons; the pair should land in Photos or
  the share sheet as two images.
- Settings → Notifications → **Daily free scan** (free accounts only): turn
  it on, pick a time a minute ahead, background the app.
- Scan on two consecutive days → **🔥 2-day streak** on the Scan tab.

## Pre-submit checklist

- [ ] Archive 1.3.4 first, from commit `aff1004` (`git checkout aff1004`), if it has not been submitted.
- [ ] Xcode: confirm **1.3.5 (12)** in the target's General tab.
- [ ] Run the test target once (`⌘U`).
- [ ] Paste the What's New above; subtitle, keywords and screenshots unchanged from 1.3.4.
