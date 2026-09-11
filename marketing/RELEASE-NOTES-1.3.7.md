# What's New — SnapWorth 1.3.7

## Scope

Five iOS pull requests since 1.3.6: #133, #140, #141, #142, #143. Everything
else that shipped on 2026-09-11 is backend and already live — App Store Server
Notifications, the operator bot, the audit fixes — and none of it is in this
build.

Three of the five are visible to a user. One is preventive and invisible today.
One is legal copy.

| PR | Closes | Visible? |
|----|--------|----------|
| #141 | #136 | **Yes** — price-tag scanning by camera |
| #133 | — | **Yes** — condition grading, and therefore price |
| #140 | #138 | **Yes** — four surfaces disagreeing about value |
| #142 | #137 | No — paid introductory-offer copy, preventive |
| #143 | — | Barely — Terms of Service wording |

---

## Primary — paste into App Store Connect

```
Fixes throughout.

Scanning a price tag with the camera now reads it reliably. Photos taken in
portrait were being handed to the text recogniser sideways, so the price often
didn't register.

Items described as clean — "no stains, no tears" — are no longer graded as
damaged. That was quietly pulling their estimated value down.

The value shown on the result screen, in My Finds, on the widget and on a
shared badge now always agree. After changing an item's condition, some of
those kept showing the original estimate.

Plus clearer subscription terms.
```

### Shorter alternative

```
Price tags scanned with the camera now read reliably — portrait photos were
being read sideways. Items described as clean are no longer graded as damaged,
which was lowering their estimate. And the value on the result screen, in My
Finds, on the widget and on a shared badge now always agree.
```

---

## What changed, for the record

### #141 — the price-tag reader was a quarter turn out (closes #136)

`PriceTagOCR` handed Vision a raw `cgImage`. `VNImageRequestHandler(cgImage:options:)`
assumes `.up`, and a `UIImage` from the camera carries its rotation in
`imageOrientation` — the pixel buffer is never turned. Every camera capture
therefore reached the text recogniser rotated. Photo-library images that had
already been normalised were the only ones that worked.

Passing the orientation also repairs the "tallest line wins" heuristic for
free: once told, Vision reports `boundingBox` in the oriented space.

### #133 — negated prose was keyword-matched

`prompts.py` teaches the model to write notes shaped like *"Light pilling at
cuffs and collar; no stains or holes visible"* — its own canonical example.
`Condition.inferred(from:)` substring-matched `"stain"` with no notion of
negation and graded that item `.used`, which carries a 0.78 multiplier.

Verified against the real matcher rather than reasoned about:

```
"Light pilling at cuffs and collar; no stains or holes visible"  ->  .used
"Great condition, no flaws"                                      ->  .used
"Clean, no tears"                                                ->  .used
```

### #140 — four surfaces disagreed about one item (closes #138)

Some readers used the raw AI baseline (`valueLow`/`valueHigh`), others the
condition-adjusted value. On anything the user had re-graded, the widget total,
the share badge and the portfolio each said something the result sheet did not
— and the widget showed a haul total and `lastItemRange` inches apart, on
different bases, inside the same view.

### #142 — a paid introductory offer read as free (closes #137)

Not on screen today; the configured product is a genuine 3-day free trial. It
becomes visible the moment the offer is changed in App Store Connect, which
needs no release. See the PR.

### #143 — the Terms named an offer only Apple controls

Both copies — in-app and the served `/terms` page — stated "A 3-day free trial
is available for new yearly subscribers" as flat fact. They now state the rule
instead of the instance.

---

## Testing on a device — required before submitting

The first three are the release. Each one needs a real device; none can be
confirmed in the simulator or by the test suite.

- [ ] **Price tag, camera, portrait.** Photograph a real price tag holding the
      phone upright. The price should register. This is the fix with the widest
      blast radius — it affected every camera capture.
- [ ] **Price tag, camera, landscape.** Same tag, phone rotated. Should also
      read.
- [ ] **Clean item.** Scan something undamaged. When the notes say "no stains"
      or "no tears", the condition chip must **not** read *Used*.
- [ ] **Re-grade an item**, then check all four surfaces show the same value:
      result screen, My Finds total, the medium widget, and a shared badge.
- [ ] **Paywall.** Headline reads "Try SnapWorth free for 3 days", CTA reads
      "Start Free Trial". Unchanged from 1.3.6 — assert it did not regress.
- [ ] **Settings → Terms of Service → Subscriptions.** No longer names a
      3-day trial.

---

## Pre-submit checklist

- [ ] `MARKETING_VERSION` bumped 1.3.6 → 1.3.7 (three places in
      `project.pbxproj`) and the build number incremented
- [ ] Device testing above, all six
- [ ] What's New pasted from this file
- [ ] **App description replaced** from `marketing/app_store_listing.md`, and
      the ⚠️ warning block at the top of that file deleted once it is in
- [ ] Screenshots unchanged from 1.3.5 — see `SCREENSHOT-COMPLIANCE.md`
- [ ] Subtitle and keywords unchanged

## Not in this build

App Store Server Notifications (#144, #145, #146) are backend-only and already
live in production, verified end to end against Apple on 2026-09-11. They need
no app release and are not mentioned in What's New — a user would not know what
they were reading.
