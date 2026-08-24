# What's New — SnapWorth 1.3.0

**Version:** 1.3.0 (build 7) · **Previous release:** 1.2.1 (build 6, approved) · **Status:** released — Ready for Distribution

## Why 1.3.0 and not 1.2.2

1.2.1 shipped and was approved. Its pre-release train is therefore closed, and
App Store Connect rejects any further upload carrying that version string:

```
90186  Invalid Pre-Release Train. The train version '1.2.1' is closed for new
       build submissions
90062  CFBundleShortVersionString [1.2.1] must contain a higher version than
       the previously approved version [1.2.1]
```

So a bump was mandatory regardless. The choice between 1.2.2 and 1.3.0 is about
what actually landed since 1.2.1, verified against `git log` for `ios/`:

| Commit    | Change                                                       | User-visible |
|-----------|--------------------------------------------------------------|--------------|
| `fa0d9c2` | Tests proving no EXIF GPS leaves the device                  | no           |
| `4c8b8e4` | Explicit data-protection class on the SwiftData store        | no           |
| `b3d9237` | Migration test for the 1.1.x → 1.2 schema                    | no           |
| `975d9e3` | My Finds portfolio — free total, Pro value history           | **yes**      |
| `417fda3` | Weekly portfolio digest notification                         | **yes**      |
| `48657fb` | Portfolio insights, recoverable 401, honest /health version   | **yes**      |

Three of six commits add user-facing capability: a new number on My Finds, a new
Pro-gated feature behind a new paywall trigger, and a new notification category
with its own settings toggle. That is a minor bump under semver — new
backwards-compatible functionality — not a patch. Calling it 1.2.2 would also
undersell it in the What's New feed, which is the wrong trade for the one release
users should notice.

Build 7 because build 6 was consumed by the 1.2.1 submission and App Store
Connect rejects duplicate build numbers.

---

## Primary — paste into App Store Connect

```
Your finds, as a portfolio.

My Finds now opens with what everything you've scanned is worth —
one number, updated as you add to it. Underneath, it tells you what
still needs listing, what you've actually made on the things you've
sold, and what's been sitting a while.

Pro adds value history: a trend line for your whole portfolio and
the change on each item since you added it.

New: an optional weekly summary so you know where you stand without
opening the app. Off unless you want it, and you can turn it off
again in Settings.

Also in this update
• Your saved scans are now protected by additional on-device encryption
• Crash and performance diagnostics, so problems get fixed faster
• Clearer message when your session needs to reconnect
```

**Character count:** well within the 4,000 limit.

### Alternative, if you prefer the shortest possible

```
Your finds, as a portfolio.

My Finds now shows what everything you've scanned is worth, what still
needs listing, and what you've actually made. Pro adds value history
over time.

New: an optional weekly summary. Plus stronger on-device encryption for
your saved scans and faster diagnosis of crashes.
```

---

## Copy constraints this text respects

**"one number, updated as you add to it"** — not "watch your wealth grow."
Nothing re-values a saved item on its own; the total moves when you scan, re-price
or sell. Implying passive appreciation would be the same class of overclaim that
was stripped from the App Store screenshots and the website homepage.

**No "exact value" anywhere.** The app returns a range with a confidence score.

**No marketplace named that the app does not serve** — eBay, Vinted, Facebook
Marketplace and OLX only.

**The weekly summary is described as optional twice.** The notification
permission prompt is the most likely bounce point in this release; saying so
plainly converts better than a surprise system dialog.

**Encryption is stated, not oversold.** It is
`.completeUntilFirstUserAuthentication` — real protection at rest, not
"military-grade."

---

## Blocking before submission

**App Privacy labels must be updated first.** The privacy manifest declares three
Diagnostics types that the current labels do not:

- Crash Data
- Performance Data
- Other Diagnostic Data

All three: *not linked to the user*, *not used for tracking*, purpose
**App Functionality**. A manifest/label mismatch is a review rejection.

**Notifications need no new privacy label** — local only, scheduled on-device by
`NotificationManager`, no payload leaves the phone and no push backend exists.
