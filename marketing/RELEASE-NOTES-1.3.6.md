# What's New — SnapWorth 1.3.6

**Version:** 1.3.6 (build 13) · **Previous release:** 1.3.5 (build 12) · **Status:** **approved and live, 2026-09-10**

## Scope

A bug-fix release, and a large one: **every iOS finding from the 2026-09-07
audit** (`docs/AUDIT-2026-09-07.md`) — all 25 — is fixed here. No new features,
no prompt or model change, nothing that changes what a scan costs or returns.

Most of it is invisible. Four things are not, and those are what the What's New
text talks about:

| Visible to a user | Audit id |
|---|---|
| Running out of free scans opens the subscription screen instead of a dead-end error | I-23 |
| The app survives moving to a new iPhone | I-1 |
| Amounts typed with a comma (12,50) are no longer discarded | I-7 |
| A deferred purchase (Ask to Buy, bank approval) says what is happening instead of silently doing nothing | I-2 |

The other 21 are correctness, memory, battery, accessibility and legal-copy
fixes a user only notices as the app being steadier.

The 41 non-iOS findings — backend, website, bot, CI — ship separately and are
not in this build.

---

## Primary — paste into App Store Connect

```
Fixes throughout.

Out of free scans? You now go straight to the subscription screen instead of
an error you can't do anything with.

Moved to a new iPhone? The app now re-verifies itself automatically. Before
this, restoring from a backup could leave scanning stuck until you deleted and
reinstalled.

Prices typed with a comma — 12,50 — now register correctly.

Purchases that need approval, like Ask to Buy, now tell you they're waiting
instead of appearing to do nothing.

Plus a faster, lighter camera and result screen, VoiceOver labels throughout
Thrift Flip, and a Privacy Policy that names every service that handles your
data.
```

### Shorter alternative

```
Running out of free scans now opens the subscription screen instead of an
error. Moving to a new iPhone no longer leaves the app stuck. Prices typed
with a comma register correctly. Plus a lighter camera and result screen,
VoiceOver labels across Thrift Flip, and a clearer Privacy Policy.
```

---

## Copy constraints this text respects

**Nothing about valuation quality.** No prompt, model or pricing change in this
build. The estimate is byte-identical to 1.3.5's.

**The device-migration line is phrased as what the user does, not as our bug.**
"Restoring from a backup could leave scanning stuck" is true and recognisable
to someone it happened to; naming App Attest or the Secure Enclave is not.

**No claim that the privacy policy changed *what* we do.** It did not. It
changed what it *says* — the practice it now discloses (Google, TelemetryDeck,
Telegram) has been in place and disclosed on the website since 3 September.
DeviceCheck went live 7 September and is newly disclosed in both.

**"Faster, lighter" is not a benchmark claim.** It is a fair summary of six
memory and main-thread fixes, none of which was measured on device. Do not
promote it to a number.

---

## What changed, for the record

Grouped as they were fixed, with the audit id for each.

### Quota and the free allowance — I-23, I-3, I-4, I-5

These four are one bug from the user's side and four from ours: the free-scan
count could be wrong, and being wrong ended in an error the user could not act
on.

- **I-23 · a spent allowance was a dead end.** The server returns 402 when the
  daily allowance is gone. The app mapped it correctly to `.quotaExceeded` and
  then did nothing with it — it fell through to a generic "Scan Failed / OK"
  alert. The user hit a wall at the moment of highest purchase intent with no
  way to subscribe from that screen, and the event was filed as
  `scan_failed{reason:no_result}`: the wrong bucket in the one funnel the
  `FREE_SCANS_FIRST_DAY=3` experiment is read against.
- **I-3 · a blip locked the day out.** If the quota backend stumbled while the
  app minted its token, the response's `free_scans_remaining` was read as 0 and
  written to disk, where it stayed for the rest of the day. The field is now
  optional and only written when the server actually sent one.
