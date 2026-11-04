# What's New — SnapWorth 1.7.2

## Scope

1.7.2 (30) adds Tag's tips for Pro on top of the design release: Apple-style
motion and Liquid Glass on iOS 26, a Search tab, and a paywall-view count for
the operator's funnel. No new permission, SDK, entitlement or data type.

**Why 30, not 29.** 29 was archived from `081c950` (2026-11-04 18:35 EET) and
uploaded, but never submitted. Tag's tips (#448) was added to 1.7.2 after
that, so 30 replaces 29 on the version page. 29 stays in TestFlight unused.

**What 28 contains.** 1.7.1 was bumped to 28 in `3758fd4` (#434, 2026-11-03
14:13 +0200). That commit is a lower bound for 28, not the build. This file
assumes 28 was archived before #436 merged (`d250357`, 2026-11-03 19:31 +0200),
so every app change below is new in 30. If Organizer shows 28 created after
that, #436 is already in 28 and What's New should drop the four design bullets (iOS 26 look, Search tab, zoom, icons).

### Ships in the binary (users get it only with 30)

| PR | What | Visible? |
|----|------|----------|
| #436 | iOS 26: primary and ghost buttons and the camera's side controls are Liquid Glass. Below iOS 26 they keep the previous fill | **Yes** |
| #436 | iOS 26: a search-role tab (`FindsSearchView`) puts finds search at the bottom of the screen | **Yes** |
| #436 | Finds and flips zoom into their result (iOS 18+); the estimate rolls up digit by digit; My Finds cards ease in and out at the scroll edges | **Yes** |
| #436 | SF Symbol effects: copy → checkmark, Pro crown, Thrift Flip seal and profit arrow, library icon; a pulse and sweep while a scan is analysed. All skipped under Reduce Motion | Yes |
| #445 | `POST /events/paywall {trigger}` when a paywall opens, only while Share anonymous analytics is on. The server keeps daily totals per device and trigger | No |
| #445 | Privacy Policy dated November 4, 2026: says the app reports paywall opens | Yes, in Settings |
| #448 | **Tag's tips** (Pro): one resale tip per result, picked on the device from the find's own facts (no request, nothing sent). Tap Tag for the next. Free users see "Unlock Tag's tips", which opens the paywall under trigger `tag_tips`. Settings → Feel → Tag's tips hides it | **Yes** |
| #447 | Sendable warning at `ScanView.swift:346` fixed; no behaviour change | No |
| #446, this PR | `chore: 1.7.2, build 29`, then build 30. Each a lower bound, not the build | No |

The backend half of #445 (`/events/paywall`, `/week` funnel), #448's `tag_tips`
trigger in `opsstats.PAYWALL_TRIGGERS`, and the web `/privacy` must be live on
Railway before 30 is submitted, so the policy URL
matches the in-app copy. Builds 27 and 28 never call the endpoint. #435 (scans
on `gemini-3.5-flash-lite`) is backend-only and needs no app release.

#436 adds no strings, and the search tab's label is the system's. #448's strings
are in `App.json` in all five languages; Tag is a name and stays Tag.

---

## What's New, all five locales

`es` goes into Spanish (Spain) and Spanish (Mexico); `en-US` also into English
(U.K.). Counts are Unicode characters, newlines included. Settings names
(Reduce Motion, the Search tab) use Apple's own label in each language. None of
the four translations has had a native reader yet.

| Locale | What's New | Promotional Text |
|---|---|---|
| `en` | 471 / 4000 | 148 / 170 |
| `ro` | 614 / 4000 | 158 / 170 |
| `es` | 595 / 4000 | 159 / 170 |
| `de` | 613 / 4000 | 147 / 170 |
| `zh-Hans` | 223 / 4000 | 67 / 170 |

### `en-US` (also English (U.K.))

```
• New for Pro: Tag's tips. Tag gives pricing and listing advice on every find. Tap Tag for another tip.
• A new look on iOS 26: Liquid Glass buttons and camera controls.
• New Search tab on iOS 26: find any saved item from the bottom of the screen.
• Finds and flips zoom open, and the estimate counts up as a result appears.
• Livelier icons: copy, Pro and profit animate when they change. Reduce Motion turns all of it off.
• Updated Privacy Policy.
• Fixes and polish.
```

### `ro`

