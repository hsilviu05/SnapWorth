# SnapWorth

Rules that are not visible from the code you happen to be looking at.
Everything else — structure, past fixes, why a line reads the way it does — is
in the files and in `git log`, where this repo's commit messages deliberately
put it.

## The shared widget model is duplicated, and CI checks it byte-for-byte

A block of ~700 lines exists twice, verbatim, in
`ios/SnapWorth/Services/WidgetDataStore.swift` (app) and
`ios/SnapWorthWidgets/SnapWorthWidgets.swift` (extension). A widget extension
cannot import the app's module, and the app target uses explicit file
references while `SnapWorthWidgets` is a synchronised folder — so one file
cannot cheaply belong to both.

**Editing one copy and not the other breaks the build.** The `Widget model is
in sync` step in `.github/workflows/ios.yml` diffs the two
`BEGIN/END SHARED WIDGET MODEL` regions and fails on any difference. Patch
both, then prove it:

```sh
diff <(sed -n '/BEGIN SHARED WIDGET MODEL/,/END SHARED WIDGET MODEL/p' ios/SnapWorth/Services/WidgetDataStore.swift) \
     <(sed -n '/BEGIN SHARED WIDGET MODEL/,/END SHARED WIDGET MODEL/p' ios/SnapWorthWidgets/SnapWorthWidgets.swift)
```

Nothing in the block may reference a symbol that exists in only one target.

## A bug reported from the phone may already be fixed

The device lags `main`. Before treating a widget or IAP report as live, find
the fix and ask whether it shipped in the installed build:

```sh
git merge-base --is-ancestor <fix-sha> <build-sha> && echo "in that build"
```

Not hypothetical: every widget fix in 1.4.1 landed after build 15, so a phone
on build 15 reproduces six bugs that are not in the tree. Say which build a fix
needs rather than re-fixing it. iOS also caches widget snapshots across an
extension update — remove and re-add the widget before trusting what it draws.

**The `chore: <version>, build <n>` commit is not the build.** It is when the
number changed, which is a lower bound and nothing more; the archive is cut
later, from whatever was HEAD at the time. 1.4.1 is the case that proves it:
`a84d1d9` bumped to build 17 on 2026-09-14, the two widget fixes landed *after*
it the same day, and the archive that went to Apple was created 2026-09-16 —
so those fixes are in build 17, while the ancestor test against `a84d1d9` says
they are not. Xcode's Organizer holds the only record of which commit an
archive was built from, by its creation date. Take the bump commit as "no
earlier than", ask for the archive date when it matters, and never tell someone
a fix is missing from a build on the strength of the `chore:` commit alone.

A second thing Organizer will show you: an archive built from a stale checkout
carries the stale version. Re-archiving after a bump that is not in the working
tree silently produces a duplicate build number, and App Store Connect rejects
a number already used for that version.

## Strings are in a catalog, and the catalog is generated

The app is English, Romanian, Spanish, German and Simplified Chinese. A
user-facing string has to have an entry in
`ios/Localization/App.json` or `Widgets.json`, keyed by its exact English text;
`tools/build_xcstrings.py` turns those into the `.xcstrings` Xcode builds.
**Never hand-edit an entry those files own** — regenerating overwrites it, and
CI fails on the drift. Xcode does add entries of its own during a build, which
is how a key whose format specifiers were guessed wrong gets corrected; those
are kept, and printed as `untranslated:` until they are moved into the source.

The failure when a key is missing is silent: the lookup misses and the English
literal is shown. So CI also runs `tools/check_localization.py`, which fails on
a literal in a localizable position with no catalog entry. A string that is
English on purpose goes in that file's `DELIBERATELY_ENGLISH` with a reason,
not left out.

Two catalogs because a widget extension cannot read the app's resources —
`String(localized:)` resolves against `Bundle.main`. The shared widget model
above is compiled into both targets, so **a string it uses needs an entry in
both**. `ios/Localization/README.md` has the rest, including why money, listing
copy and the legal documents stay English.

**The `.xcstrings` are tracked, and never to be gitignored.** They go dirty
after every build — `SWIFT_EMIT_LOC_STRINGS = YES` makes Xcode write extracted
keys back into them — and that churn is not a reason to stop tracking them.
They are the resource the localized app is built from, and CI diffs them
against the JSON source — the check that catches a translation whose format
specifiers disagree with the English they were written from, which would crash
at display time in a language nobody here reads.

When Xcode keeps re-adding the same key, the fix is to write it into the JSON
with all five languages rather than to discard it each time. The generator then
emits `extractionState: "manual"` and Xcode leaves it alone. `CFBundleDisplayName`
and `CFBundleName` are that case — a brand name, the same value five times, and
the sameness is the point. Do read what Xcode added before discarding it: an
extraction pass is the only thing in this repo that sees the real specifiers a
call site passes.

## App Store metadata belongs to a version, not to the app

Name, subtitle, keywords, description and What's New are per-version fields.
They can only be edited while that version is editable, and **a version in
review is not**. So a language cannot be added to a submission that is already
with Apple — it waits for the next one. That is the whole reason 1.4.2 exists:
1.4.1 went to review English-only, and the four localizations had to ride the
following build.

Promotional text is the one exception. It can be swapped on a live version at
any time, but only for locales that version already has, so it cannot be used
to get a new language in early.

A localized listing ships with the localized binary, never ahead of it. A store
page in a language the app does not speak is worse than an English page.

The copy lives in `marketing/app_store_listing.md` and one file per storefront
beside it, each with character counts already verified and each naming what it
deliberately leaves out. Per-release notes are `marketing/RELEASE-NOTES-<version>.md`,
which carry the What's New to paste plus the device checks that release needs.

## Running the iOS tests

`-destination 'platform=iOS Simulator,name=iPhone 16'` is ambiguous (one name,
two arches) and makes `xcodebuild` print the device list instead of running
anything. Pass a UDID, chosen as CI chooses it — the newest installed runtime's
iPhone, because the shipped binary targets the iOS 26 SDK:

```sh
UDID=$(xcrun simctl list devices available \
  | awk '/^-- iOS /{ok=1; next} /^-- /{ok=0} ok' \
  | grep iPhone | tail -1 | grep -oE '[A-F0-9-]{36}')
xcodebuild test -project ios/SnapWorth.xcodeproj -scheme SnapWorth \
  -destination "platform=iOS Simulator,id=$UDID" \
  -only-testing:SnapWorthTests/WidgetScansLeftTests
```

`xcrun swiftc -typecheck` over a target's sources is seconds rather than
minutes for a model or copy change — a supplement, not a substitute.