- **I-4 · local midnight against UTC days.** The client reset the allowance at
  local midnight; the server counts UTC days. Between 00:00 and 03:00 a
  UTC+1..+3 user (which is to say: here) had a spent allowance pass the client's
  own gate, so only the server refused — straight into I-23.
- **I-5 · Thrift Flip never recorded the server's count.** A Thrift Flip scan
  incremented the local counter but not the server's figure, and the Scan tab
  prefers the server's. On a `FREE_SCANS_FIRST_DAY=3` welcome day three Thrift
  Flip scans still left the tab reading "3 left", and the fourth scan 402'd.
  This was the daily route into I-23, not the timezone skew.

### Purchases and the paywall — I-2, I-9, I-24, I-26

- **I-2 · a deferred purchase looked like a completed one.** `purchase`
  returned nothing, so "StoreKit is waiting for a parent to approve this" and
  "the subscription is live" were the same answer. The paywall dismissed on
  both, leaving an Ask to Buy or SCA user with no subscription, no explanation,
  and the same paywall on their next scan. It now returns an outcome, and the
  deferred case says so and stays put.
- **I-9 · an expired subscription kept its Pro chrome.** `Transaction.updates`
  delivers renewals and revocations, but an expiry produces no event at all, so
  a session left open across the expiry date went on painting Pro. The server
  still enforced it, so this was stale UI rather than lost revenue —  but it is
  the paywall the user never saw. Entitlements are re-read on every foreground.
- **I-24 · an inert CTA over em-dashes.** A failed product fetch set the same
  "loaded" flag as a successful one, so the redaction lifted onto price cards
  reading "—" and the CTA sat there disabled-but-identical-looking, with
  nothing said. There is now a real "couldn't load plans" state with a retry,
  and `PrimaryButton` reads `\.isEnabled`, so every disabled button in the app
  now looks disabled.
- **I-26 · the benefits list named none of the gates.** It led with "Full scan
  history", which is not gated at all, and omitted the four features that
  actually open this paywall. Rewritten to the real gates, and a test now
  checks the list against them.

### Input, capture and data — I-7, I-8, I-10, I-16, I-6

- **I-7 · comma decimals were silently discarded.** Every money field wrote
  `Double(newValue)` onto the model on each keystroke. `Double("12,50")` is
  nil, so on a German, French, Romanian or Brazilian keypad the amount vanished
  with no error shown. The guess field was worse: it stripped the comma, so
  "12,50" scored as 1250.
- **I-8 · a crash waiting on iPad.** `maxPhotoDimensions` was hard-coded to
  4032×3024. AVFoundation aborts the process for a value the active format does
  not list, and this iPhone target installs on iPad in compatibility mode,
  where the 8MP cameras of the iPad 6–9, mini 5 and Air 3 never offered it. It
  now asks the format, and stays capped at 12MP so the 48MP Pro cameras are not
  taken up on the offer.
- **I-10 · a failed ledger save reported success.** `try?` plus an
  unconditional success flag meant a SwiftData failure gave a success haptic,
  hid the button and lost the flip.
- **I-16 · My Finds spent scan quota on a card.** The tab re-fetched `/trends`
  on every appearance, and `/trends` shares the same 20-per-hour device rate
  limiter as `/scan` with no Pro exemption — so idly switching tabs consumed
  the user's actual scan budget. Cached for 30 minutes.
- **I-6 · past-dated reminders.** The foreground sync re-scheduled the ledger
  follow-up for every listed item with no future-date guard, so each listing
  older than 14 days re-added a past-dated notification request, and counted a
  `notification_scheduled` event, on every single foreground.

### Memory, main thread and battery — I-11, I-12, I-13, I-14, I-15, I-17

None of these was a correctness bug. All of them cost more than they needed to.

- **I-11.** The full-resolution capture — 48.8MB decoded at 12MP — was held for
  the whole result-sheet lifetime, although the upload (1568px) and stored
  (1024px) copies are both encoded within a second of the shutter. Released as
  soon as the scan returns.
