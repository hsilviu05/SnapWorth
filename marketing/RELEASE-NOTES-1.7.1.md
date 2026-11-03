# What's New — SnapWorth 1.7.1

## Scope

1.7.1 (28) is a patch release: privacy, legal and layout fixes on top of 1.7.0.
No new feature, permission, SDK or capability.

**What 27 contains.** 1.7.0 (27) went live on 2026-10-31 (owner). Its archive
predates #422, whose thread recorded on 2026-10-31 that none of its fixes were
in build 27. Every app change merged after #421 (`5951bbe`) is therefore new in
28 (`git log --first-parent 5951bbe..main -- ios/`).

### Ships in the binary (users get it only with 28)

| PR | What | Visible? |
|----|------|----------|
| #424 | **Delete my server data** in Settings → Privacy (`DELETE /me/data`). Deletes shared sales, then the device's server records; an active subscription's record is kept | **Yes** |
| #424, #427 | Privacy Policy and Terms: controller's name and address, legal bases, international transfers, your rights, the website, Railway and Vercel named, Payments and Refunds | **Yes** |
| #424 | Paywall ✕ shown at once (was after 2 s) | Yes |
| #422 | Haul / Rack / Daily guess row fits without Pro (padlock in place of PRO, or a second row); camera-off placeholder inside the viewfinder | **Yes** |
| #425 | iOS 26: Liquid Glass result toolbar without a bubble in a bubble; `Tab` API, tab bar minimises on scroll; My Finds cards are real buttons | Yes |
| #428 | Onboarding copy no longer cut off on short displays (iPhone Duo folded); `-start-tab` launch argument for CI only | Yes |
| this PR | Delete my server data's failure message says what failed (was "Couldn't delete them") | On failure |
| this PR | `chore: 1.7.1, build 28`. A lower bound for 28, not the build | No |

The backend half of #424 (`DELETE /me/data`) and the web `/privacy` and `/terms`
are already live on Railway. 1.7.0 never calls the endpoint.

---

## What's New, all five locales

`es` goes into Spanish (Spain) and Spanish (Mexico); `en-US` also into English
(U.K.). Labels match `ios/Localization/App.json`. Counts are Unicode
characters, newlines included. None of the four translations has had a native
reader yet.

| Locale | What's New | Promotional Text |
|---|---|---|
| `en` | 479 / 4000 | 166 / 170 |
| `ro` | 599 / 4000 | 162 / 170 |
| `es` | 595 / 4000 | 167 / 170 |
| `de` | 608 / 4000 | 161 / 170 |
| `zh-Hans` | 218 / 4000 | 73 / 170 |

### `en-US` (also English (U.K.))

```
• New in Settings → Privacy: Delete my server data. The Privacy Policy lists what it removes.
• Updated Privacy Policy and Terms of Service: who runs SnapWorth, your data rights, and how refunds work.
• Scan tab: Haul, Rack and Daily guess now fit on every iPhone, and the camera access message sits inside the viewfinder.
• iOS 26: cleaner buttons on results, and the tab bar tucks away as you scroll.
• Onboarding no longer cuts off text on smaller screens.
• Fixes and polish.
```

### `ro`

```
• Nou în Setări → Confidențialitate: Șterge datele mele de pe server. Politica de confidențialitate arată ce se șterge.
• Politica de confidențialitate și Termenii și condițiile, actualizate: cine administrează SnapWorth, drepturile tale asupra datelor și cum funcționează rambursările.
• Fila Scanează: Lot, Raft și Ghicitoarea zilei încap acum pe orice iPhone, iar mesajul despre accesul la cameră apare în vizor.
• iOS 26: butoane mai curate pe rezultate, iar bara de file se ascunde când derulezi.
• Prezentarea de la început nu mai taie textul pe ecranele mai mici.
• Remedieri și îmbunătățiri.
```

### `es` (Spain and Mexico)

```
• Nuevo en Ajustes → Privacidad: Eliminar mis datos del servidor. La Política de privacidad detalla qué se elimina.
• Política de privacidad y Términos del servicio actualizados: quién gestiona SnapWorth, tus derechos sobre tus datos y cómo funcionan los reembolsos.
• Pestaña Escanear: Lote, Estante y Adivinanza diaria ya caben en cualquier iPhone, y el aviso de acceso a la cámara aparece dentro del visor.
• iOS 26: botones más limpios en los resultados, y la barra de pestañas se oculta al desplazarte.
• La introducción ya no corta el texto en pantallas pequeñas.
• Correcciones y mejoras.
```

### `de`

```
• Neu unter Einstellungen → Datenschutz: Meine Serverdaten löschen. Die Datenschutzerklärung zeigt, was gelöscht wird.
• Datenschutzerklärung und Nutzungsbedingungen aktualisiert: wer SnapWorth betreibt, deine Rechte an deinen Daten und wie Erstattungen funktionieren.
• Tab „Scannen“: Stapel, Regal und Tagesrätsel passen jetzt auf jedes iPhone, und der Hinweis zum Kamerazugriff steht im Sucher.
• iOS 26: klarere Tasten bei den Ergebnissen, und die Tab-Leiste blendet sich beim Scrollen aus.
• Die Einführung schneidet auf kleineren Bildschirmen keinen Text mehr ab.
• Fehlerbehebungen und Verbesserungen.
```

### `zh-Hans`

