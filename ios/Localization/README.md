# Localization

The app ships in English (`en`, the development language) and Romanian (`ro`).

## Where the strings live

`App.json` and `Widgets.json` in this folder are the **source**. They are plain
JSON, one entry per key, and they are what a change is written in and reviewed
from:

```json
"Reveal the estimate": {
  "comment": "Button under a covered price.",
  "en": "Reveal the estimate",
  "ro": "Arată estimarea"
}
```

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
  "ro": { "one": "o zi", "few": "%lld zile", "other": "%lld de zile" }
}
```

The builder requires `one`/`other` for English and `one`/`few`/`other` for
Romanian, and refuses a translation whose format specifiers disagree with the
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

`tools/check_localization.py` holds the same list, with the reasons, so a
string that is English on purpose is distinguishable from one that was
forgotten.

## Romanian conventions used here

* Second person singular throughout ("Scanează", "Ai atins limita"), matching
  the English copy's register. Never the formal plural.
* iOS vocabulary follows Apple's Romanian: *ecranul blocat* (Lock Screen),
  *Activități live* (Live Activities), *Centru de control*, *Setări*.
* The app's own nouns: a scan is a *scanare*, a find is a *găselniță*, the
  library is *Găselnițe*, the ledger is *Flipuri*, a flip stays a *flip*.
* Quotation marks are the Romanian pair „ ", not " ".
* Diacritics are always written, including *ș* and *ț* with comma below
  (U+0219, U+021B) rather than the cedilla forms.

## Adding a third language

Add the code to `knownRegions` in `project.pbxproj`, add the plural categories
the language needs to `REQUIRED` in `tools/build_xcstrings.py`, add a value per
entry in the three source files, and regenerate. Nothing in the Swift changes.
