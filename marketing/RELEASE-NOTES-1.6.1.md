# What's New — SnapWorth 1.6.1

## Scope

1.6.0 (24) is **live** (owner, 2026-10-24). 1.6.1 (25) is a patch release:
two small app fixes, plus the store listing rework from #386, which could not
go onto 1.6.0 because a version in review cannot take a new name, subtitle or
keywords.

**What 24 contains is recorded.** It was archived from `2d33f7a` (the merge
of #373). Every app change below is new since then, from
`git log --first-parent 2d33f7a..main -- ios/`.

### Ships in the binary (users get it only with 25)

| PR | What | Visible? |
|----|------|----------|
| [#385](https://github.com/hsilviu05/SnapWorth/pull/385) | The rating request is never shown over the paywall, a share sheet, the tag camera or Legit check. It waited two seconds after the price appeared, and a sheet opened in that pause didn't cancel it. It now checks again after the pause | Only to someone who opens a sheet within two seconds of the price |
| [#383](https://github.com/hsilviu05/SnapWorth/pull/383) | "Why this price" gives the reason "the brand was not confirmed from a label photo" in all five languages. The server already caps such clothing scans at Medium and sends this reason to 24, which shows the server's English clause instead | Yes, in Pro's "Why this price", non-English phones |
| [#374](https://github.com/hsilviu05/SnapWorth/pull/374) | Xcode 27 project upgrade, committed. 24 was archived with it uncommitted in the working tree | No |
| this PR | `chore: 1.6.1, build 25`, a lower bound for 25, not the build | No |

### Store listing (version-scoped, pasted onto 1.6.1)

From [#386](https://github.com/hsilviu05/SnapWorth/pull/386): new name
`SnapWorth: Thrift Resale Value`, subtitle `What's It Worth? Price Checker`,
keywords with no other company's name (Guideline 2.3.7), a new English (U.K.)
localization, separate Spanish (Spain) and Spanish (Mexico) keyword lines,
and the new screenshot order with Rack scan and Legit check frames.

### Not in this build

* Anything merged after `f889ac8`, the archive commit recorded below.

---

## What's New, all five locales

Paste each block into its locale's What's New field on the 1.6.1 version
page. `es` goes into both Spanish (Spain) and Spanish (Mexico); `en-US` also
goes into English (U.K.). "Why this price" is the app's own wording from
`ios/Localization/App.json`. None of the four translations has had a native
reader.

Counts are Unicode characters, newlines included. The limit is 4000.

| Locale | Characters |
|---|---|
| `en-US` | 161 |
| `ro` | 194 |
| `es` | 214 |
| `de` | 217 |
| `zh-Hans` | 58 |

### `en-US`

```
• The rating request no longer pops up over the paywall or a share sheet.
• "Why this price" now says when a garment's brand wasn't confirmed from a label photo.
```

### `ro`

```
• Cererea de evaluare nu mai apare peste ecranul Pro sau peste fereastra de partajare.
• „De ce prețul ăsta” îți spune acum când marca unui articol nu a fost confirmată dintr-o poză a etichetei.
```

### `es`

```
• La solicitud de valoración ya no aparece sobre la pantalla de Pro ni sobre la hoja de compartir.
• «Por qué este precio» ahora indica cuándo la marca de una prenda no se ha confirmado con una foto de la etiqueta.
```

### `de`

```
• Die Bitte um eine Bewertung erscheint nicht mehr über dem Pro-Bildschirm oder dem Teilen-Menü.
• „Warum dieser Preis“ zeigt jetzt an, wenn die Marke eines Kleidungsstücks nicht durch ein Etikettfoto bestätigt wurde.
```

### `zh-Hans`

```
• 评分请求不再出现在 Pro 页面或分享菜单之上。
• “为什么是这个价”现在会说明衣物品牌何时未经标签照片确认。
```

### TestFlight — What to Test

```
Please test on this build:
• Scan an item, and within two seconds of the price appearing, open the paywall or Share. No rating request appears over it.
• Pro, phone in Romanian, Spanish, German or Chinese: scan a branded garment without a tag photo. "Why this price" shows the brand reason in that language.
```

---

## Testing on a device — required before submitting

Owner checks, on the TestFlight build against production.

### Rating prompt ([#385](https://github.com/hsilviu05/SnapWorth/pull/385))

`ReviewPrompt` keeps a 60-day gap, and in TestFlight `requestReview` never
shows the real sheet, so a missing prompt there proves nothing. Run these on
a **development build from the same commit** with a fresh install.

- [ ] Fresh scan, High or Medium confidence, no sheet opened: the rating
      request appears about two seconds after the price.
- [ ] Fresh install again: open the paywall from "Unlock why this price"
      within two seconds of the price. No rating request over the paywall,
      and none after closing it.
- [ ] Same with Share, and with the tag camera.

### Confidence reason ([#383](https://github.com/hsilviu05/SnapWorth/pull/383))

- [ ] Pro, phone language German (or any of the four): scan a branded
      garment, front photo only. Confidence is at most Medium, and "Warum
      dieser Preis" lists the label-photo reason in German, not English.
- [ ] Same garment with a tag photo: the reason is gone.

### Upgrade

- [ ] Install 25 over 24 (or 1.5.3) with finds saved: every find opens, a
      saved Legit check is still on its find.

---

## App Store Connect walkthrough (owner)

In order. Nothing here is submitted until the last step, and that one is yours.

### 0. Before touching 1.6.1

- [x] **24 is live** (owner, 2026-10-24), so 1.6.1 can be created.
- [ ] **Record what 1.6.0 shipped with** in `app_store_listing.md`: its
      subtitle (the TODO under *Subtitle*) and its first screenshot. Neither
      is in git, and the search-term comparison needs the before value.
- [ ] **Promotional text** can be swapped on the live version now, without
      waiting for 1.6.1: the search-led line in `app_store_listing.md`. Only
      for locales the live version already has, so not en-GB yet.

### 1. Archive and upload build 25

- [ ] `git pull` on `main` after this PR merges, then confirm the bump is in
      the checkout: `git merge-base --is-ancestor <this PR's merge commit> HEAD && echo ok`.
      A stale checkout silently re-archives 24, and App Store Connect rejects
      a build number already used.
- [ ] Archive with **Xcode 27** (`.xcode-version`), Release configuration.
      Check Organizer shows **1.6.1 (25)**.
- [x] Record the commit and Organizer's creation date under *Build 25* below
      (`f889ac8`, 2026-10-25 19:39 EET). Tagging it is still open.
- [ ] Distribute → App Store Connect → Upload, **once**. If a rejection needs
      a new archive, bump to 26 first.
- [ ] Wait for the "has completed processing" email (usually 5–30 minutes)
      and answer the export compliance question the same way as for 24.

### 2. TestFlight validation

- [ ] 25 appears under TestFlight with no *Missing Compliance* or warning
      badge.
- [ ] Paste *What to Test* (above). Internal testers, or a small invite-only
      external group. **No public TestFlight link** while
      `SANDBOX_ENTITLEMENTS` is `bounded` (RUNBOOK §17): every TestFlight
      tester with a Sandbox subscription is Pro in production.
- [ ] Run the device checks above and tick them.

### 3. Create the 1.6.1 version

- [ ] *+ Version or Platform* → iOS → `1.6.1`. App Store Connect copies
      1.6.0's metadata and screenshots across; everything below replaces them.
- [ ] Build: select **25**.

### 4. Listing, every locale

From `app_store_listing.md` and one file per locale. Run
`python3 tools/check_listing_limits.py` first; every field must pass.

- [ ] **English (U.S.)**: name, subtitle, keywords, description,
      promotional text.
- [ ] **Add English (U.K.)** from the language menu at the top right of the
      version page, then paste `app_store_listing.en-GB.md`.
- [ ] **German, Romanian, Chinese (Simplified)**: the new keyword lines,
      and anything else their file marks as changed.
- [ ] **Spanish (Spain)** and **Spanish (Mexico)**: the shared text, and
      each its own keyword line.
- [ ] Name decision: the new name reverses the 2026-09-28 call to keep
      "SnapWorth: Resell & Flip". Keep or revert before pasting.
- [ ] Native readers have seen the German and Spanish promotional text,
      the Spain and Mexico keywords and the en-GB copy, or you accept
      shipping them unread.

### 5. Screenshots

- [ ] Capture frame 5 (Rack scan picks) and frame 6 (Legit check verdict
      with "not a certification of authenticity" visible) on a Pro build,
      from the shipping binary (Guideline 2.3.3).
- [ ] Rebuild captions with `marketing/build_screenshots.py` and upload in
      the order in `app_store_listing.md` → *Screenshots — proposed order*,
      for the 6.9" display (the other sizes scale from it).
- [ ] Every Pro frame says Pro on the frame (2.3.2). Repeat per locale that
      has localized screenshots; the others fall back to English.

### 6. App Privacy and privacy manifest

- [ ] Nothing changes in 1.6.1: neither fix collects or sends new data, and
      `PrivacyInfo.xcprivacy` is untouched since 24. Open *App Privacy* and
      confirm it still reads *Photos or Videos*, App Functionality, not
      linked, no tracking, as for 24. No new purpose strings.

### 7. What's New and review notes

- [ ] What's New in all five locales, plus en-GB (blocks above).
- [ ] App Review notes: copy 1.6.0's (`RELEASE-NOTES-1.6.0.md`). Keep the
      reference shirt photo attached.

### 8. Release options

- [ ] *Phased Release for Automatic Updates*: **on**. It spreads automatic
      updates over seven days and can be paused if Organizer shows a crash;
      anyone can still update by hand from the store.
- [ ] *Version release*: manually release, if you want to choose the day the
      new name goes live; otherwise automatic.

### 9. Submit (owner only)

- [ ] *Add for Review* → *Submit to App Review*. Claude does not do this step.

### Build 25 — the archive

The `1.6.1, build 25` commit in this PR is a lower bound, not the build
(CLAUDE.md). This record is the build.

- **Archive commit** (`git rev-parse HEAD` at archive time):
  `f889ac857f11a4634f34b31b3b12c3d62ed79f74` (the merge of #389)
- **Organizer creation date:** 2026-10-25 19:39:31 EET (17:39:31 UTC),
  `SnapWorth 25-10-26, 7.39 PM.xcarchive`, `Info.plist` reads 1.6.1 (25)
- **How the commit is known:** the archive records no commit. The main
  checkout's reflog shows `pull --tags origin main` fast-forwarding to
  `f889ac8` at 19:38:48, 43 seconds before the archive, with no later move
  and a clean working tree. `f889ac8` was also `origin/main`'s tip all day,
  and it contains `73274d6` (`chore: 1.6.1, build 25`) and #390
  (`.gitignore` only, nothing in the binary).
- **Tag** it (not done yet):

  ```sh
  git tag build-25 f889ac857f11a4634f34b31b3b12c3d62ed79f74 && git push origin build-25
  ```

---

## After approval (owner)

- [ ] A week after release: Acquisition → Sources → Search terms, and the
      impression-to-tap rate, against what was recorded in step 0.
- [ ] Organizer → Crashes on 1.6.1.
