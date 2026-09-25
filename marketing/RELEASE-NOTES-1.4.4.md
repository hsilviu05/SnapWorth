# What's New — SnapWorth 1.4.4 (draft: stickers only so far)

## Scope

1.4.3 (19) is approved and live. So far 1.4.4 carries one change; add the rest
here as it lands.

| Change | What | Visible? |
|----|------|----------|
| `feature/imessage-stickers` | Tag's iMessage sticker pack: 20 stickers, four animated, in a new `SnapWorthStickers` extension | **Yes**, in Messages and the sticker drawer |

---

## Primary — paste into App Store Connect (`en-US`)

```
New: Tag stickers for iMessage. 20 of them, four animated — find them in Messages under Stickers.
```

### The other four locales

Paste each from its own file; do not translate this block again.

| Locale | File | Block |
|---|---|---|
| `ro` | `app_store_listing.ro.md` | *What's New (Version 1.4.4) — Română* |
| `es` | `app_store_listing.es.md` | *What's New (Version 1.4.4) — Español* |
| `de` | `app_store_listing.de.md` | *What's New (Version 1.4.4) — Deutsch* |
| `zh-Hans` | `app_store_listing.zh-Hans.md` | *What's New (Version 1.4.4) — 简体中文* |

"Tag" stays English, as in 1.4.3. The four blocks were written without a
native reader; have each read once before pasting.

### The description changes too

App Review Guideline 4.4 asks apps to disclose their extensions in the
marketing text, so every listing file's feature list gains an iMessage stickers
line. Description is version-scoped: **paste the description again for every
locale** on 1.4.4.

### iMessage App section — new, and required

An app that contains an iMessage extension cannot be submitted without iMessage
screenshots. On the 1.4.4 version page, in the **iMessage App** section, upload
1–10 at iPhone 6.9″ (1320 × 2868): Tag stickers in a real Messages conversation
(Guideline 2.3.4 allows the Messages UI for sticker packs). An iPhone 17 Pro Max
simulator gives that size directly: `xcrun simctl io booted screenshot`.
The iMessage app icon comes from the binary; there is nothing to upload.

---

## Testing on a device — required before submitting

### Stickers

- [ ] Messages: ＋ → Stickers → Tag's icon shows all 20; peel one onto a bubble.
- [ ] The emoji keyboard's stickers show the pack in another app (Notes will do).
- [ ] `hi-wave`, `worth-it-flip`, `yay-bounce` and `snooze` loop.
- [ ] VoiceOver reads each sticker's label ("Tag waving hello", …).
- [ ] The name under the pack's icon reads "SnapWorth".

---

## Pre-submit checklist

- [ ] `MARKETING_VERSION` and `CURRENT_PROJECT_VERSION` agree in **all twelve**
      slots of `project.pbxproj`: app, widgets and stickers, Debug and Release.
      `ExtensionBundleTests` fails in CI when one is missed.
- [ ] Organizer shows `SnapWorthStickers.appex` inside the archive. With
      automatic signing, the first archive registers
      `eu.snapworth.app.SnapWorthStickers`; the extension needs no capabilities.
- [ ] iMessage screenshots uploaded (above).
- [ ] Description re-pasted per locale.
- [ ] What's New pasted per locale.
- [ ] App Privacy unchanged: the sticker pack has no code and collects nothing.
