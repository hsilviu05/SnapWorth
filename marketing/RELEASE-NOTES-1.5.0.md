# What's New — SnapWorth 1.5.0

## Scope

1.4.3 (19) is approved and live. 1.5.0 (20) carries one change, and it is a
minor version because it adds a feature:

| Change | What | Visible? |
|----|------|----------|
| `feature/imessage-stickers` | Tag's iMessage sticker pack: 20 stickers, four animated, in a new `SnapWorthStickers` extension | **Yes**, in the sticker drawer everywhere |

---

## Primary — paste into App Store Connect (`en-US`)

```
New: Tag stickers for iMessage. 20 of them, four animated, for every find worth showing off. They're with your stickers on the emoji keyboard.
```

### The other four locales

Paste each from its own file; do not translate this block again.

| Locale | File | Block |
|---|---|---|
| `ro` | `app_store_listing.ro.md` | *What's New (Version 1.5.0) — Română* |
| `es` | `app_store_listing.es.md` | *What's New (Version 1.5.0) — Español* |
| `de` | `app_store_listing.de.md` | *What's New (Version 1.5.0) — Deutsch* |
| `zh-Hans` | `app_store_listing.zh-Hans.md` | *What's New (Version 1.5.0) — 简体中文* |

"Tag" stays English, as in 1.4.3. The four blocks were written without a
native reader; have each read once before pasting.

**Where the stickers live.** They are in the system sticker drawer: the
stickers section of the emoji keyboard, in Messages and in any other app. In
the iOS 27 simulator the ＋ menu in Messages has no Stickers entry, and the
pack is not in its app list either, by design: with both presentation
contexts declared, Messages shows a sticker pack only in the media context.
The What's New lines point to the emoji keyboard for that reason.

### The description changes too

App Review Guideline 4.4 asks apps to disclose their extensions in the
marketing text, so every listing file's feature list gains an iMessage stickers
line. Description is version-scoped: **paste the description again for every
locale** on 1.5.0.

### iMessage App section

App Store Connect's help lists iMessage screenshots as required for an app
that contains an iMessage extension. If the submission is blocked for them,
upload 1–10 at iPhone 6.9″ (1320 × 2868) in the **iMessage App** section on
the 1.5.0 version page: the sticker drawer open on Tag's pack in Messages
(Guideline 2.3.4 allows the Messages UI for sticker packs). An iPhone 18 Pro
Max simulator gives that size directly: `xcrun simctl io booted screenshot`.
The iMessage app icon comes from the binary; there is nothing to upload.

---

## Testing on a device — required before submitting

### Stickers (TestFlight build)

- [ ] Messages: emoji keyboard → stickers → Tag's icon shows all 20; send one
      and peel one onto a bubble.
- [ ] The same pack appears in the emoji keyboard of another app (Notes will do).
- [ ] `hi-wave`, `worth-it-flip`, `yay-bounce` and `snooze` loop.
- [ ] VoiceOver reads each sticker's label ("Tag waving hello", …).

---

## Pre-submit checklist

- [ ] **Archive from `main` after the merge of `feature/imessage-stickers`**,
      which carries the 1.5.0 bump. `MARKETING_VERSION = 1.5.0` and
      `CURRENT_PROJECT_VERSION = 20` in **all twelve** slots of
      `project.pbxproj`: app, widgets and stickers, Debug and Release.
      `ExtensionBundleTests` fails in CI when one is missed.
- [ ] Organizer shows `SnapWorthStickers.appex` inside the archive. With
      automatic signing, the first archive registers
      `eu.snapworth.app.SnapWorthStickers`; the extension needs no capabilities.
- [ ] Create version 1.5.0 in App Store Connect; all five locales carry over.
- [ ] What's New pasted per locale, as above.
- [ ] Description re-pasted per locale.
- [ ] Phased release on: a seven-day rollout that can be paused.
- [ ] App Privacy unchanged: the sticker pack has no code and collects nothing.
