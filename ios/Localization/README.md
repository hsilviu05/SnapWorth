# Localization

The app ships in English (`en`, the development language), Romanian (`ro`),
Spanish (`es`), German (`de`) and Simplified Chinese (`zh-Hans`).

## Where the strings live

`App.json` and `Widgets.json` in this folder are the **source**. They are plain
JSON, one entry per key, and they are what a change is written in and reviewed
from:

```json
"Reveal the estimate": {
  "comment": "Button under a covered price.",
  "en": "Reveal the estimate",
  "ro": "Arată estimarea",
  "es": "Ver la estimación",
  "de": "Schätzung zeigen",
  "zh-Hans": "看估价"
}
```

Every string carries a value for every language. A missing one is an error,
reported per language with a count, because the alternative is a screen that is
half translated and nothing that says which half.

`tools/build_xcstrings.py` turns them into the two String Catalogs Xcode
builds — `ios/SnapWorth/Localizable.xcstrings` and
`ios/SnapWorthWidgets/Localizable.xcstrings` — plus `InfoPlist.xcstrings` for
the three permission prompts, from `InfoPlist.json`.

**A string that is in these source files is owned by them.** Editing its
entry in Xcode's catalog editor loses the edit at the next regeneration, and CI
runs `--check`, which fails on a managed entry that has drifted.

The catalogs are not owned outright, though. Both targets build with
`SWIFT_EMIT_LOC_STRINGS = YES`, so Xcode extracts strings during its own builds
and adds any key the catalog does not have — which is the one thing this
arrangement cannot do for itself. A key with an interpolation in it depends on
the *type* of what is interpolated (`%lld` for an integer, `%@` for a string),
and that needs a compiler. Where one was guessed wrong here, Xcode adds the
right key on the first build and the wrong one simply never matches anything.

So the generator keeps entries it does not know about rather than deleting
them, and reports them by name:

```
471 strings  ios/SnapWorth/Localizable.xcstrings, 1 not in App.json
       untranslated: 'Held for %lld days'
```

That line means the string is shipping in English. Move it into the source
file, translate it, and regenerate.

Why not let Xcode own the whole thing: there is no Swift toolchain in this
repository's agent environment, and the CI runner builds only after these
checks have already passed. A source that is plain JSON can be reviewed as a
diff and checked in a second; a catalog that only a build can produce cannot.
The cost is that a missing or misspelled key fails silently — the lookup misses
and the English literal is shown, and nothing says so — which is what
`tools/check_localization.py` is for.

## Adding a string

1. Write it in the code as a literal SwiftUI takes as a key — `Text("…")`,
   `Button("…")`, `.navigationTitle("…")`, `.accessibilityLabel("…")` — or, for
   anything that is a plain `String`, as `String(localized: "…")`.
2. Add an entry to `App.json` or `Widgets.json` keyed by that exact English
   text.
3. Run `python3 tools/build_xcstrings.py`.
4. Commit the source **and** the regenerated catalog.

`tools/check_localization.py` fails CI if step 2 was skipped.

## Two targets, two catalogs