```
• 新功能：“设置 → 隐私”中新增“删除我在服务器上的数据”。具体删除哪些数据，请见隐私政策。
• 更新了隐私政策和服务条款：说明谁在运营 SnapWorth、你的数据权利，以及退款方式。
• 扫描页：“批量”、“货架”和“每日猜价”现在在所有 iPhone 上都能完整显示；相机权限提示也移到了取景框内。
• iOS 26：结果页按钮更简洁，滚动时标签栏会自动收起。
• 在较小的屏幕上，引导页文字不再被截断。
• 问题修复与改进。
```

---

## Promotional text (can go on now)

It names Daily guess and Share to SnapWorth, both live since 1.7.0, so it can
be pasted on the live version today, for the five locales it already has.

`en-US` (also English (U.K.)):

```
New every day: Daily guess. Guess what a thrift find resells for, then see the typical range. Spotted something on Vinted? Share it to SnapWorth. One free scan a day.
```

`ro`:

```
Nou zilnic: Ghicitoarea zilei. Ghicește cât se revinde un obiect și vezi intervalul tipic. Ai văzut ceva pe OLX? Trimite-l în SnapWorth. O scanare gratuită pe zi.
```

`es` (Spain and Mexico):

```
Cada día: Adivinanza diaria. Adivina por cuánto se revende un artículo y mira el rango habitual. ¿Algo en Wallapop? Compártelo con SnapWorth. Un escaneo gratis al día.
```

`de`:

```
Jeden Tag neu: das Tagesrätsel. Rate den Wiederverkaufspreis eines Fundstücks und sieh die übliche Spanne. Was auf Kleinanzeigen entdeckt? Teil es mit SnapWorth.
```

`zh-Hans`:

```
每天一题：每日猜价。猜猜一件二手好物能卖多少钱，再看看常见价格区间。在闲鱼上看到心动好物？分享到 SnapWorth 就能估价。每天一次免费扫描。
```

---

### TestFlight — What to Test

```
Please test on this build:
• Settings → Privacy → Delete my server data: confirm, then see "Your server data was deleted."
• Same with Airplane Mode on: you should see "Couldn't delete your server data right now."
• Scan tab without Pro: Haul, Rack and Daily guess all fit on screen.
• Onboarding on a small screen: no text cut off.
```

---

## Testing on a device — required before submitting

- [ ] Settings → Privacy → **Delete my server data**, free account: the alert,
      then "Your server data was deleted." Relaunch: the app still works and
      the free scan count is unchanged (kept on purpose).
- [ ] Same on a **Pro** (Sandbox) account: the message says the
      subscription's record is kept, and Pro still works after relaunch.
- [ ] With sharing on and one sale shared: after the delete, Settings shows
      sharing off.
- [ ] Airplane Mode: the new failure message, nothing deleted locally.
- [ ] Paywall: the ✕ is there from the first frame.
- [ ] Scan tab, free account, default text size and the largest non-accessibility
      size: the Haul / Rack / Daily guess row is not clipped.
- [ ] Deny camera access: the placeholder sits inside the viewfinder and
      Open Settings works.
- [ ] iOS 26 device: result toolbar buttons have no inner circle; the tab bar
      shrinks when scrolling My Finds. iOS 17 or 18 device if you have one: tabs
      still work.
- [ ] Settings → Privacy Policy and Terms of Service: the new sections render,
      dated November 2, 2026.
- [ ] Upgrade 27 → 28 with finds saved: every find opens.

---

## Release checklist (owner)

Nothing is submitted until the last step, and that one is yours.

### 1. Before archiving
- [ ] Merge this PR, `git pull` on `main`, and confirm the bump is in the
      checkout: `grep -m1 "MARKETING_VERSION = 1.7.1" ios/SnapWorth.xcodeproj/project.pbxproj`.
      A stale checkout re-archives 27, and App Store Connect rejects it.
- [ ] CI green on that `main` commit (iOS, backend, secrets).
- [ ] No debug code: `-mock-scans` and `-start-tab` are launch arguments an App
      Store build can't receive; nothing else to strip.

### 2. Archive
- [ ] Xcode 27 (`.xcode-version`), scheme **SnapWorth**, Release. Organizer
      shows **1.7.1 (28)**.
- [ ] Record the commit and Organizer's creation date under *Build 28* below.
- [ ] Distribute → App Store Connect → Upload, once. A re-archive needs 29.

### 3. TestFlight
- [ ] 28 has no *Missing Compliance* badge.
- [ ] Paste *What to Test*; run the device checks above.

### 4. Version page
- [ ] *+ Version* → `1.7.1`, build **28**.
- [ ] What's New, all locales (above). Description, keywords, screenshots: no
      change needed (nothing in them is outdated by this release).
- [ ] **App Privacy:** no new data type. Confirm *Other User Content* (App
      Functionality, not linked, no tracking) is there from the 10-27 audit.
- [ ] Privacy Policy URL unchanged (`https://api.snapworth.eu/privacy`, already
      updated).
- [ ] App Review notes: 1.7.0's, plus "New in 1.7.1: Settings → Privacy →
      Delete my server data removes the server-side records for this device.
      No account is needed to use the app."

### 5. Release
- [ ] Phased Release for Automatic Updates: on.
- [ ] Submit for review (owner only).

### Build 28 — the archive

- **Archive commit:** _not yet_
- **Organizer creation date:** _not yet_
