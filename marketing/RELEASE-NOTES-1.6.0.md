# What's New — SnapWorth 1.6.0

## Scope

1.5.3 (23) is live. 1.6.0 (24) is a minor release because it adds two Pro
features, Rack scan and Legit check. It also carries the fix for the one
crash Organizer shows on 1.5.3, and the store-page changes from the
2026-10-19 analytics review (icon, subtitle, first screenshot).

**What 23 contains is recorded.** It was archived from `70aa61e`
(`RELEASE-NOTES-1.5.3.md`, *Build 23*). Every app change below is new
since then, from `git log --merges --first-parent 70aa61e..main` filtered to
merges that touch the app's sources.

### Ships in the binary (users get it only with 24)

| PR | What | Visible? |
|----|------|----------|
| [#345](https://github.com/hsilviu05/SnapWorth/pull/345) | **Rack scan** (#89): a "Rack" pill beside Haul. One photo of a rail, shelf or bin, then up to three numbered picks with where each is, a rough range and why it stands out. "Scan this one" returns to the camera with a banner naming the item. Pro; free users get the paywall (`rack_scan`). Nothing from a rack read is saved | **Yes** |
| [#343](https://github.com/hsilviu05/SnapWorth/pull/343) | **Legit check** (#90): a card on a result for commonly faked brands, and on any find that already has one. Four guided close-ups (logo, inside label, stitching, hardware), at least two needed, then a verdict, its reasons and any photo too unclear to judge, always with "An observation about these photos, not a certification of authenticity." Stored on the find (`legitCheckData`, a new optional field). Pro; free users get the paywall (`legit_check`). The in-app privacy policy says photos may be sent for a check (`f8e9489`) | **Yes** |
| [#362](https://github.com/hsilviu05/SnapWorth/pull/362) | Photos and the preview follow how the phone is held (`AVCaptureDevice.RotationCoordinator`), so they are level on iPhone Duo's inner display; My Finds fits more columns on a wide screen (#361) | On iPhone Duo |
| [#367](https://github.com/hsilviu05/SnapWorth/pull/367) | "Copy listing draft" writes the condition the user picked instead of the scan's notes | Yes, once a condition is picked |
| [#357](https://github.com/hsilviu05/SnapWorth/pull/357) | In-app privacy policy, dated October 19, 2026: TypeSafe (Jev) listed as a processor for the listing-claims check. That check is server-side and behind a flag | Yes, in Settings → Privacy Policy |
| [#373](https://github.com/hsilviu05/SnapWorth/pull/373) | A result sheet whose find was deleted closes instead of reading it. Fixes 1.5.3's only Organizer crash, in `ScanResult.imageData` from `ResultView` (one device, 2026-10-20). This PR also bumps to 1.6.0 (24) | Only to whoever hit the crash |
| [#370](https://github.com/hsilviu05/SnapWorth/pull/370) | Views and Services grouped into feature folders. Files moved, no code change | No |

### Server and website (deployed on merge; already serving 23)

| PR | What |
|----|------|
| [#345](https://github.com/hsilviu05/SnapWorth/pull/345), [#343](https://github.com/hsilviu05/SnapWorth/pull/343) | `POST /rack` and `POST /legit`, Pro-only (402 otherwise). 23 never calls them |
| [#367](https://github.com/hsilviu05/SnapWorth/pull/367), [#368](https://github.com/hsilviu05/SnapWorth/pull/368) | Scan prompt v2.2 (served while `SCAN_PROMPT_VERSION=v2.2`), and sizes or style numbers the photo doesn't show are dropped |
| [#357](https://github.com/hsilviu05/SnapWorth/pull/357), [#358](https://github.com/hsilviu05/SnapWorth/pull/358) | Listing drafts checked for claims the scan can't support (behind a flag), and the listing prompt told not to vouch for authenticity |
| [#369](https://github.com/hsilviu05/SnapWorth/pull/369) | Backend modules grouped into packages, no behaviour change |
| [#346](https://github.com/hsilviu05/SnapWorth/pull/346)–[#349](https://github.com/hsilviu05/SnapWorth/pull/349), [#363](https://github.com/hsilviu05/SnapWorth/pull/363)–[#366](https://github.com/hsilviu05/SnapWorth/pull/366) | Dependency bumps |

### Repository only

[#350](https://github.com/hsilviu05/SnapWorth/pull/350) (software factory),
[#351](https://github.com/hsilviu05/SnapWorth/pull/351) (load test),
[#355](https://github.com/hsilviu05/SnapWorth/pull/355),
[#356](https://github.com/hsilviu05/SnapWorth/pull/356),
[#360](https://github.com/hsilviu05/SnapWorth/pull/360) (evals),
[#371](https://github.com/hsilviu05/SnapWorth/pull/371) (faster iOS CI),
[#372](https://github.com/hsilviu05/SnapWorth/pull/372) (docs/audits).

### Not in this build

* Anything merged after the archive commit recorded below.

---

## What's New, all five locales

Paste each block into its locale's What's New field on the 1.6.0 version
page, one language per field, never several in one. The `es` block goes
into both Spanish (Spain) and Spanish (Mexico), as
`app_store_listing.es.md` explains. Feature names and phrasing come from `ios/Localization/App.json`
("Rack scan", "Legit check", "Scan this one", "Copy listing draft", "My
Finds", and the Legit check disclaimer), so the store and the app use the
same words. None of the four translations has had a native reader yet.

Counts are Unicode characters, newlines included. App Store Connect's limit
is 4000.

| Locale | Characters |
|---|---|
| `en-US` | 551 |
| `ro` | 663 |
| `es` | 666 |
| `de` | 645 |
| `zh-Hans` | 223 |

### `en-US`

```
New in Pro:
• Rack scan: one photo of a rail, shelf or bin, and SnapWorth points out up to three items worth a closer look. Tap "Scan this one" for a real estimate.
• Legit check: photograph the logo, the inside label, the stitching and the hardware, and SnapWorth looks for the usual signs of a replica. An observation about your photos, not a certification of authenticity.

Also new:
• "Copy listing draft" uses the condition you picked.
• Photos stay level, and My Finds fills iPhone Duo's inner display.
• Fixed a rare crash on the result screen.
```

### `ro`

```
Nou în Pro:
• Scanare raft: o fotografie a unui stand, raft sau coș, iar SnapWorth îți arată până la trei articole care merită o privire mai atentă. Atinge „Scanează-l pe acesta” pentru o estimare reală.
• Verificare originalitate: fotografiază logoul, eticheta interioară, cusăturile și accesoriile metalice, iar SnapWorth caută semnele obișnuite ale unei replici. O observație despre fotografiile tale, nu o certificare a autenticității.

Tot nou:
• „Copiază ciorna anunțului” folosește starea pe care ai ales-o.
• Fotografiile rămân drepte, iar Găselnițe umple ecranul interior al iPhone Duo.
• Am remediat o închidere neașteptată rară pe ecranul rezultatului.
```

### `es`

```
Novedades en Pro:
• Escaneo de estante: una foto de un perchero, estante o cesta, y SnapWorth te señala hasta tres artículos que merecen una mirada. Toca «Escanear este» para una estimación real.
• Comprobación de originalidad: fotografía el logo, la etiqueta interior, las costuras y los herrajes, y SnapWorth busca las señales habituales de una réplica. Una observación sobre tus fotos, no una certificación de autenticidad.

También:
• «Copiar el borrador del anuncio» usa el estado que elegiste.
• Las fotos salen rectas y Mis hallazgos ocupa toda la pantalla interior del iPhone Duo.
• Corregido un cierre inesperado poco frecuente en la pantalla de resultados.
```

### `de`

```
Neu in Pro:
• Regal-Scan: ein Foto einer Stange, eines Regals oder einer Kiste, und SnapWorth zeigt dir bis zu drei Artikel, die einen genaueren Blick wert sind. Tippe auf „Diesen scannen“ für eine echte Schätzung.
• Echtheits-Check: Fotografiere Logo, Innenetikett, Nähte und Beschläge, und SnapWorth sucht nach den üblichen Anzeichen einer Replik. Eine Beobachtung zu deinen Fotos, keine Echtheitszertifizierung.

Außerdem:
• „Inserat-Entwurf kopieren“ übernimmt den Zustand, den du gewählt hast.
• Fotos bleiben gerade, und Meine Funde nutzt das innere Display des iPhone Duo ganz.
• Einen seltenen Absturz auf dem Ergebnisbildschirm behoben.
```

### `zh-Hans`

```
Pro 新功能：
• 货架扫描：拍一张衣架、货架或货箱的照片，SnapWorth 会指出最多三件值得细看的物品。点按“扫描这件”即可获得完整估价。
• 真伪检查：拍下标志、内侧标签、缝线和五金件，SnapWorth 会查找仿品的常见迹象。这是对你的照片的观察，并非真伪认证。

其他更新：
• “复制商品描述草稿”会使用你选择的成色。
• 照片保持水平，“我的好物”可铺满 iPhone Duo 的内屏。
• 修复了结果页面上一个罕见的闪退问题。
```

### What these leave out

The listing-claims check and prompt v2.2 (server-side, and not features a
user turns on), the privacy-policy wording, the folder move, and every
server, website and repository change. The crash line says "rare" and
nothing more: Organizer shows one device.

### TestFlight — What to Test

```
Please test on this build:
• Rack scan (Pro): tap Rack beside Haul, photograph a rail or shelf. Check the numbered pins match the cards, tapping a pin scrolls to its card, and "Scan this one" returns to the camera with the banner.
• Legit check (Pro): open a result for a brand like Nike or Coach, start a legit check, take two to four close-ups, then Check now. The read always shows "not a certification of authenticity".
• Upgrade: install over 1.5.3 with finds saved. Every find still opens.
```

---

## Testing on a device — required before submitting

All of these are owner checks, on the TestFlight build against production.
Tick each one, or open an issue for the failure that names the build its
fix needs.

### Upgrade from 1.5.3 (the model gained `legitCheckData`)

- [ ] On a phone with 1.5.3 and saved finds (sold flips among them),
      install 24 over it. The app opens, My Finds shows every find, and each
      one opens with its photo, prices and currency intact.
- [ ] My Flips' totals and the Profit widget (removed and re-added first,
      CLAUDE.md) read the same as before the upgrade.

### The crash fix ([#373](https://github.com/hsilviu05/SnapWorth/pull/373))

The deleting path behind the 1.5.3 crash is not known (the log has no
caller), so these try the ways a find can go while a sheet holds it.

- [ ] My Finds: open a find, close it, delete it from the long-press menu.
      Repeat quickly a few times. No crash.
- [ ] Haul: value two photos, open the summary, delete one find with
      "Delete this find?", then open My Finds. No crash, and the find is gone.
- [ ] Settings → Clear history with finds saved, then open My Finds and My
      Flips. No crash.
- [ ] Organizer → Crashes, a week after release: nothing new on 1.6.0 in
      `ResultView` or `ScanResult.imageData`.

### Rack scan ([#345](https://github.com/hsilviu05/SnapWorth/pull/345))

- [ ] Free: the Rack pill shows PRO and opens the paywall headed "Find the
      best on the rack".
- [ ] Pro: a photo of a real rail returns up to three picks. Pins and cards
      agree; tapping a pin scrolls to its card; the range says it is rough.
- [ ] "Scan this one" closes the rack and shows the banner on the camera;
      the next scan clears it.
- [ ] A photo with nothing worth flagging says "Nothing stood out in this
      photo." as an answer, not an error.
- [ ] Library pick works, and VoiceOver reads each pin and card.
- [ ] Nothing from the rack read appears in My Finds.

### Legit check ([#343](https://github.com/hsilviu05/SnapWorth/pull/343))

- [ ] Free: the card on a Nike result shows the Pro badge and opens the
      paywall headed "Check it before you buy".
- [ ] Pro: the camera walks logo, inside label, stitching and hardware, each
      with its own guide. Skip works; after two photos "Check now" sends.
- [ ] The read shows a verdict, reasons and any unclear photo, and always
      the "not a certification of authenticity" line. No headline says
      authentic, genuine or fake.
- [ ] Close and reopen the find: the read is still there, and "Check again"
      runs a new check. The share card does not show it.
- [ ] Many checks in an hour end with "You've run a lot of legit checks
      this hour", not the scan-limit message.

### Camera and iPhone Duo ([#362](https://github.com/hsilviu05/SnapWorth/pull/362))

- [ ] On an ordinary iPhone, scans and Haul photos are upright, as in 23.
- [ ] On iPhone Duo's inner display, if one is at hand: the preview and the
      saved photo are level, and My Finds shows more than two columns.

### Listing draft ([#367](https://github.com/hsilviu05/SnapWorth/pull/367))

- [ ] Change a result's condition from Used to New, then "Copy listing
      draft": the pasted text says New, not the scan's notes.

### Privacy policy

- [ ] Settings → Privacy Policy reads "Last updated: October 19, 2026",
      mentions photos sent for a legit check and lists TypeSafe (Jev), the
      same as `https://api.snapworth.eu/privacy`.

---

## Pre-submit checklist (owner)

- [ ] **Create the 1.6.0 version** in App Store Connect. Metadata belongs to
      a version, so the subtitle, keywords, description, screenshots and
      What's New are all edited there, and only while it is editable.
- [ ] **Add the store languages** if the version page shows only English:
      the language menu at the top right of the 1.6.0 page → Romanian,
      Spanish (Spain), Spanish (Mexico), German, Chinese (Simplified). The
      binary has spoken all five since 1.4.2, so a localized page is not
      ahead of the app. Paste name, subtitle, keywords and description from
      `app_store_listing.<locale>.md`, and What's New from below. This
      cannot be done once 1.6.0 is in review.
- [ ] **Description, all five locales**: re-paste in full. Each gained a
      Rack scan and a Legit check line in its Pro list, in the paywall's
      own wording, the Legit check one with its disclaimer.
- [ ] **Store page changes** from the analytics review: new subtitle and
      first screenshot, plus a screenshot each for Rack scan and Legit
      check. Screenshots must show the app as it ships (Guideline 2.3.3).
- [ ] **App icon**, if it changes: the icon is in the binary
      (`Assets.xcassets/AppIcon`), so it has to be swapped **before** the
      archive. The store page takes the icon from the build.
- [ ] **App Privacy label.** Legit check sends photos, which the label and
      `PrivacyInfo.xcprivacy` already declare (*Photos or Videos*, App
      Functionality, not linked, no tracking). Confirm the label in App
      Store Connect still matches; nothing new to add.
- [ ] `SANDBOX_ENTITLEMENTS` is `bounded` while 24 is with App Review
      (RUNBOOK §17), and there is no public TestFlight link.
- [ ] **Archive** with Xcode 27 from a fresh pull of `main`, after #373 has
      merged. First confirm the bump is in the checkout:
      `git merge-base --is-ancestor <#373's merge commit> HEAD && echo ok`.
      An archive from a stale checkout silently reuses 23, and App Store
      Connect rejects it.
- [ ] Record the archive below, then tag it.
- [ ] Upload 24 **once**. A rejection that needs a new archive bumps to 25
      first.
- [ ] What's New pasted in all five locales, from the blocks above.
- [ ] App Review notes pasted (below).
- [ ] Phased release on.

### Build 24 — the archive

The `1.6.0, build 24` change in #373 is a lower bound for what 24 contains,
not the build (CLAUDE.md). This record is the build.

- **Archive commit** (`git rev-parse HEAD` in the checkout, at archive
  time): `2d33f7ab9824b4596606e6335d5f38504e276963`, the merge of #373. The
  working tree also held Xcode 27's project upgrade, uncommitted (#374:
  object order and one analyzer setting, nothing that reaches the binary).
- **Organizer creation date:** 2026-10-21 18:06:58 (+03:00). Uploaded the
  same evening; 1.6.0 was Waiting for Review at 18:47.
- **Tag** it:

  ```sh
  git tag build-24 <archive-commit> && git push origin build-24
  ```

### App Review notes (paste into *App Review Information → Notes*)

```
No account is needed. SnapWorth works from the first launch without signing in.

In-app purchases made in the review Sandbox unlock SnapWorth Pro immediately. To see the Pro features, buy from the paywall, for example from "Unlock why this price" on a scan result.

New in this version, both in Pro:

Rack scan: on the Scan tab, tap "Rack" beside "Haul" and photograph a clothing rail or shelf. The app points out up to three items worth a closer look, with a rough range for each. Nothing from a rack photo is saved.

Legit check: on a scan result for a commonly counterfeited brand (for example Nike or Coach), tap "Legit check" and photograph up to four close-ups (logo, inside label, stitching, hardware). The app describes whether the photos show the usual signs of a replica. Every result is labelled "An observation about these photos, not a certification of authenticity", and no result states that an item is authentic or fake. The photos are sent to our server only to produce the read and are not stored there; the privacy policy (Settings → Privacy Policy) describes this.

The iMessage stickers are in the sticker drawer. In Messages, open the emoji keyboard and go to Stickers to find Tag's pack.

Easter egg: scanning one particular printed shirt (photo attached) plays a short, clearly labelled joke appraisal ("EASTER EGG · Just for fun — not a real valuation") before the real estimate, which is shown below it unchanged. Nothing from the joke is saved, priced or sold.
```

Keep the reference photo `ios/SnapWorthTests/rare-find-reference-shirt.png`
attached under *App Review Information → Attachment*, as for 23.

---

## After approval (owner)

- [ ] Field report: `rack_scan_completed`, `legit_check_completed` and the
      two new paywall triggers (`rack_scan`, `legit_check`) in the growth
      dashboard. Zero for a week is a result; record it.
- [ ] `/minbuild 24` only if a 23 bug requires it, and only once 24 is live.