A widget extension cannot read the app's resources: `String(localized:)`
resolves against `Bundle.main`, which is the app in one target and the
extension in the other. The shared widget model — the ~700 lines duplicated
between `WidgetDataStore.swift` and `SnapWorthWidgets.swift`, see the root
`CLAUDE.md` — is compiled into both, so **every string it uses must be in both
catalogs**. Seventeen strings are in both for that reason, which is most of
what the two catalogs have in common — the other three ("Scan", "No finds
yet", "Profit this month") are separate strings that happen to read the same
in the app and in a widget.

## Symbol generation is off, on purpose

`STRING_CATALOG_GENERATE_SYMBOLS` is `NO` in all four build configurations.
Xcode 26 otherwise turns every key into a Swift identifier, and eleven pairs in
these catalogs collide when it does: `Nothing scanned yet` (a widget's empty
state) against `nothing scanned yet` (the same fact mid-sentence, for
VoiceOver); `Copied` against `Copied!`; `Search finds` against `Search finds…`;
`Best flip` against `best flip`. Each pair is two strings a reader should see
differently, and the only way to keep the symbols is to bend the English until
an identifier generator is satisfied with it.

Nothing here uses the generated symbols — the code says `Text("…")` and
`String(localized: "…")` — so the setting is off and the copy stays as written.
`tools/build_xcstrings.py` fails if it is turned back on, because the
alternative is finding out four minutes into a build.

## Plurals

Romanian agrees at three boundaries where English agrees at one: `1`, `2–19`,
and `20` and up, where the noun takes *de* — "o zi", "3 zile", "20 **de** zile".
A key whose value depends on a count is written with all three:

```json
"%lld days": {
  "en": { "one": "%lld day", "other": "%lld days" },
  "ro": { "one": "o zi", "few": "%lld zile", "other": "%lld de zile" },
  "es": { "one": "%lld día", "other": "%lld días" },
  "de": { "one": "%lld Tag", "other": "%lld Tage" },
  "zh-Hans": "%lld 天"
}
```

English, Spanish and German inflect once, at 1, so they need `one` and `other`.
Romanian needs `few` as well. Chinese does not inflect at all, so its value is
a plain string — one form covers every number, and writing it as a one-entry
plural would be a dictionary pretending to a distinction the language does not
make. The builder requires exactly those, and refuses a translation whose format specifiers disagree with the
source string — a `%@` where the code passes an `Int` is a crash at the moment
the string is shown, not a mistranslation. A plural form may leave the number
out entirely ("o zi", not "1 zi"); the rule still consumes the argument.

**One plural key can only agree with one number.** A sentence with two counts,
or a count and an amount, is assembled from parts: each count is inflected on
its own and then placed. `WeeklyDigest.body` and `TrendingCard.rowLabel` are
the worked examples.

## What is deliberately not translated

* **The privacy policy and the terms of service.** They are the English
  documents served at `api.snapworth.eu/privacy` and `/terms`, and a translated
  copy of a legal text is a second legal text to keep in step. The links, the
  headings in Settings, and "Last updated" around them are translated.
* **Listing copy.** `Condition.listingPhrase`, the marketplace fallback
  descriptions in `ListingService`, and the text `copyListing` puts on the
  clipboard are all input to, or output of, a listing the backend writes in
  English. Translating the app's half would produce a Romanian clause inside an
  English listing. `Condition.displayPhrase` is the same grade worded for the
  screen, and is translated.
* **Money.** `NumberFormatter.snapCurrency` is pinned to `en_US` and USD
  because the valuation is in dollars, whatever the phone's region. A Romanian
  user sees "$45–$90" because that is what the estimate is.
* **Brand, feature and stored names** — "SnapWorth", "Thrift Flip",
  "Snap → Sell", marketplace names, and every enum `rawValue`. Raw values are
  persisted, sent to the backend, and used in SwiftData predicates and mail
  subjects; each of those enums has a separate translated `label`.

  `CFBundleDisplayName` and `CFBundleName` in `InfoPlist.json` are the one
  place a brand name is nonetheless listed in all five languages, with the same
  value in each. Leaving them out is what `SWIFT_EMIT_LOC_STRINGS = YES` turns
  into churn: Xcode re-extracts them on every build as untranslated entries the
  generator does not own, and the catalogs go dirty in the working tree for no
  reason. Written down, they carry `extractionState: manual` and Xcode leaves
  them alone. Five identical values are the point, not an oversight.

`tools/check_localization.py` holds the same list, with the reasons, so a
string that is English on purpose is distinguishable from one that was
forgotten.

## Conventions, per language

All three translations use the informal second person, matching the English
copy's register — `tu` in Romanian, `tú` in Spanish, `du` in German — and never
the formal form. iOS vocabulary follows Apple's own translations for each
language. Diacritics and sharp s are always written out.

### Romanian

* iOS vocabulary: *ecranul blocat* (Lock Screen), *Activități live* (Live
  Activities), *Centru de control*, *Setări*.
* The app's own nouns: a scan is a *scanare*, a find is a *găselniță*, the
  library is *Găselnițe*, the ledger is *Flipuri*, a flip stays a *flip*.
* Quotation marks are the Romanian pair „ ", not " ".
* *ș* and *ț* are written with the comma below (U+0219, U+021B), not the
  cedilla forms.

### Spanish

* Peninsular Spanish, but avoiding anything that reads oddly in Latin America:
  *tú* rather than *vos*, and no *vosotros* — the app never addresses a group.
* iOS vocabulary: *Ajustes* (Settings), *pantalla bloqueada* (Lock Screen),
  *Actividades en vivo*, *Tiempo de uso* (Screen Time).
* The app's own nouns: a scan is an *escaneo*, a find is a *hallazgo*, the
  library is *Mis hallazgos*, the ledger is *Mis reventas*, a flip is a
  *reventa*, and thrifting is *segunda mano*.
* Quotation marks are the Spanish angular pair « », not " ".
* Opening ¿ and ¡ are always written.

### German

* *du*, lower-case, as Vinted and Kleinanzeigen address their sellers. Never
  *Sie*, which would make a resale app sound like a bank.
* iOS vocabulary: *Einstellungen* (Settings), *Sperrbildschirm* (Lock Screen),
  *Live-Aktivitäten*, *Mitteilungen* (Notifications), *Bildschirmzeit*.
* The app's own nouns: a scan is a *Scan*, a find is a *Fund*, the library is
  *Meine Funde*, the ledger is *Meine Flips*, a flip stays a *Flip*, and
  thrifting is *Second Hand*.
* Quotation marks are the German pair „ ", not " ".
* Compounds are written closed or hyphenated as German requires —
  *Wiederverkaufswert*, *Second-Hand-Tour*, *Gratis-Scan* — never spaced.

### Simplified Chinese

* Mainland conventions: `zh-Hans`, full-width punctuation （。，、：！？）, and
  Apple's own curly quotes “ ” rather than 「 」.
* A space sits between Chinese and any Latin text or digit — *扫描 %lld 次*,
  *升级 Pro* — which is Apple's own house style and is what makes a mixed line
  readable.
* iOS vocabulary: *设置* (Settings), *锁定屏幕* (Lock Screen), *实时活动*
  (Live Activities), *灵动岛* (Dynamic Island), *通知*, *屏幕使用时间*
  (Screen Time), *照片*, *邮件*.
* The app's own nouns: a scan is *扫描*, a find is *好物*, the library is
  *我的好物*, the ledger is *我的转卖*, a flip is *转卖*, a haul is *收获*, and
  thrifting is *淘货* / *二手*.
* Two arguments swap order more often here than in any other language — Chinese
  puts the period before the price in "前 3 个月 9.99 美元" — so several entries
  use positional specifiers (`%1$@`, `%2$@`). The builder checks that each one
  refers to an argument that exists and reads it as the right type.

## Adding another language

Add the code to `knownRegions` in `project.pbxproj` and to `LANGUAGES` and
`REQUIRED` in `tools/build_xcstrings.py` — `REQUIRED` being the CLDR plural
categories that language actually uses — then add a value per entry in the
three source files and regenerate. Nothing in the Swift changes; the Romanian,
Spanish and German passes each needed none.

Note that the marketplaces the app knows are eBay, Poshmark, Mercari, Depop,
Vinted, OLX, Facebook, Xianyu and Kleinanzeigen. A language whose sellers use
none of them gets a translated app that still points them at the wrong places,
which is a product change and not a translation — Chinese needed Xianyu added
before it could ship, and that was the larger half of the work.

## Listings follow the marketplace, not the interface

A generated listing is read by buyers on the platform, not by its author, so
its language follows the marketplace. Xianyu's is written in Chinese and
Kleinanzeigen's in German, whatever language the seller's phone is in.
`Condition.xianyuPhrase` and `Condition.kleinanzeigenPhrase` are separate
members from `Condition.listingPhrase` for exactly that reason — that one is an
input to English listing text — and each marketplace's entry in the backend's
`MARKETPLACE_GUIDANCE` says so to the model in as many words.

**The rule is single-country, not non-English.** Xianyu is China and
Kleinanzeigen is Germany, so each has a language. Vinted and OLX both operate
across a dozen countries and have no one language, so they stay English;
picking one from the seller's phone would put a German ad on a French listing.
eBay is the same case in reverse — international, and English is the sane
default.

The price in those listings is still in dollars. Converting would need an FX
rate the backend does not have, and a made-up yuan or euro figure is worse than
an honest dollar one.
