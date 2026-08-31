# What's New — SnapWorth 1.3.3

**Version:** 1.3.3 (build 10) · **Previous release:** 1.3.2 (build 9, live) · **Status:** unreleased, waiting on screenshots

## Scope

Four commits touch `ios/` since 1.3.2. Two are user-visible, one is a
behaviour change you have to decide how to communicate, one is developer-only.

| Change | Visible? |
|---|---|
| Paywall headline said "free for 3 dayss" | **yes** |
| Free-scan count now reflects what the server actually allows | **yes** |
| Free tier is 1 scan/day, not 3 | **yes — and already live server-side** |
| StoreKit config path in the Xcode scheme | no (developer only) |

The backend halves of all of this are already deployed. This release is what
makes the app agree with the server it has been talking to.

---

## 🔴 Blocking — the store listing contradicts the app

**Do not submit until this is resolved.**

Production has been serving **1 free scan per day** since before this release.
The live App Store description still says:

> Now with 3 free scans every day!
> …
> You get 3 free scans every day, forever.

`marketing/app_store_listing.md:9,53`

The website repeats it in at least four more places (`website/index.html:1248,
1450, 1531`, `website/support.html:349`).

That is inaccurate metadata — App Store Review Guideline 2.3.1 — and it is the
kind of thing a reviewer checks by opening the app. It is also simply untrue
for every user reading it today.

Two ways out, and this is a product decision, not a copy edit:

1. **Update the copy to match the product.** Honest and immediate. Costs you
   the "3 free scans every day" hook, which is doing real work at the top of a
   funnel currently converting 19.6% store-page-to-download.
2. **Put the server back to 3/day** (`FREE_SCANS_PER_DAY=3` in Railway) and
   ship the accuracy fixes without the reduction. Revisit the limit once you
   have data on whether a wall converts or churns.

Either is defensible. Shipping 1.3.3 with the current mismatch is not.

---

## Primary — paste into App Store Connect

```
Your free scans, accurately counted.

SnapWorth now shows exactly how many free scans you have left — the real
number, straight from the app, rather than a figure it guessed at.

So it's said plainly: the free plan is one scan a day, and it refreshes
every day. Pro removes the limit.

Also fixed a typo on the subscription screen that had been there longer
than we'd like.
```

**Character count:** well within the 4,000 limit.

### Shorter alternative

```
SnapWorth now shows the real number of free scans you have left. The free
plan is one scan a day, refreshed daily; Pro removes the limit. Also fixed
a typo on the subscription screen.
```

---

## Copy constraints this text respects

**No claim that anything got faster or more accurate.** Nothing in this build
touches valuation. The estimate quality changes people associate with this
period were server-side and shipped weeks ago.

**The one-scan-a-day limit is stated outright.** It changed server-side before
this build, so the release did not cause it — but 1.3.3 is when the number on
screen visibly goes from 3 to 1, and users deserve to read why from us rather
than discover it. It is phrased as a statement of the current terms, not as a
new feature. The store description must carry it too; What's New is not a
substitute for that.

**"a count the app guessed at" is literally accurate.** `ScanView` rendered the
figure from a compiled-in constant and ignored the `free_scans_remaining` the
server sent on every token mint.

**Nothing about sold listings, comps or market data.** SnapWorth still has no
sold-listings source; `backend/comps/` is built but disabled.

---

## What went wrong, for the record

### The count the app showed was never the server's

`ScanQuota` has been authoritative since SEC-02 — the local counter was
advisory and reset on reinstall. But the *displayed* figure came from
`Config.freeScansAllowed`, a constant compiled into the binary, and the word
"today" was hardcoded in two places. `free_scans_remaining` was decoded off the
token response and thrown away.

While the limit was 3 on both sides this was invisible. The moment production
moved to 1, every free user was told they had three scans and refused on the
second. Two quieter consequences of the same bug:

- **Reinstalls.** DeviceCheck can withhold a fresh allowance
  (`starting_balance` returns 0), but a newly installed local counter reads
  untouched — so the app offered scans the server was always going to refuse.
- **Staleness.** Even reading the server's figure only at token mint would
  leave it wrong for up to a token lifetime. The scan response now carries the
  count, so it is exact after every scan.

### "free for 3 dayss"

Two layers each pluralised the trial duration.
`StoreKitPurchaseService.introductoryDescription` built `"3-days free trial"`,
then `PaywallView.trialDuration` split on `-`, took `"days"` and appended
another `s`. The headline on the highest-intent screen in the app read **"Try
SnapWorth free for 3 dayss"**.

It survived because `MockPurchaseService` hardcodes the *singular*
`"3-day free trial"`, so previews and the test suite rendered the correct
string while real StoreKit did not — the mock disagreed with production about
its own input.

It also survived because **it could not be reproduced locally**: both
`StoreKitConfigurationFileReference` paths in the scheme were one `../` short
and had never resolved, so the local StoreKit configuration had been broken
since it was added on 18 July. The only code path that reproduces the bug was
the one nobody could run. That is fixed in this build; `⌘R` now loads the local
`.storekit` file and renders the paywall with a real trial.

---

## Before submitting

- [ ] **Resolve the store-listing contradiction above.** Blocking.
- [ ] New screenshots in place — see #35. The currently shipped
      `screenshot_1.png` / `screenshot_2.png` claim "real sold listings" and
      "38 sold listings", which the app does not do. That is a second
      inaccurate-metadata exposure and this release is the natural moment to
      retire it.
- [ ] Run the paywall in the Simulator (`⌘R`) and confirm the headline reads
      "free for 3 days". This is newly possible in this build.
- [ ] Confirm the free-scan pill matches `FREE_SCANS_PER_DAY` in Railway.

No new data collection, so the App Privacy labels settled for 1.3.0 are
unchanged.
