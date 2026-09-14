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
the fix and ask whether it shipped in the installed build — build commits are
the `chore: <version>, build <n>` ones:

```sh
git merge-base --is-ancestor <fix-sha> <build-sha> && echo "in that build"
```

Not hypothetical: every widget fix in 1.4.1 landed after build 15, so a phone
on build 15 reproduces six bugs that are not in the tree. Say which build a fix
needs rather than re-fixing it. iOS also caches widget snapshots across an
extension update — remove and re-add the widget before trusting what it draws.

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