- **I-15.** Thrift Flip held the photo picker's untouched original for the whole
  session to paint a 64pt thumbnail. It keeps a thumbnail now.
- **I-13, I-17.** The My Flips rows and the My Finds grid called
  `UIImage(data:)` in a background task — which decodes nothing. The real
  decode happened on the main thread at first draw, per row and per cell, and
  again on every filter and sort tap. Both now decode and downsample off the
  main actor and keep only the pixels the cell draws.
- **I-12.** The share card rendered at `max(displayScale, 2)`, so a 3× phone
  built an 18.7MB bitmap for an image whose target is 1080×1920 and which every
  destination downsamples again. Capped at 2. The QR footer also built a fresh
  `CIContext` on every render, including each 300ms typing pause; the context
  and the image are both cached now, and the URL is constant.
- **I-14 · the battery one.** A sheet presented over the Scan tab does not fire
  its `onDisappear`, so the full photo-preset capture pipeline — sensor, ISP
  and a 30fps preview — kept running behind the result sheet, the paywall and
  Thrift Flip for as long as the user spent reading or typing. Thrift Flip then
  started a second session on top of it. This was the largest battery cost in
  the app.

### Accessibility — I-25, I-27, I-28

- **I-25.** Thrift Flip had one accessibility modifier against ResultView's 61.
  The three money fields were unlabeled and read the "$" aloud; the marketplace
  chips carried their selected state in colour alone, with a ~31pt target. They
  now mirror the pattern ResultView already had.
- **I-27.** "Add the tag" succeeding was a haptic and nothing else — no visible
  confirmation, no VoiceOver announcement, and the estimate may not visibly
  move at all.
- **I-28.** Onboarding's Skip — the only way out of onboarding — the Regenerate
  button and the feedback chips were all under Apple's 44pt minimum.

### Legal and privacy — I-18, I-19, I-20, I-21, I-22

- **I-18 · the shipped policy said data was not shared.** The website policy
  gained a Service Providers section on 3 September; the in-app copy did not,
  and still read *"We do not sell, rent, or share your photos or device
  identifier with third parties."* For a week the app said that on the screen
  the paywall links to, while photos went to Google on every scan. Both copies
  now name Google/Gemini, TelemetryDeck, Telegram and Apple (DeviceCheck).
- **I-19 · the analytics opt-out did not stop the SDK.** Turning off "Share
  anonymous analytics" silenced our own events, but TelemetryDeck emits its own
  session and install signals — each carrying a per-device hashed identifier —
  from inside the SDK at launch and on every foreground, gated only by a
  configuration flag we never set. The toggle now sets it, immediately.
- **I-20 · the privacy manifest had the right categories with the wrong
  reasons.** File timestamps were declared under the reason reserved for a
  third-party SDK wrapping those APIs; the app-group `UserDefaults` suite the
  widget reads was missing its reason entirely. Nothing asserted reason codes —
  only data types — which is how it went unnoticed.
- **I-21.** `StoreProtection`'s doc comment claimed to cover item photos. It
  does not: those are external-storage blobs outside the store file. No
  regression — they sit at the platform default, as the store did before that
  type existed — but the comment was wrong.
- **I-22.** Settings advertised "3 free scans a day" against a compiled-in
  allowance of 1. True only on a `FREE_SCANS_FIRST_DAY=3` welcome day, wrong
  every day after — which is exactly the window the experiment measures. It is
  derived from the allowance now.

### Auth — I-1

**A new iPhone could leave the app dead.** The App Attest key **id** lives in
`UserDefaults`, which iCloud backup and Quick Start restore onto a new device.
The Secure Enclave **key** it names is hardware-bound and does not migrate. The
new phone was therefore forced to mint a credential, and failed client-side
before any request left the device — then told the user to reinstall, which was
the only thing that actually worked.

