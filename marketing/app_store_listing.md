# App Store Listing

English (`en-US`), the primary storefront. The app also ships in Romanian,
Spanish, German and Simplified Chinese, each with its own listing beside this
one — `app_store_listing.ro.md`, `.es.md`, `.de.md`, `.zh-Hans.md`. Those three are metadata for an
app that is translated; the listing and the interface ship together, because a
localized store page that opens an English app is worse than neither.

## The live store is not this file (checked 2026-09-26)

Read with the iTunes lookup
(`https://itunes.apple.com/lookup?id=6788521307&country=de`) on the `us`,
`de`, `es`, `ro` and `cn` storefronts. Control apps return localized text by
the same method — WhatsApp on `de`, `es` and `ro`, WeChat on `cn` — so the
result is not the lookup's fault. `ro` defaults to English even for a
localized app; add `&lang=ro_ro` there.

* **Name** is "SnapWorth: Resell & Flip" on every storefront. That was set in
  App Store Connect and recorded nowhere in git; this file said
  "SnapWorth: Resale Value".
* **No localized metadata.** Every storefront shows the English page, although
  the binary has been in five languages since 1.4.2 (Apple lists EN, DE, RO,
  ZH, ES). RELEASE-NOTES-1.5.0.md assumed the four translations carried over
  to 1.5.0; they did not, or were never added.
* **The English description is an older copy**: seven marketplaces rather
  than nine (no Kleinanzeigen, no Xianyu), no iMessage stickers line on the
  sticker release, and "accurate" still in the second paragraph.

All of it is version-scoped, so it is fixed by the next submission and not
before — see *Paste with 1.5.1* at the end of this file.

## Claims this listing does not make

Every locale follows these; the translated files point here.

* **Sold listings, comps or market data.** SnapWorth has no such source. The
  estimate is the model's.
* **"Accurate", "exact" or "precise" about the estimate** — nor *exactă*,
  *precisă*, *exacta*, *precisa*, *genau*, *exakt*, *准确*, *精准*. No accuracy
  figure has ever been measured (`docs/EVALUATION.md`: "zero measurements
  taken"). Until one is, the estimate is an AI estimate with a range and a
  confidence score, and nothing stronger. "Accurate" sat in the description
  from a26e321 until 1.5.1 and was live on the US store.
* **"Unlimited scans" without its fair-use qualifier** — in every locale, and
  in the paywall's footnote it mirrors. Pro scans are capped per device per
  hour on the server (`PRO_SCAN_RATE_MAX_REQUESTS`, 60; RUNBOOK §5.8), and one
  address is capped at 60 requests an hour across scans, drafts and trends.
  Nor a number: the caps are server configuration, and a figure here would
  outlive a change to them.
* **Confidence as "how clearly the AI identified the item".** That was v1,
  the model rating itself. The score since v2 (`backend/confidence.py`) weighs
  whether the brand was read, how tight the range is, how clear the photo is,
  the category, and — at low weight — the model's own certainty. Describe it
  as how strongly the photo and the identification back the estimate.

The care-tag line's comparative ("sharper"; *mai exactă*, *genauere*, *更准* in
the translations) is also unmeasured. It stays as the app words it, because
the listing mirrors the app: change "Read the care tag for a sharper estimate"
in `ios/Localization/App.json` first if it changes.

## App name (30 chars max)
SnapWorth: Resale Value

**Live is "SnapWorth: Resell & Flip" (24 chars).** Decide which one 1.5.1
carries, delete the other line, and pick the keyword line below that matches:
`resale` and `value` are only safe to leave out of the keywords while they are
in the name.

## Subtitle (30 chars max)
Thrift Store Flip Scanner

The previous subtitle, "Resale Value in Seconds", repeated the two words
already in the app name — the one field Apple both indexes for search *and*
shows on the results card was spent saying the same thing twice. Six weeks of
data: 16.8K impressions, 2.1% tapped through, 18.6% of those installed. The
page converts; the card does not. This subtitle adds four searched terms
(thrift, store, flip, scanner) and tells a thrifter in three words that this
is for them. Re-check Acquisition → Sources → Search terms a week after the
change.

