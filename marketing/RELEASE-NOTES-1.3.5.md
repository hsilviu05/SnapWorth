# What's New — SnapWorth 1.3.5

**Version:** 1.3.5 (build 12) · **Previous release:** 1.3.4 (build 11, commit `aff1004`) · **Status:** feature-complete, pending a device test

## Scope

| Change | Visible? | PR |
|---|---|---|
| Guess the price — the estimate is covered until you reveal it, plus question/reveal share cards (#95) | **yes — headline** | #100, #102 |
| Why this price — four price points, confidence reasons, value drivers, "sharpen this estimate", Pro (#87) | **yes** | #101 |
| Add the tag — a second photo of the label re-reads the item, Pro (#88) | **yes** | #104 |
| Trending at the thrift — what everyone scanned this week, on My Finds; averages and notable finds for Pro (#96) | **yes** | #103 |
| Mock-scans scheme, and Simulator scan errors that say why | developer only | #98, #102 |

The backend halves of #96 and #88 are already deployed, so the endpoints are
live before the app that calls them — which is the right order: an old client
never sends `tag` and never calls `/trends`.

---

## Primary — paste into App Store Connect

```
Guess before you look.

Every scan now opens with the price hidden. Take a guess, tap to reveal,
and see how close you were — then share it as a story: the question card
first, the reveal second.

Add the tag (Pro). Photograph the care label, size tag or sole stamp and
SnapWorth re-reads the item with both photos. Size, fabric and style codes
come off the label, so the estimate tightens.

Why this price (Pro). Every result shows the four price points — floor,
quick sale, expected, best case — the reasons behind the confidence score,
what drives the value, and how to sharpen the estimate.

Trending at the thrift. See what everyone is finding this week: the
most-scanned categories and brands, which way each is moving, and — on Pro
— average prices and the week's best finds.
```

### Shorter alternative

```
Every scan now opens with the price hidden — guess first, then reveal, then
share it as a story. Pro adds "Add the tag": photograph the care label and
SnapWorth re-reads the item with both photos for a tighter estimate. Plus
Why this price, and Trending at the thrift.
```

## Copy constraints

Same as 1.3.4: **"estimate"**, never "worth" or "sells for"; nothing about sold
listings, comps or market data; authenticity is an observation about the photo,
never a certification. Trending is described as what *people scanned*, never as
market activity — it counts scans, not sales.

## Testing in the Simulator

App Attest does not exist in the Simulator, so against production every scan
fails with "Scanning needs a real iPhone". To exercise the flow offline, pick
the **SnapWorth (Mock scans)** scheme in the scheme picker (it is the normal
scheme with `-mock-scans` already set; the plain `SnapWorth` scheme is what CI
tests with). Scans then return three canned results carrying the full "Why this
price" detail, `/trends` returns a canned week, and adding a tag returns a
visibly sharpened result. An App Store build can never receive a launch
argument, so this cannot ship on.

## Testing on a device (Xcode → your iPhone) — required before submitting

- **Guess first.** Scan anything: the value card opens covered. Type a guess,
  tap **Reveal the estimate**, check the spring and the verdict line. Reopen
  the same find from My Finds — it must show the number straight away.
- **Add the tag (the one that needs a real camera).** On that fresh result tap
  **Add the tag**, photograph a care label, and confirm the estimate, the
  detail panel and the listing draft update — and that what you paid and the
  ledger status survive. Then force a failure (airplane mode) and confirm the
  original estimate is untouched and the error appears under the button.
- **Why this price.** Pro opens the full panel; a free account shows the lock.
- **Trending at the thrift.** Top of My Finds. It is *absent* on a quiet week —
  that is correct, not a bug.
- Settings → Notifications → **Daily free scan** (free accounts only): turn it
  on, set a time a minute ahead, background the app.
- Share: **Result card** and **Guess-the-price story** from the toolbar share
  button; the story should hand two images to the share sheet.

## Pre-submit checklist

- [ ] **1.3.4 is approved and live** — Apple processes one version at a time.
- [ ] Xcode: confirm **1.3.5 (12)** in the target's General tab.
- [ ] Run the test target once (`⌘U`) on the plain **SnapWorth** scheme.
- [ ] The device pass above, especially Add the tag.
- [ ] Paste the What's New; subtitle, keywords and screenshots unchanged from 1.3.4.
- [ ] After approval: consider `FREE_SCANS_FIRST_DAY=3` in Railway for two weeks,
      and read the App Store search-terms report a week after 1.3.4's metadata landed.
