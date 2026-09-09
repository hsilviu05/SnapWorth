# What's New — SnapWorth 1.3.6

**Version:** 1.3.6 (build 13) · **Previous release:** 1.3.5 (build 12) · **Status:** ready to archive

## Scope

A bug-fix release. No new features, nothing to learn, nothing that changes what a
scan costs or returns. Three fixes, all found by the 2026-09-07 audit
(`docs/AUDIT-2026-09-07.md`), all merged in PRs #116 and #117.

| Fix | Visible to a user? | Audit id |
|---|---|---|
| A spent free allowance opens the paywall instead of a "Scan Failed" dead end | **yes** | I-23 |
| The app survives moving to a new iPhone | **yes — but only to the people it was breaking** | I-1 |
| The in-app Privacy Policy names everyone who receives data | only if read — but it is what App Review reads | I-18 |

Two of the three are things a user only notices when they were already broken.
That is the point of the release.

---

## Primary — paste into App Store Connect

```
Fixes.

Out of free scans? You now go straight to the subscription screen instead of
an error you can't do anything with.

Moved to a new iPhone? The app now re-verifies itself automatically. Before
this, restoring from a backup could leave scanning stuck until you deleted and
reinstalled.

The in-app Privacy Policy now lists every service that handles your data.
```

### Shorter alternative

```
Running out of free scans now opens the subscription screen instead of an
error. Moving to a new iPhone no longer leaves the app stuck. The Privacy
Policy now names every service that handles your data.
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

---

## What changed, for the record

### I-23 · a spent allowance was a dead end

The server returns 402 when the daily free allowance is gone. The app mapped it
correctly to `.quotaExceeded` and then did nothing with it: both cases fell
through to a generic "Scan Failed / OK" alert.

Two costs. The user hit a dead end at the moment of highest purchase intent,
with no way to subscribe from that screen. And the event was filed as
`scan_failed{reason:no_result}` — the wrong bucket in the one funnel the
`FREE_SCANS_FIRST_DAY=3` experiment is being read against, which started
7 September. Until this ships, that experiment's numbers understate limit hits
and overstate failures.

It reaches users because the client resets the allowance at **local** midnight
while the server counts **UTC** days. In the hours between, a spent allowance
passes the client's own pre-flight gate and only the server refuses.

### I-1 · a new iPhone could leave the app dead

The App Attest key **id** lives in `UserDefaults`, which iCloud backup and Quick
Start restore onto a new device. The Secure Enclave **key** it names is
hardware-bound and does not migrate. The new phone was therefore forced to mint
a credential, and failed client-side before any request left the device — then
told the user to reinstall, which was the only thing that actually worked.

Now the stale id is discarded and the device re-attests. Narrowly: an id the
Enclave has no key for recovers; a DeviceCheck outage does not, because
throwing away a working key on a transient failure costs a pointless
attestation.

### I-18 · the shipped policy said data was not shared

The website policy gained a Service Providers section on 3 September. The
in-app copy did not, and still read *"We do not sell, rent, or share your photos
or device identifier with third parties."* For a week the app said that on the
screen the paywall links to, while photos went to Google on every scan.

Both copies now name Google/Gemini, TelemetryDeck, Telegram and Apple
(DeviceCheck), describe analytics collection and its opt-out, and qualify the
sharing sentence.

The policy text also moved out of the SwiftUI view body into `PrivacyPolicy`,
because that is why it drifted unnoticed for a week: nothing could read it.
Four tests now assert every processor is named and that the `updated` date moves
when the policy does.

---

## Testing on a device — required before submitting

- **The paywall path (I-23).** On a free account, spend the day's allowance,
  then scan again. You should land on the subscription screen, not an alert.
  With `FREE_SCANS_FIRST_DAY=3` live, that is the fourth scan of the day.
- **Everything else still works.** One ordinary scan, one listing draft, one
  Thrift Flip. This release touches the error path and the policy screen; a
  normal scan should be indistinguishable from 1.3.5.
- **Settings → Privacy Policy.** Scroll to Service Providers. Google,
  TelemetryDeck, Telegram and Apple should all appear, and the date should read
  September 9, 2026.
- **I-1 cannot be tested without a second device.** Restoring a backup onto
  another iPhone is the only real reproduction. It is covered by unit tests on
  the decision rule; the recovery path itself ships unexercised on hardware.
  Worth knowing rather than pretending otherwise.

## Pre-submit checklist

- [ ] **1.3.5 is approved and live** — Apple processes one version at a time.
- [ ] Xcode: confirm **1.3.6 (13)** in the target's General tab.
- [ ] Scheme is plain **SnapWorth**, not *SnapWorth (Mock scans)*.
- [ ] Run the test target once (`⌘U`).
- [ ] The device pass above.
- [ ] Paste the What's New; subtitle, keywords and screenshots unchanged from 1.3.5.
- [ ] After approval: the `FREE_SCANS_FIRST_DAY` funnel becomes readable for the
      first time — I-23 is what stops quota refusals being filed as failures.
      Read it ~21 September as planned, but note the first days of data predate
      this fix.