---

## Promotional Text (170 chars — update anytime without resubmitting)

This field is not tied to a build. It can be swapped any time from App Store
Connect, which makes it the right place to test hooks and to carry anything
seasonal or time-limited. Keep the standing terms in the Description instead.

**Live — pain-led (153 chars).** Reuses the onboarding headline, so the
store page and first launch say the same thing. The strongest hook of the three.

That $4 jacket might be $90. Snap any thrift find and get an AI resale estimate in seconds — before you're at the till guessing. One free scan every day.

**Alternative — offer-led (151 chars).** Was live until September 2026.

One free scan every day. Point your camera at any thrift find and instantly know what it's worth — an AI resale estimate in seconds. No account needed.

**Alternative — short (141 chars).** Least reliant on a specific figure.

Know what it's worth before you buy it. One photo, one AI resale estimate, about four seconds. One free scan every day — no account, no card.

Every option states the one-scan-a-day limit. None claims sold listings, comps
or market data — SnapWorth has no such source.

---

## Description

Ever picked up something at a thrift store and wondered if it's actually worth something? SnapWorth tells you instantly.

Point your camera at any secondhand item — a jacket, a pair of sneakers, a vintage camera, a designer bag — and our AI identifies it and estimates a resale value range in seconds.

No more guessing. No more leaving $100 flips on the shelf.

--------------------------

HOW IT WORKS

1. Point your camera at any secondhand item
2. Tap the shutter — or pick a photo from your library
3. Get an instant AI resale-value estimate
4. Copy your ready-to-post listing and sell it today

--------------------------

WHAT YOU GET

• Instant resale value — an estimated low-to-high range for your item
• Confidence score — how strongly the photo and the identification back the estimate
• AI listing draft — a ready-to-post title and description with every scan
• Thrift Flip — scan an item, add its shelf price, and see what you'd make after marketplace fees before you buy
• Scan history — every find saved automatically with its value
• Total haul tracker — see what your collection is worth at a glance
• Widgets — your haul's total on the Lock Screen; recent finds, scans left and one-tap scan on the Home Screen
• iMessage stickers — 20 stickers of Tag, our mascot (four animated), for bragging about a find

--------------------------

BUILT FOR

• Thrift store shoppers who want to flip for profit
• Resellers on eBay, Poshmark, Mercari, Depop, Facebook Marketplace, Vinted, OLX, Kleinanzeigen and Xianyu
• Estate sale and yard sale hunters
• Anyone who's ever thought "is this worth buying?"

--------------------------

FREE & PRO

SnapWorth is free to try — no account needed. You get one free scan every day, forever. Every find is saved to your device with its value, and your history is yours whether you pay or not.

Pro adds:
• Unlimited scans (fair use applies)
• Listing drafts rewritten for the marketplace you pick — eBay, Poshmark, Mercari, Depop, Facebook Marketplace, Vinted, OLX, Kleinanzeigen or Xianyu
• Why this price — the full breakdown behind an estimate, including the price ladder and what drove the value
• Read the care tag — photograph the label for a sharper estimate
• Portfolio value, trend and thrift trends
• Your profit ledger — what you paid, what it sold for, and what you actually made after fees, with CSV export

• Monthly: $4.99/month
• Yearly: $39.99/year (3-day free trial included)

Cancel anytime from your iPhone settings.

--------------------------

PRIVACY

Photos are processed in real time and never stored on our servers. Your scan history stays on your device. We don't sell your data. Ever.

LEGAL

Privacy Policy: https://api.snapworth.eu/privacy
Terms of Use: https://api.snapworth.eu/terms

snapworth.eu

---

## Keywords (100 chars max)
reseller,secondhand,vintage,goodwill,poshmark,mercari,depop,ebay,thrifting,worth,price,profit,sell