Now the stale id is discarded and the device re-attests. Narrowly: an id the
Enclave has no key for recovers; a DeviceCheck outage does not, because
throwing away a working key on a transient failure costs a pointless
attestation and a new server record.

---

## Testing on a device — required before submitting

The release is broad, so the pass is broader than 1.3.5's.

- **The paywall path (I-23).** On a free account, spend the day's allowance,
  then scan again. You should land on the subscription screen, not an alert.
  With `FREE_SCANS_FIRST_DAY=3` live, that is the fourth scan of the day.
- **Thrift Flip end to end (I-5, I-7, I-10, I-15, I-25).** Scan an item, read a
  price tag, type a resale price **with a comma** — `12,50` — check the verdict
  uses 12.50 and not 1250 or nothing, switch marketplace, save to My Flips.
- **Money fields on the result sheet (I-7).** Type `12,50` into "What did you
  pay?" and confirm the share card's multiple reflects it.
- **The camera behind a sheet (I-14).** Scan, leave the result sheet open for a
  minute, and confirm the phone is not warming. Dismiss it and confirm the
  preview comes back live.
- **My Finds and My Flips scrolling (I-13, I-17).** With 20+ finds, scroll and
  tap through the filters. Should be smooth; the thumbnails may now appear a
  frame later, which is the fix working.
- **VoiceOver on Thrift Flip (I-25).** Turn VoiceOver on and swipe through the
  inputs card. Each money field should announce its own name and value; the
  selected marketplace chip should announce as selected.
- **Settings → Privacy Policy (I-18).** Scroll to Service Providers. Google,
  TelemetryDeck, Telegram and Apple should all appear, and the date should read
  September 9, 2026.
- **Settings → the plan row (I-22).** On a free account it should say "1 free
  scan a day", not 3.
- **Analytics opt-out (I-19).** Turn it off in Settings. Nothing to see in the
  app; noted here so it is exercised at least once.
- **I-1 and I-8 cannot be tested here.** I-1 needs a second iPhone and a real
  backup restore; I-8 needs an 8MP iPad. Both ship covered by unit tests on the
  decision rule and unexercised on that hardware. Worth knowing rather than
  pretending otherwise.

## Pre-submit checklist — done

- [x] **1.3.5 approved and live** — was live 2026-09-06.
- [x] Xcode: **1.3.6 (13)**, verified in `project.pbxproj` (all four entries).
- [x] Scheme plain **SnapWorth**.
- [x] Test target — CI ran it: 312 tests, 0 failures, Xcode 26.3 on iOS 26.2.
- [x] What's New pasted; subtitle, keywords and screenshots unchanged from 1.3.5.
- [x] **Approved 2026-09-10.**
- [x] The 41 non-iOS findings shipped separately on 2026-09-09 (PR #119) and
      are live on the backend, bot and website.

## After approval — open

- [ ] **Read the `FREE_SCANS_FIRST_DAY` funnel from 2026-09-10 to 09-24.** The
      clean window opens now, not on 09-07 when the flag was armed: until I-23
      shipped, a spent allowance was filed as `scan_failed{reason:no_result}`,
      so 09-07 to 09-09 undercounts limit hits and overcounts failures by the
      same events. Approval is not installation, so the first days are a blend
      that shifts clean as people update — weight the back half if it is close.
- [ ] **Correct the App Store description.** It still lists "Full scan history"
      as a Pro benefit. History is not gated — `HistoryView` has no `isPro`
      check — and the app's own paywall (I-26) and the website's pricing cards
      both dropped the claim on 09-09. The description is the last place it
      survives. It needs no build, so it can be edited in App Store Connect
      whenever.
- [ ] **I-1 and I-8 remain unexercised on hardware.** A backup restored onto a
      second iPhone, and an 8MP iPad. Both ship covered by unit tests on the
      decision rule and by nothing else. Not a blocker; worth knowing if a
      report ever comes in from either.
