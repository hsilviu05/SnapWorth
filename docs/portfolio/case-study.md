# SnapWorth — case study

**An iOS app that tells thrift resellers what an item is worth from one photo.**
Solo build, July 2026 to today: design, iOS, backend, website, CI and App
Store releases. [App Store](https://apps.apple.com/app/id6788521307) ·
[snapworth.eu](https://snapworth.eu) · source on GitHub (this repository).

## The problem

Resellers decide in the aisle, in about fifteen seconds, whether a $4 jacket
is a $60 flip. Checking means typing into marketplace search on a phone while
holding the item. SnapWorth replaces that with one photo: the item, its brand
and condition, a resale price range with a confidence level, and what the
user would make after the marketplace's fees.

## What I built

| Layer | Stack |
|---|---|
| iOS app | SwiftUI, MVVM with `@Observable`, SwiftData, AVFoundation camera, StoreKit 2 subscriptions, WidgetKit (Home and Lock Screen widgets, a Control Center control, a Live Activity), iMessage stickers; English, Romanian, Spanish, German and Simplified Chinese |
| Backend | FastAPI (Python) calling Google Gemini vision (`gemini-2.5-flash` by default), Redis, Docker on Railway |
| Website | Static site on Vercel, with generated SEO pages (`website/seo/`) |
| CI | GitHub Actions: iOS build and tests, backend tests and type check, secret scanning, a model-evaluation gate, website checks |

Around 900 commits; about 2,400 backend and 1,200 iOS test functions.

## Technical challenges, and how I solved them

**Device attestation that silently failed.** The backend verifies App Attest
assertions before minting a token. Token refreshes were being rejected, so
returning users looked like new devices and lost their free-scan state. The
device signs the nonce as a message, and the server was verifying it as a
pre-hashed digest. Verifying over the nonce as signed fixed it (`e7ff784`),
with a regression test that a pre-hashed signature is refused.

**One model file, two targets.** A widget extension can't import the app's
module, yet both read the same widget data. Rather than keep two copies in
step with a CI diff, one source file (`ios/Shared/WidgetModel.swift`) is
compiled into both targets (`e5261f7`), with a rule that it may reference
nothing that exists in only one.

**Localization that fails loudly.** A missing string-catalog key fails
silently: the English literal is shown. Strings live in JSON with five
languages each; a generator builds the `.xcstrings`, and CI fails both on
drift between the two and on any user-facing literal with no catalog entry
(`tools/check_localization.py`).

**A data store that emptied itself.** Redis is the system of record for
sign-ins, refunds and quotas. A persistence setting applied in the wrong
order emptied it twice. I traced it, wrote the safe order into the runbook
(`1ffff51`), and added a health check that reports persistence status.

**Subscriptions the server can trust.** StoreKit 2 transactions are verified
server-side, App Store Server Notifications V2 are received for production
and sandbox, and the backend asks Apple for a subscriber's live status
rather than only waiting to be told (`e2a5af4`).

**Free-tier fairness across time zones.** The free allowance is counted on
the server, by UTC day, so the app mirrors the server's day rather than the
phone's. That removed a window east of UTC where the app offered a scan the
server refused.

**Secrets in a public repository.** gitleaks scans every commit in history on
every CI run, not only the current tree (`3473f80`), with scoped exceptions
for test fixtures that are meant to look like keys.

**Deploys in order.** Backend deploys run serially, in push order (`a8db906`),
so an older commit can never overwrite a newer one in production.

**Honest numbers.** No accuracy figure is published until it is measured on
a held-out gold set of real sales, with label-drift and leakage guards
(`docs/EVALUATION.md`), and CI checks for fabricated benchmark data.

## Results

* Live on the App Store, with real paying subscribers.
* About **225 downloads** and about **$106 in revenue** so far, roughly $0.47
  per download: people who find it pay for it, so the work now is discovery.
* In the six weeks to the September subtitle change, the store page turned
  16.8K impressions into a 2.1% tap-through, and 18.6% of page views
  installed (`marketing/app_store_listing.md`).