```
• Nou pentru Pro: Sfaturile lui Tag. Tag îți dă sfaturi despre preț și anunț pentru fiecare găsire. Atinge-l pe Tag pentru alt sfat.
• Aspect nou pe iOS 26: butoane și comenzi ale camerei în stil Liquid Glass.
• Filă nouă de Căutare pe iOS 26: găsești orice obiect salvat direct din partea de jos a ecranului.
• Găselnițele și flipurile se deschid cu un zoom, iar estimarea crește cifră cu cifră când apare rezultatul.
• Pictograme mai vii: copierea, Pro și profitul se animă când se schimbă. Opțiunea Reducere mișcare le oprește pe toate.
• Politica de confidențialitate, actualizată.
• Remedieri și îmbunătățiri.
```

### `es` (Spain and Mexico)

```
• Novedad para Pro: Consejos de Tag. Tag te aconseja sobre precio y anuncio en cada hallazgo. Toca a Tag para otro consejo.
• Nuevo diseño en iOS 26: botones y controles de la cámara con Liquid Glass.
• Nueva pestaña Buscar en iOS 26: encuentra cualquier artículo guardado desde la parte inferior de la pantalla.
• Tus hallazgos y reventas se abren con un zoom, y la estimación sube cifra a cifra al aparecer el resultado.
• Iconos más vivos: copiar, Pro y el beneficio se animan al cambiar. Reducir movimiento los desactiva todos.
• Política de privacidad actualizada.
• Correcciones y mejoras.
```

### `de`

```
• Neu für Pro: Tags Tipps. Tag gibt dir Rat zu Preis und Inserat für jeden Fund. Tippe auf Tag für einen weiteren Tipp.
• Neues Design unter iOS 26: Tasten und Kamerasteuerung in Liquid Glass.
• Neuer Tab „Suchen“ unter iOS 26: Finde jedes gespeicherte Stück direkt unten am Bildschirm.
• Funde und Flips öffnen sich mit einem Zoom, und die Schätzung zählt Ziffer für Ziffer hoch, sobald das Ergebnis erscheint.
• Lebendigere Symbole: Kopieren, Pro und Gewinn sind animiert, wenn sie sich ändern. „Bewegung reduzieren“ schaltet alles ab.
• Datenschutzerklärung aktualisiert.
• Fehlerbehebungen und Verbesserungen.
```

### `zh-Hans`

```
• Pro 新功能：Tag 的小贴士。每件好物都能收到 Tag 关于定价和发布的建议，点一下 Tag 就能看另一条。
• iOS 26 全新外观：按钮和相机控件采用液态玻璃设计。
• iOS 26 新增“搜索”标签页：在屏幕底部就能找到任何已保存的物品。
• 打开好物和转卖记录时会有缩放动画，估价会逐位跳动显示。
• 图标更生动：复制、Pro 和利润变化时会有动画。开启“减弱动态效果”即可全部关闭。
• 更新了隐私政策。
• 问题修复与改进。
```

---

## Promotional text (paste with 1.7.2, not before)

It describes Tag's tips and the iOS 26 redesign, which the live version does not have, so it
goes on with the 1.7.2 version page. Until then, keep 1.7.1's promotional text
(Daily guess, Share to SnapWorth).

`en-US` (also English (U.K.)):

```
New for Pro: Tag's tips, pricing and listing advice on every find. Plus a fresh iOS 26 look with Liquid Glass and a Search tab. One free scan a day.
```

`ro`:

```
Nou pentru Pro: sfaturile lui Tag despre preț și anunț, la fiecare găsire. Plus un aspect nou pe iOS 26, cu Liquid Glass și Căutare. O scanare gratuită pe zi.
```

`es` (Spain and Mexico):

```
Novedad para Pro: los consejos de Tag sobre precio y anuncio en cada hallazgo. Y un nuevo diseño en iOS 26 con Liquid Glass y Buscar. Un escaneo gratis al día.
```

`de`:

```
Neu für Pro: Tags Tipps zu Preis und Inserat für jeden Fund. Dazu ein neues iOS-26-Design mit Liquid Glass und Suchen-Tab. Ein Gratis-Scan pro Tag.
```

`zh-Hans`:

```
Pro 新功能：Tag 的小贴士，每件好物都有定价和发布建议。另有 iOS 26 全新液态玻璃外观和“搜索”标签页。每天一次免费扫描。
```

---

### TestFlight — What to Test

