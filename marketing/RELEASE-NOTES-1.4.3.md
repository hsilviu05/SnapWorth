# What's New — SnapWorth 1.4.3

## Scope

1.4.2 (18) is approved and live. Three iOS pull requests since, plus one that
may or may not be in the live build:

| PR | What | Visible? |
|----|------|----------|
| #167 | Thrift Flip's net profit, ROI and fee breakdown free for everyone (#128) | **Yes** |
| #168 | Tag, the mascot: analysing overlay and the two empty states | **Yes** |
| #169 | Tag's Dark artwork: round die-cut edge (the sparkles were stepped) | **Yes**, on every scan |
| #162 | `CFBundleDisplayName` / `CFBundleName` written into the catalog | No — both are still "SnapWorth" |

#162 merged 2026-09-20 21:53, after the 1.4.2 bump (`e81d0ec`). Whether the
1.4.2 archive contains it is in Organizer, not in git (see CLAUDE.md on bump
commits). It changes nothing a user sees either way.

Not in this build: listing photo cleanup (#91, in progress), and #166 (comps
docs and stub notes), which is backend and was deployed on merge.

---

## Primary — paste into App Store Connect (`en-US`)

```
Meet Tag.

Tag, our new mascot, keeps you company while SnapWorth reads your item, and waits in My Finds and My Flips until you've saved something.

Thrift Flip now shows everything for free: net profit, ROI and the full fee breakdown for the marketplace you pick. Save a flip to My Flips straight from the verdict, free or Pro.
```

### Shorter alternative

```
Meet Tag, our new mascot — there while SnapWorth reads your item. And Thrift Flip's profit, ROI and fee breakdown are now free for everyone.
```

### The other four locales

Paste each from its own file; do not translate this block again.

| Locale | File | Block |
|---|---|---|
| `ro` | `app_store_listing.ro.md` | *What's New (Version 1.4.3) — Română* |
| `es` | `app_store_listing.es.md` | *What's New (Version 1.4.3) — Español* |
| `de` | `app_store_listing.de.md` | *What's New (Version 1.4.3) — Deutsch* |
| `zh-Hans` | `app_store_listing.zh-Hans.md` | *What's New (Version 1.4.3) — 简体中文* |

Feature names in them are the app's own, from `ios/Localization/App.json`:
*Găselnițe / Flipuri*, *Mis hallazgos / Mis reventas*, *Meine Funde / Meine
Flips*, *我的好物 / 我的转卖*. "Thrift Flip" and "Tag" stay English, as in the
app. The four blocks were written without a native reader; have each read once
before pasting.

### The description changes too

The Pro list no longer names Thrift Flip's profit numbers (#167), in all five
listing files. Description is version-scoped, so **paste the description again
for every locale** on 1.4.3, not only What's New. Pasting only What's New
leaves the live text claiming Pro reveals numbers the app now gives away.

---

## What changed, for the record

### #167 — Thrift Flip, unblurred

The blurred net profit / ROI / fees were the same arithmetic as the public
calculator on the marketing site, so the gate protected nothing. Free users
also get **Save to My Flips**; the ledger's sold-flip cap is the Pro gate this
now leads to. The paywall sheet in Thrift Flip stays for the shared daily scan
cap and now reports `scan_limit`.

Analytics, before reading any dashboard after release:

* `thrift_flip_calculated` was Pro-only and now counts every user who sees a
  verdict. It will jump; that is more people seeing it, not more use.
* The `thrift_flip` paywall trigger is retired. Scan-limit hits from Thrift
  Flip now arrive as `scan_limit`.

### #168 and #169 — Tag

AnalyzingOverlay's ring and sparkle become Tag at 96 pt, whose face follows the
rotating message (happy, wow, happy, joy); the empty My Finds and My Flips
states show Tag at 112 pt. Static under Reduce Motion, hidden from VoiceOver,
ground shadow in light mode only. The overlay always uses the Dark artwork,
which is why #169's edge fix had to ship with it.

---

## Testing on a device — required before submitting

The simulator covered layout, light/dark, Reduce Motion and CPU. These need a
phone.

### Thrift Flip, free account

- [ ] Net profit, ROI and the fee breakdown are shown, unblurred.
- [ ] **Save to My Flips** appears on a profitable verdict and the flip lands in
      My Flips with its fees.
- [ ] With today's free scan already used, scanning inside Thrift Flip **still
      opens the paywall**. This path lost its sheet once during #128 and only a
      code read caught it; it is the one to watch.

### Tag

- [ ] A real scan: Tag on the analysing overlay over a real photo — a bright one
      (white shelf, lit wall) as well as a dark one. The cream edge should read
      on both, and the sparkles should be round, not stepped.
- [ ] Settings → Accessibility → Motion → **Reduce Motion** on: Tag stands still
      and the face still changes with the message.
- [ ] VoiceOver on the overlay reads the message and "Photo captured…", and
      nothing for Tag.
- [ ] Empty My Finds and My Flips, light and dark (a fresh install, or a test
      account with nothing saved).
- [ ] Xcode's debug gauge on the empty My Finds screen: CPU stays low. The Mac
      measurement was 2–6% of one core; a device should be in that range.

---

## Pre-submit checklist

- [ ] **Archive from a checkout that contains #169 and the 1.4.3 bump.**
      `MARKETING_VERSION = 1.4.3`, `CURRENT_PROJECT_VERSION = 19`, all eight
      slots. An archive cut from an older checkout carries 1.4.2 (18), which
      App Store Connect rejects as a used build number.
- [ ] Create version 1.4.3 in App Store Connect; all five locales carry over
      from 1.4.2.
- [ ] What's New pasted per locale, as above.
- [ ] **Description re-pasted per locale** (the Pro list changed).
- [ ] Device testing above.
- [ ] Screenshots: **`store_1_four-seconds.jpg` shows the old analysing
      overlay** — the terracotta ring and sparkle that Tag replaces. It still
      shows the real flow (snap, "Analyzing the item…", "Photo captured"), so it
      is out of date rather than misleading, and 1.4.3 can ship with it.
      Retaking it with Tag on the overlay is the better first screenshot
      anyway: it is the release's most visible change. The other three (result,
      My Flips with data, My Finds with data) show nothing this release changed.

## Not in this build

* #91 listing photo cleanup — in progress. Vision's foreground mask cannot run
  in the simulator, so it needs device verification before any release.
* #166 — backend and docs; deployed on merge (commit `0f32ec1`).
