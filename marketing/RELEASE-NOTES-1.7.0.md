# What's New — SnapWorth 1.7.0

## Scope

1.7.0 (27) makes Tag, tilted inside camera corners, the app icon, and lets
people pick another one in Settings → App icon. It also carries everything
merged into the app since the `1.6.2, build 26` bump: Share to SnapWorth,
Siri and Spotlight, visual intelligence, the haul slideshow, "Type it instead"
on onboarding, and a second rating moment.

**Versioning.** A minor release, not a patch: the share extension, App
Shortcuts, visual intelligence and the icon picker are new features. The
owner first asked for 1.6.3 and chose 1.7.0 on 2026-10-30. The build number
continues from 26.

**What 26 contains is known.** 1.6.2 (26) is approved (owner, 2026-10-30).
Organizer created its archive on 2026-10-28 at 23:53, and no app change was
merged between the bump `296a89a` and then (`RELEASE-NOTES-1.6.2.md`,
*Build 26*). Every change below merged on 2026-10-29 or later, so all of it is
new in 27 (`git log --first-parent 296a89a..main -- ios/`).

### Ships in the binary (users get it only with 27)

| PR | What | Visible? |
|----|------|----------|
| this PR | **New app icon and an icon picker.** Primary icon: Tag inside viewfinder corners on terracotta, with a dark-mode and a tinted variant. Settings → App icon offers Tag, Night, Wow and Classic (the icon before 1.7.0). People who had saved finds before updating get a one-time alert ("Choose your app icon"). New installs never see it. `app_icon_changed{icon, source}` | **Yes** |
| [#410](https://github.com/hsilviu05/SnapWorth/pull/410) | **Share to SnapWorth**: a share extension takes one photo or screenshot from any app. The app values it on next open; a notification offers it in one tap | **Yes** |
| [#412](https://github.com/hsilviu05/SnapWorth/pull/412) | **Siri and Spotlight**: a "Value an item" App Shortcut opens the camera. **Visual intelligence** (iOS 26, Pro): "Value this with SnapWorth" on a screenshot or camera search | **Yes** |
| [#409](https://github.com/hsilviu05/SnapWorth/pull/409) | **Share as slideshow** for a haul: hook slide, up to four finds, the total card | Yes, Haul (Pro) |
| [#408](https://github.com/hsilviu05/SnapWorth/pull/408) | Onboarding's last page offers "Nothing to scan right now? Type it instead" | New users |
| [#413](https://github.com/hsilviu05/SnapWorth/pull/413) | The rating request can also come when a flip is saved as sold at a profit | Rarely (iOS decides) |
| this PR | `chore: 1.7.0, build 27`. A lower bound for 27, not the build | No |

### New target and capabilities (must be in place before archiving)

- **`eu.snapworth.app.SnapWorthShare`** registered in Certificates, Identifiers
  & Profiles with the App Group `group.eu.snapworth.app` (#410). Without it
  the archive fails to sign, or the extension cannot hand the photo over.
- The share extension has its own `PrivacyInfo.xcprivacy`. App Privacy already
  declares Photos or Videos for App Functionality, which covers it.

### Store listing (version-scoped, pasted onto 1.7.0)

The description in every locale gains the new features: share from another
app, Siri and Spotlight, and for Pro the haul slideshow and visual
intelligence. Paste each locale's Description from its
`app_store_listing.<locale>.md` file. `zh-Hans` leaves out visual intelligence,
because Apple Intelligence is not offered in mainland China.
`python3 tools/check_listing_limits.py` passes. The tightest is `de` at 3,990
of 4,000.

The icon on the store page comes from the binary, so it changes when 27 goes
live. Nothing to upload separately.

---

## What's New, all five locales

Paste each block into its locale's What's New field on the 1.7.0 version page.
`es` goes into both Spanish (Spain) and Spanish (Mexico). `en-US` also goes
into English (U.K.). Icon names and the settings path match the app's own
labels in `ios/Localization/App.json`. Siri's spoken phrases are English only
(no `AppShortcuts.xcstrings` yet), so the other locales name the shortcut, not
a phrase. None of the four translations has had a native reader yet. In
German, "Tag" also means "day"; it stays because it is the mascot's name.

Counts are Unicode characters, newlines included. The limit is 4000.

| Locale | Characters |
|---|---|
| `en-US` | 457 |
| `ro` | 532 |
| `es` | 505 |
| `de` | 538 |
| `zh-Hans` | 173 |

### `en-US`

```
• A new look: meet Tag, our new app icon. Prefer another? Pick Night, Wow or Classic in Settings → App icon.
• Share to SnapWorth: share a photo or screenshot from Vinted, eBay or any other app and see what it's worth.
• Siri and Spotlight: say "What's this worth in SnapWorth" to open the camera.
• Pro: share a haul as a slideshow for TikTok and Instagram.
• Pro, iOS 26: value a screenshot or a camera search with visual intelligence.
• Fixes and polish.
```

### `ro`

```
• Aspect nou: fă cunoștință cu Tag, noua pictogramă a aplicației. Preferi alta? Alege Noapte, Uau sau Clasic în Setări → Pictograma aplicației.
• Distribuie în SnapWorth: trimite o poză sau o captură de ecran din Vinted, OLX sau orice altă aplicație și afli cât valorează.
• Siri și Spotlight: scurtătura „Evaluează un obiect” deschide camera.
• Pro: distribuie lotul ca prezentare pentru TikTok și Instagram.
• Pro, iOS 26: evaluează o captură de ecran sau o căutare cu camera prin inteligența vizuală.
• Remedieri și îmbunătățiri.
```

### `es`

```
• Nuevo look: te presentamos a Tag, el nuevo icono de la app. ¿Prefieres otro? Elige Noche, Wow o Clásico en Ajustes → Icono de la app.
• Compartir con SnapWorth: comparte una foto o captura desde Vinted, Wallapop o cualquier otra app y descubre cuánto vale.
• Siri y Spotlight: el atajo «Valorar un artículo» abre la cámara.
• Pro: comparte tu lote como carrusel para TikTok e Instagram.
• Pro, iOS 26: valora una captura o una búsqueda con la cámara con la inteligencia visual.
• Correcciones y mejoras.
```

### `de`

```
• Neuer Look: Das ist Tag, unser neues App-Symbol. Lieber ein anderes? Wähl Nacht, Wow oder Klassisch unter Einstellungen → App-Symbol.
• Mit SnapWorth teilen: Teil ein Foto oder einen Screenshot aus Vinted, Kleinanzeigen oder jeder anderen App und sieh, was es wert ist.
• Siri und Spotlight: Der Kurzbefehl „Artikel bewerten“ öffnet die Kamera.
• Pro: Teil deinen Stapel als Diashow für TikTok und Instagram.
• Pro, iOS 26: Bewerte einen Screenshot oder eine Kamerasuche mit visueller Intelligenz.
• Fehlerbehebungen und Verbesserungen.
```

### `zh-Hans`

No visual intelligence line: Apple Intelligence is not offered in mainland China.

```
• 新面貌：来认识一下 Tag，我们的新应用图标。想换一个？在“设置 → 应用图标”中选择“夜间”、“哇”或“经典”。
• 分享到 SnapWorth：从闲鱼或任何 App 分享照片或截图，就能看到它值多少钱。
• Siri 与聚焦搜索：“估价一件物品”快捷指令可直接打开相机。
• Pro：把整批好物做成幻灯片，一次分享。
• 问题修复与改进。
```

---

## Promotional text, all locales (after 1.7.0 is live)

Not tied to a build, but paste it only once 27 is live, or the page promises a
feature people can't find. It can only go on locales the live version already
has. Counts are Unicode characters; the limit is 170.

| Locale | Characters |
|---|---|
| `en-US`, `en-GB` | 145 |
| `ro` | 144 |
| `es` | 144 |
| `de` | 149 |
| `zh-Hans` | 52 |

`en-US` and `en-GB`:

```
Spotted something on Vinted or eBay? Share the photo to SnapWorth and see what it's worth in seconds. One free scan every day, no account needed.
```

`ro`:

```
Ai văzut ceva pe Vinted sau OLX? Trimite poza în SnapWorth și afli în câteva secunde cât valorează. O scanare gratuită în fiecare zi, fără cont.
```

`es` (Spain and Mexico):

```
¿Has visto algo en Vinted o Wallapop? Comparte la foto con SnapWorth y descubre en segundos cuánto vale. Un escaneo gratis cada día, sin cuenta.
```

`de`:

```
Etwas auf Vinted oder Kleinanzeigen entdeckt? Teil das Foto mit SnapWorth und sieh in Sekunden, was es wert ist. Ein Gratis-Scan pro Tag, ohne Konto.
```

`zh-Hans`:

```
在闲鱼上看到心动好物？把照片分享到 SnapWorth，几秒就知道值多少钱。每天一次免费扫描，无需注册。
```

---

### TestFlight — What to Test

```
Please test on this build:
• The new app icon on the Home Screen, in light, dark and tinted mode.
• Settings → App icon: switch to Night, Wow and Classic, then back to Tag.
• Share a photo from Photos or a screenshot to SnapWorth, then open the app: it is valued.
• Ask Siri "What's this worth in SnapWorth": the camera opens.
• Haul (Pro): Share as slideshow.
```

---

## Testing on a device — required before submitting

Owner checks, on the TestFlight build against production. Remove and re-add
any widget first: iOS caches widget snapshots across an update (CLAUDE.md).

### App icon (this PR)

- [ ] Fresh install: the Home Screen shows Tag on terracotta. Long-press →
      Edit → Customize: Dark shows the espresso version, Tinted a gray one
      that takes the tint.
- [ ] Settings → App icon: four rows with thumbnails, a check on Tag. Pick
      Night: iOS shows its "You have changed the icon" alert, the Home Screen
      icon changes, the check moves. Same for Wow and Classic, then back to Tag.
- [ ] Kill and relaunch after picking Wow: the check is still on Wow.
- [ ] **Upgrade from 26 with finds saved**: about 1.5 s after launch, "Choose
      your app icon" appears once. "Choose icon" opens the picker as a sheet
      with Done. Relaunch: no alert again.
- [ ] **Fresh install**: no alert, not even after a first scan and a relaunch.
- [ ] Phone in German (or any of the four): the row, picker, footer and alert
      are translated, and "Night", "Wow", "Classic" match the alert text.
- [ ] VoiceOver on the picker: each row reads its name; the current one adds
      "Selected".

### Share, Siri, visual intelligence, slideshow (#408–#413)

The device checks for these are in each PR. At minimum:

- [ ] Share a photo from Photos → SnapWorth: the extension confirms, and the
      app values it on next open (free plan spends the daily scan).
- [ ] Spotlight: type "Value an item": the shortcut opens the camera.
- [ ] iOS 26 device, free account: visual intelligence → "Value this with
      SnapWorth" → the paywall. Pro account: the picture is valued.
- [ ] Haul summary → Share as slideshow: the slides arrive in order.

### Upgrade

- [ ] Install 27 over 26 with finds saved: every find opens, ledger entries
      are intact.

---

## App Store Connect walkthrough (owner)

In order. Nothing here is submitted until the last step, and that one is yours.

### 0. Before touching 1.7.0

- [ ] **1.6.2 is live** (approved 2026-10-30). A new version cannot be
      created while another is waiting for or in review.

### 1. Identifiers and archive

- [ ] `eu.snapworth.app.SnapWorthShare` exists with the App Group (above).
- [ ] `git pull` on `main` after this PR merges, then confirm the bump is in
      the checkout: `git merge-base --is-ancestor <this PR's merge commit> HEAD && echo ok`.
      A stale checkout silently re-archives 26, and App Store Connect rejects
      a build number already used.
- [ ] Archive with **Xcode 27** (`.xcode-version`), scheme **SnapWorth** (not
      *Mock scans*), Release configuration. Organizer shows **1.7.0 (27)**,
      and its icon is the new Tag icon.
- [ ] Record the commit and Organizer's creation date under *Build 27* below.
- [ ] Distribute → App Store Connect → Upload, **once**. If a rejection needs
      a new archive, bump to 28 first.

### 2. TestFlight validation

- [ ] 27 appears with no *Missing Compliance* or warning badge.
- [ ] Paste *What to Test* (above). No public TestFlight link while
      `SANDBOX_ENTITLEMENTS` is `bounded` (RUNBOOK §17).
- [ ] Run the device checks above and tick them.

### 3. Version, listing, privacy

- [ ] *+ Version or Platform* → iOS → `1.7.0`. Build: **27**.
- [ ] What's New in all five locales, plus en-GB (blocks above).
- [ ] Description in every locale, from the listing files.
- [ ] App Privacy: no change. The icon picker collects nothing. The share
      extension's photo is the same Photos or Videos already declared.
- [ ] No new permission, so no new purpose string.
- [ ] App Review notes: copy 1.6.2's, and add: "New in 1.7.0: Settings → App
      icon changes the Home Screen icon. Share a photo to SnapWorth from the
      Photos app to value it."

### 4. Release options

- [ ] *Phased Release for Automatic Updates*: **on**.
- [ ] If a Product Page Optimization test is running, end it first: the new
      icon changes the search card mid-test.

### 5. Submit (owner only)

- [ ] *Add for Review* → *Submit to App Review*. Claude does not do this step.

### Build 27 — the archive

The `1.7.0, build 27` commit in this PR is a lower bound, not the build
(CLAUDE.md). Fill this in at archive time.

- **Archive commit** (`git rev-parse HEAD` at archive time): _not yet_
- **Organizer creation date:** _not yet_

---

## After approval (owner)

- [ ] Paste the promotional text above, every live locale.
- [ ] Profile pictures: `snapworth-profile-1080.png` (project files,
      `icon-directions/social/`) on Instagram, Snapchat, X and TikTok, the
      same day the new icon goes live.
- [ ] A week after release: impressions → product page views (tap-through,
      1.8% for Jul 20 – Oct 29) against the weeks before. The subtitle changed
      too, so the two effects can't be fully separated. A Product Page
      Optimization test of Classic against Tag can, now that both icons are
      in the binary.
- [ ] Analytics: `app_icon_changed` by `icon`, to see which icons people
      actually choose.
