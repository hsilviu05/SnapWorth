# What's New — SnapWorth 1.4.2

## Scope

Four iOS pull requests since 1.4.1: #157, #158, #159, #160. Everything else on
`main` since build 17 is Dependabot on the backend — `pyjwt`, `google-genai`,
`actions/setup-python` — which is deployed and not in this build.

One release, one story: **the app is no longer English-only, and two
marketplaces were added because a translation without somewhere to sell is
decoration.**

| PR | What | Visible? |
|----|------|----------|
| #158 | Romanian, plus the catalog machinery all four languages ride on | **Yes** |
| #159 | Spanish and German | **Yes** |
| #160 | Simplified Chinese, Xianyu, Kleinanzeigen | **Yes** |
| #157 | First-run funnel instrumentation | No — measurement only |

549 strings, five languages, two catalogs (the app's and the widget
extension's, because `String(localized:)` resolves against `Bundle.main` and a
widget extension cannot read the app's resources). `ios/Localization/README.md`
is the contract; this file is the release.

### Was 1.4.1 ever released?

No. Build 17 was uploaded to Apple on 2026-09-16 and is still in review,
English-only. Its content — the two widget fixes, `f6c6ec4` and `f1a495a`,
both 2026-09-14 — is therefore **already in the pipeline and must not be
repeated in this What's New**. If 1.4.1 is rejected or pulled, fold its note
(below, under *If 1.4.1 never ships*) into this one before submitting.

One uncertainty worth resolving in Organizer: #157 merged 2026-09-16 at 17:56
local. If the build-17 archive was cut after that, the funnel instrumentation
is already uploaded and 1.4.2 adds nothing on that front; if before, it ships
here. Either way no user sees a difference — it changes only which release the
telemetry backfills from.

---

## Primary — paste into App Store Connect (`en-US`)

```
Now in five languages, and two more places to sell.

SnapWorth speaks Romanian, Spanish, German and Simplified Chinese — the whole app, not just the menus. The camera, the result screen, My Finds, the profit ledger and every widget. It follows your phone's language on its own; you can also set it per app in Settings.

Two marketplaces added: Kleinanzeigen and 闲鱼 (Xianyu). Pick either for a listing draft and it comes back written in German or Chinese, the way a seller there writes it, with that marketplace's fees in the profit maths — neither charges a private seller commission.

Estimates stay in US dollars wherever you are. That's what the valuation is, and a converted figure would only be a guess.
```

### Shorter alternative

```
SnapWorth is now in Romanian, Spanish, German and Simplified Chinese — the whole app, including the widgets. And two marketplaces added: Kleinanzeigen and 闲鱼 (Xianyu), each with listing drafts written in its own language and its fees in your profit maths.
```

### The other four locales

Each localized listing file carries its own What's New block, already written,
in the language of that storefront. Paste each from its own file — do not
translate this English block a second time:

| Locale | File | Block |
|---|---|---|
| `ro` | `app_store_listing.ro.md` | *What's New (Version 1.4.2) — Română* |
| `es` | `app_store_listing.es.md` | *What's New — Español* |
| `de` | `app_store_listing.de.md` | *What's New — Deutsch* |
| `zh-Hans` | `app_store_listing.zh-Hans.md` | *What's New — 简体中文* |

The `es`, `de` and `zh-Hans` blocks open on the language and then recap the
widgets from 1.4.0. That is deliberate — in those storefronts this is the first
release anyone can read, so the recap is new information to a new reader, even
though an existing user has had widgets for two versions. If you would rather
they match the English block exactly, cut each one's widget paragraphs; nothing
else depends on them. The Romanian block is the newest of the four and skips
the recap, because `ro` was written before that argument was settled — leave it
or add the recap, but pick one and apply it to all four.

Two marketplace notes, because #160 landed after #159 wrote the Spanish and
German pages. The German What's New now names **Kleinanzeigen** — it is the
platform most Germans sell on, it leads that page's description already, and a
German release note that omitted it would be burying the release's best line
for that storefront. The Spanish and Romanian blocks name neither new
marketplace, which is right: Xianyu is China and Kleinanzeigen is Germany, and
neither is where a Spanish or Romanian seller lists.

Name, subtitle, keywords and description for each locale are in the same four
files, with verified character counts.

---

## What changed, for the record

### #158 — Romanian, and the machinery (549 strings, three catalogs)

The strings do not live in `.xcstrings`. They live in plain JSON under
`ios/Localization/`, keyed by the exact English text, and
`tools/build_xcstrings.py` generates the catalogs Xcode compiles. Two reasons:
a JSON diff is reviewable and an `.xcstrings` diff is not, and the generator
can refuse a translation that would crash.

That refusal is the safety property of this release. **Keys are the English
source string**, so a missing or mistyped key degrades to English silently —
annoying, never fatal. The one thing that *is* fatal is a format specifier that
disagrees with what the code passes: `%@` where the caller hands over an `Int`
crashes at display time, in a language nobody on the team reads. The generator
validates every form of every entry against its English source and fails the
build on a mismatch, allowing exactly three shapes — an identical specifier
sequence, an all-positional reordering with bounds and conversion checked, or a
plural form that drops specifiers.

`tools/check_localization.py` covers the other direction: a Swift lexer of its
own walks every source file and fails CI on a literal sitting in a localizable
position with no catalog entry. A string that is English on purpose goes in
`DELIBERATELY_ENGLISH` with a reason, so "forgotten" and "decided" are
distinguishable a year from now.

Plurals are per-language CLDR categories, not an appended "s": `en`/`es`/`de`
need `one` and `other`, `ro` needs `one`, `few` and `other` (Romanian inflects
at 1, at 2–19, and again at 20+ where the noun takes *de*), `zh-Hans` needs
only `other`. One plural key agrees with one number, so the few sentences
counting two things are assembled from separately-inflected parts rather than
forced into a single key.

### #159 — Spanish and German

Chosen from TelemetryDeck's *New Users by Preferred Language*, not from a guess
about which markets sound promising.

German is the reason Kleinanzeigen is in #160: shipping a German app whose
Snap → Sell list is eBay, Poshmark, Mercari and Depop would be a translation
pointing at the wrong shops.

### #160 — Simplified Chinese, and the two marketplaces

Chinese was the strongest signal in the same data and was still held back one
release, because until this PR the app had nowhere Chinese to sell. 闲鱼 is
where a Chinese reseller actually lists; a Chinese app recommending Poshmark is
worse than no translation.

`Marketplace` went from seven cases to nine. Both new ones return `nil` from
`appURLScheme` — the file's standing rule is never to fabricate a scheme, so
they open `goofish.com` and `kleinanzeigen.de`, which reach the app anyway via
universal links when it is installed.

**Listing language follows the marketplace when the marketplace belongs to one
country.** `MARKETPLACE_GUIDANCE` in `backend/main.py` tells the model to write
Xianyu listings in Simplified Chinese and Kleinanzeigen listings in German
addressing the reader as *du*, whatever language the seller's phone is in — a
German buyer reading a Kleinanzeigen ad wants German. Vinted and OLX operate
across a dozen countries and have no one language, so they stay English; eBay is
the same case and English is the sane default. `Condition.xianyuPhrase` and
`Condition.kleinanzeigenPhrase` exist as separate members from
`Condition.listingPhrase` for precisely this reason.

Fees: both entries are 0% and both carry a comment naming what is *not*
modelled rather than inventing a number — Xianyu's software service fee
(introduced late 2024, above a monthly volume threshold, rate and threshold
unverified) and Kleinanzeigen's optional paid upgrades and commercial-seller
plans (neither is a commission, neither applies to a private reseller).
`website/index.html`'s `FEES` mirror was updated to match, which CI checks.

### #157 — first-run funnel (invisible)

Onboarding had no instrumentation at all, so a user who never reached the
camera was invisible and the retention number had no denominator. `is_first`
now rides on `scan_started`, `scan_result_shown`, `scan_failed` and
`paywall_viewed` as a parameter rather than a parallel family of `first_*`
events. No UX, copy or flow change, no new SDK, no App Privacy label change.
It is not in What's New; a user would not know what they were reading.

---

## Testing on a device — required before submitting

The whole release is text on a screen, which means the simulator can carry most
of it. Three things can only be seen on a device.

### Language switching

- [ ] **Settings → SnapWorth → Language** exists at all. iOS adds this row
      automatically for any app shipping more than one localization; if it is
      missing, `knownRegions` or the catalogs did not make it into the build,
      and every check below is moot.
- [ ] Set it to **Română**, relaunch, and walk the app: camera, result screen,
      Găselnițe, Flipuri, Setări, the paywall.
- [ ] Repeat for **Español**, **Deutsch** and **简体中文**. The Chinese pass
      matters most — it is the only language of the four with no Latin
      fallback, so a missing key is unmistakable rather than merely wrong.
- [ ] Set the phone to a language the app does *not* ship (French) and confirm
      it falls back to English rather than showing anything half-translated.

### Widgets — remove and re-add

iOS caches widget snapshots across an extension update. A widget left on the
Home Screen will keep drawing the old English snapshot after the update and
prove nothing.

- [ ] Remove every SnapWorth widget, install the build, **then** add them back.
- [ ] In each non-English language, check all eight surfaces: Haul (small,
      medium), Haul on the Lock Screen (inline, circular, rectangular), Recent
      finds (medium, large), Scans left (small, circular), Profit this month,
      Thrift run in the Dynamic Island.
- [ ] **Scans left, as a Pro subscriber**, must read *Unlimited* translated,
      not a number and not the English word. This one string is in both
      catalogs — it is part of the shared widget model — so it is the single
      best probe that the widget catalog actually shipped.

### Plurals and truncation

- [ ] **Romanian, at 1, at 3 and at 20 scans left.** Three different forms.
      Twenty is the one that is usually wrong: *20 de scanări*, with the *de*.
- [ ] German compounds and Romanian diacritics in the tab bar and on buttons —
      look for clipping at the smallest supported width, and at the largest
      Dynamic Type size.
- [ ] Chinese at the largest Dynamic Type size: the glyphs do not shrink the
      way Latin text does.

### The two new marketplaces

- [ ] **Snap → Sell → Kleinanzeigen.** The draft comes back in German, using
      *du*, with a private-sale disclaimer. Requires the deployed backend — it
      ships on merge to `main`, so it is already live, but confirm before
      trusting the tap.
- [ ] **Snap → Sell → 闲鱼.** The draft comes back in Simplified Chinese.
- [ ] Both open a browser rather than doing nothing — neither has a URL scheme.
- [ ] The fee breakdown for both shows 0% and the profit number equals the sale
      price minus what you paid.

### Money stays in dollars

- [ ] In every language, an estimate reads `$45–$90`. Confirm no locale
      formats it as `45,00 $` or converts it. `NumberFormatter.snapCurrency` is
      pinned to `en_US`/USD on purpose, because the valuation is in dollars
      whatever the phone's region.

---

## Pre-submit checklist

- [ ] **Archive from a checkout that contains `e81d0ec`.** The Organizer
      currently holds a 1.4.1 (17) archive created 2026-09-20 that was cut
      before the bump and has not been uploaded — re-archive, do not upload
      that one. App Store Connect rejects a build number already used for a
      version, and 17 is spent.
- [ ] `MARKETING_VERSION = 1.4.2` and `CURRENT_PROJECT_VERSION = 18` in all
      eight slots of `project.pbxproj` — app and widget extension, Debug and
      Release. Done in `e81d0ec`.
- [ ] Add `ro`, `es`, `de` and `zh-Hans` to the **1.4.2** version in App Store
      Connect. This is version-scoped metadata and cannot be added to 1.4.1
      while it is in review.
- [ ] Name, subtitle, keywords, description and What's New pasted per locale
      from the four listing files.
- [ ] English description replaced from `app_store_listing.md` — it now names
      Kleinanzeigen and Xianyu in both the *Built for* and *Pro* lists.
- [ ] Device testing above, all five groups.
- [ ] Screenshots: none uploaded per locale, so Apple falls back to the English
      set. Acceptable for `ro`, `es` and `de`; weakest for `zh-Hans`, whose page
      claims the app is in Chinese while showing five English screenshots. If
      one locale gets its own set, it is that one.

### If 1.4.1 never ships

If 1.4.1 is rejected or withdrawn rather than approved, 1.4.2 becomes the first
build since 1.4.0 to reach anyone, and its two widget fixes need a line. Insert
before the dollars paragraph:

```
Also fixed: "Scans left" now reads Unlimited for Pro subscribers instead of a number that meant nothing, and "Recent finds" fills the whole widget rather than half of it.
```

Then delete this section, and note in `app_store_listing.ro.md` that its
archived 1.4.1 Romanian block is live after all.

## Not in this build

* Dependabot's backend bumps (#154, #155, #156) — deployed on merge, no app
  release needed.
* Everything in the 1.4.1 archive uploaded 2026-09-16, which is a separate
  submission still in review.