```
Please test on this build:
• iOS 26: buttons and camera controls are Liquid Glass; the Search tab finds a saved item.
• Open a find and a flip: they zoom open and the estimate counts up.
• Settings → Accessibility → Motion → Reduce Motion on: no zoom, no counting, no icon animations.
• iOS 18 device if you have one: buttons look as in 1.7.1 and there is no Search tab.
• Pro: open a result and tap Tag for the next tip. Free: "Unlock Tag's tips" opens the paywall.
```

---

## Testing on a device — required before submitting

- [ ] Remove and re-add any SnapWorth widget after installing 30 (iOS caches
      widget snapshots across an update).
- [ ] iOS 26: primary and ghost buttons and the camera's side controls are
      glass; cards stay solid. Text on glass is readable in light and dark.
- [ ] Search tab: type part of a saved find's name, open it, go back. Empty
      library and no-match states look right.
- [ ] Open a find and a flip from their lists: zoom in, swipe back, no
      flicker. Change a condition chip: the estimate rolls to the new value.
- [ ] Copy a listing: the icon turns into a checkmark. Pro crown and Thrift
      Flip seal animate once.
- [ ] Reduce Motion on: none of the above moves; everything still works.
- [ ] iOS 17 or 18 device: no Search tab, previous button fill, tabs work.
- [ ] Paywall, with Share anonymous analytics on: opens and closes normally.
      With it off: same, and `/paywall` in the bot shows no new view from
      this device.
- [ ] Settings → Privacy Policy: dated November 4, 2026, paywall sentence
      present.
- [ ] Each of the five languages: Scan, My Finds, Settings and the paywall
      have no clipped or English-only text.
- [ ] Tag's tips, Pro (Sandbox): a tip on a fresh scan, a sold flip and a
      find with a paid price; tapping Tag shows the next tip and Tag hops.
      Reduce Motion: no spring, no hop.
- [ ] Tag's tips, free: the card shows "Unlock Tag's tips"; it opens the
      paywall headed "Sell smarter with Tag". `/paywall` in the bot then
      lists a `tag_tips` view.
- [ ] Tag's tips on a covered (hidden-price) result gives no price away.
- [ ] Settings → Feel → Tag's tips off: the card is gone for Pro and free.
- [ ] Upgrade 28 → 30 with finds saved: every find opens.

---

## Release checklist (owner)

Nothing is submitted until the last step, and that one is yours.

### 1. Before archiving
- [ ] Merge this PR, `git pull` on `main`, and confirm the bump is in the
      checkout: `grep -m1 "CURRENT_PROJECT_VERSION = 30" ios/SnapWorth.xcodeproj/project.pbxproj`.
      A stale checkout re-archives 29, and App Store Connect rejects it.
- [ ] CI green on that `main` commit (iOS, backend, secrets).
- [ ] Backend from #445 and #448 deployed on Railway; `https://api.snapworth.eu/privacy`
      shows November 4, 2026.
- [ ] No debug code: `-mock-scans` and `-start-tab` are launch arguments an App
      Store build can't receive; nothing else to strip.

### 2. Archive
- [ ] Xcode 27 (`.xcode-version`), scheme **SnapWorth**, Release. Organizer
      shows **1.7.2 (30)**.
- [ ] Record the commit and Organizer's creation date under *Build 30* below.
- [ ] Distribute → App Store Connect → Upload, once. A re-archive needs 31.

### 3. TestFlight
- [ ] 30 has no *Missing Compliance* badge.
- [ ] Paste *What to Test*; run the device checks above.

### 4. Version page
- [ ] Version `1.7.2`: select build **30** (not 29).
- [ ] What's New and Promotional Text, all locales (above).
- [ ] Screenshots: the current set still shows real screens, so a change is
      not required. The iOS 26 look is the selling point of this release,
      though, so consider the CI set in `screenshots-1.7.2-ui`.
- [ ] **App Privacy:** no new data type. The paywall count is *Product
      Interaction*, already declared for TelemetryDeck. Confirm it lists
      *Analytics*, not linked to identity, no tracking.
- [ ] `PrivacyInfo.xcprivacy`: no change (Product Interaction already there).
- [ ] Privacy Policy URL unchanged (`https://api.snapworth.eu/privacy`).
- [ ] App Review notes: 1.7.1's. Nothing new needs explaining.

### 5. Release
- [ ] Phased Release for Automatic Updates: on.
- [ ] Submit for review (owner only).

### Build 29 — archived, uploaded, not submitted

- **Archive commit:** `081c950`
- **Organizer creation date:** 2026-11-04 18:35:38 EET

### Build 30 — the archive

- **Archive commit:** _not yet_
- **Organizer creation date:** _not yet_