98 characters, for the name "SnapWorth: Resale Value". Words already in the
app name or subtitle (resale, value, thrift, store, flip, scanner) are indexed
from there and were dropped to make room. Poshmark, Mercari and Depop are
searched by exactly this audience and are honest claims from 1.3.4. Vinted
was dropped as a keyword only; it is still supported and still named in the
description.

**If the name stays "SnapWorth: Resell & Flip"**, `resale` and `value` — the
category's two main search terms — are in neither the name nor the keywords
on the live store. Use this line instead (97 characters):

resale,value,secondhand,vintage,goodwill,poshmark,mercari,depop,ebay,thrifting,worth,price,profit

It drops `reseller` and `sell`, the two nearest to the name's "Resell". That
is a judgment, not a measurement: check Acquisition → Sources → Search terms
a week after it goes live.

## Category
Primary: Shopping
Secondary: Utilities

## Age Rating
4+

## Support URL
https://snapworth.eu/support

## Privacy Policy URL
https://api.snapworth.eu/privacy

## Marketing URL
https://snapworth.eu

---

## What's New (Version 1.1.2) — ARCHIVED, do not paste

Historical record of what shipped with 1.1.2, when the free tier was three a
day. It is now one. Do not reuse this text.

More free scans, every day.

• You now get 3 free scans every single day — use them up and they refresh tomorrow. No account, no card.

• Faster, more reliable valuations behind the scenes.

• Clearer, more honest results — every estimate is labeled as an AI estimate with a confidence level.

• General polish and bug fixes.

Happy hunting! Got a feature request? Email her.silviu.i@gmail.com

---

## What's New (Version 1.1)

New: Share Cards & My Flips profit tracking

• Share Cards — turn any valuation into a clean, shareable card with the resale range and a scan-me QR code. Perfect for posting your finds to stories and groups.

• My Flips — your new profit ledger. Mark items as sold, track what you actually made vs. what you paid, and see your total profit on a dashboard. Export your numbers or share your monthly haul.

• Polished the My Finds screen so empty and no-results states are now cleanly centered.

• Plus performance improvements and bug fixes.

Happy hunting! Got a feature request? Email her.silviu.i@gmail.com

---

## Paste with 1.5.1

Metadata is per version and a version in review cannot be edited, so all of
this goes onto 1.5.1 before it is submitted.

- [ ] **Add the four localizations** — Romanian, Spanish, German, Simplified
      Chinese — and paste each from its own file: name, subtitle, keywords,
      description, promotional text. The 1.5.1 What's New per locale comes
      from `RELEASE-NOTES-1.5.1.md` once it exists.
- [ ] **App name**: decide (see *App name* above) and record the answer here.
- [ ] **Keywords** (`en-US`): the line that matches the name.
- [ ] **Description** (`en-US`): re-paste in full. Against the live
      copy it names nine marketplaces rather than seven, and adds the
      stickers, Thrift Flip and widgets lines, the new confidence wording,
      and drops "accurate".
- [ ] **Description** (other four locales): paste in full; each gained the
      same Thrift Flip, widgets and confidence lines.
- [ ] **"Unlimited scans (fair use applies)"** is new in all five
      descriptions. The cap it qualifies is the server's (RUNBOOK §5.8), not
      the binary's, so the line is true whichever build is live.
- [ ] **App Privacy** (app-level, not per version, but it must match the
      manifest in the 1.5.1 binary): add Purchases → Purchase History, used
      for App Functionality, linked to the user, not used for tracking. The
      reasoning is in `ios/SnapWorth/PrivacyInfo.xcprivacy`.
- [ ] After release, open apps.apple.com/de/app/id6788521307 (and `/es`,
      `/ro`, `/cn`) and confirm the page is in that language — or re-run the
      iTunes lookup above per storefront.

The new translated lines — confidence, Thrift Flip and widgets in each of the
four files — were written without a native reader; have each read once
before pasting.
