# What's New — SnapWorth 1.8.3

## Scope

**Submit 1.8.3 (36).** It replaces two uploads that were never submitted:
1.8.2 (34), and 1.8.3 (35), which was archived before the yearly trial badge
(#476) was added to this release. 36 carries everything both did (34's
Privacy Policy paragraph from #458/#460 included), so leave 34 and 35 unused.
The new App Store screenshots that 1.8.2 existed for go on the 1.8.3 version
page instead.

`chore` caveat as always: this bump is a lower bound for 36, not the build.
Record the archive commit and Organizer's date below.

### Ships in the binary (users get it only with 36)

| PR | What | Visible? |
|----|------|----------|
| #458, #460 | In-app Privacy Policy: RevenueCat paragraph (was going to ship in 34) | Yes, in Settings |
| #472 | First result: Pro is offered on the result's own card, not a full-screen paywall; no rating prompt over it | Yes, first result only, free users |
| #473 | "Check sold prices on eBay" under the estimate | Yes |
| #473 | Pro: one price-drop nudge when an item has been listed 30 days without selling (Settings → Notifications → Price-drop nudges) | Yes, Pro |
| #473 | "What should SnapWorth add next?" one-time vote in My Finds, from the third find | Yes, once |
| #473 | Notifications: after "Not now", reminders are delivered quietly; one trial tip the evening before the trial-ending reminder; opt-in measured | Partly |
| #474 | Main button in the brand font; Settings plan card aligned; My Finds shows "Start scanning" first for a new user; a banner for quiet notifications | Yes |
| #476 | Paywall: the yearly card's badge names the free trial ("3-DAY FREE TRIAL", in the length App Store Connect sets) for accounts that can take it, instead of "SAVE 33%" | Yes, eligible accounts |
| #474, #476 | 1.8.3, build 35, then 36 | No |

Backend changes in #473 (`POST /events/vote`, `/votes` in the ops bot, the
privacy-policy paragraph on votes) deploy from `main` and are live before the
binary, which is fine: older builds never call the endpoint.

### TestFlight — What to Test

```
Please test on this build:
• Scan an item: "Check sold prices on eBay" opens eBay's sold listings.
• My Finds with no finds: "Start scanning" is visible without scrolling.
• Settings → Notifications: toggles work; with quiet delivery a banner offers to turn them on.
• Pro: Settings → Notifications shows "Price-drop nudges".
• Paywall, with an account that has never had a trial: the Yearly card's badge names the free trial.
• Scans, purchases, restore and Redeem a code work as before.
```

### Device checks (once, before submitting)

- [ ] Paywall and onboarding: the main button's label is DM Sans semibold, not the system font.
- [ ] Settings: the Free Plan card's left edge lines up with the sections under it.
- [ ] Fresh install, tap "Not now" on the priming alert, scan once more the next day: Settings → Notifications shows "Reminders arrive quietly". Tapping it shows the iOS alert (or, if iOS shows none, opens Settings).
- [ ] The eBay link sits clear of the estimate card above it.
- [ ] Paywall with a fresh sandbox Apple Account (iOS Settings → Developer →
      Sandbox Apple Account): the Yearly badge reads the trial, the detail
      line reads only the weekly price, and the badge fits beside the price.
      With your own account (no trial left) it still reads "SAVE 33%".

---

## What's New, every store language except Chinese

Same locale mapping as 1.8.1: `en-US` also goes into English (U.K.), `es`
into Spain and Mexico, `fr` into France and Canada, `pt-BR` only into
Portuguese (Brazil). The feature names match the in-app strings.

**No Chinese (Simplified) store listing for 1.8.3** (owner's decision,
2026-11-17). The app itself keeps its Simplified Chinese strings for people
whose iPhone is set to Chinese. If the version page still lists Chinese
(Simplified), App Store Connect will ask for its What's New: remove that
language from the listing first, from the language menu on the App Store tab. The paste sheet is
`launch/APP-STORE-PASTE-1.8.3.md` in the project files.

### `en-US`
```
• Check sold prices on eBay, right under your estimate.
• Pro: a nudge when something has been listed 30 days without selling.
• Tell us what SnapWorth should add next, in My Finds.
• Clearer plans: when a free trial is available, the Yearly plan says so.
• Fixes and polish.
```

### `ro`
```
• Vezi prețurile de vânzare pe eBay, chiar sub estimare.
• Pro: un memento când ceva e listat de 30 de zile și nu s-a vândut.
• Spune-ne ce să adauge SnapWorth în continuare, în Găselnițe.
• Planuri mai clare: când ai o probă gratuită disponibilă, planul Anual o arată.
• Remedieri și îmbunătățiri.
```

### `es`
```
• Ver precios de venta en eBay, justo debajo de tu estimación.
• Pro: un aviso cuando algo lleva 30 días publicado sin venderse.
• Dinos qué debería añadir SnapWorth, en Mis hallazgos.
• Planes más claros: si tienes una prueba gratis disponible, el plan Anual lo indica.
• Correcciones y mejoras.
```

### `de`
```
• Verkaufspreise auf eBay prüfen, direkt unter deiner Schätzung.
• Pro: ein Hinweis, wenn etwas seit 30 Tagen unverkauft eingestellt ist.
• Sag uns in Meine Funde, was SnapWorth als Nächstes bekommen soll.
• Klarere Abos: Ist ein Gratis-Test für dich verfügbar, zeigt ihn das Abo „Jährlich“ an.
• Fehlerbehebungen und Verbesserungen.
```

### `fr`
```
• Voir les prix de vente sur eBay, juste sous ton estimation.
• Pro : un rappel quand un article est en vente depuis 30 jours sans trouver preneur.
• Dis-nous ce que SnapWorth devrait ajouter, dans Mes trouvailles.
• Des offres plus claires : quand un essai gratuit t'est proposé, l'offre Annuel l'indique.
• Corrections et améliorations.
```

### `it`
```
• Vedi i prezzi di vendita su eBay, subito sotto la stima.
• Pro: un promemoria quando un articolo è in vendita da 30 giorni senza essere venduto.
• Dicci cosa dovrebbe aggiungere SnapWorth, in Le mie scoperte.
• Piani più chiari: se hai una prova gratuita disponibile, il piano Annuale lo mostra.
• Correzioni e miglioramenti.
```

### `pt-BR`
```
• Veja preços de venda no eBay, logo abaixo da sua estimativa.
• Pro: um lembrete quando algo está anunciado há 30 dias sem vender.
• Diga o que o SnapWorth deve adicionar, em Meus achados.
• Planos mais claros: quando você tem um teste grátis disponível, o plano Anual mostra.
• Correções e melhorias.
```

### `pl`
```
• Sprawdź ceny sprzedaży na eBay, tuż pod wyceną.
• Pro: przypomnienie, gdy coś jest wystawione od 30 dni i się nie sprzedało.
• Powiedz nam w Moich znaleziskach, co SnapWorth ma dodać dalej.
• Czytelniejsze plany: gdy masz dostępny darmowy okres próbny, plan Rocznie to pokazuje.
• Poprawki i ulepszenia.
```

### `ja`
```
• 査定額のすぐ下から、eBayで落札価格を確認できます。
• Pro：出品から30日たっても売れていないときにお知らせします。
• SnapWorthに次に追加してほしい機能を「マイお宝」で教えてください。
• プランをわかりやすく：無料トライアルを利用できる場合は「年額」プランに表示されます。
• 不具合の修正と改善。
```

### `nl`
```
• Bekijk verkoopprijzen op eBay, direct onder je schatting.
• Pro: een herinnering als iets 30 dagen te koop staat zonder te verkopen.
• Laat in Mijn vondsten weten wat SnapWorth hierna moet toevoegen.
• Duidelijkere abonnementen: als je een gratis proefperiode kunt starten, staat die bij Jaarlijks.
• Oplossingen en verbeteringen.
```

Promotional text is unchanged; it is not tied to a build.

---

## Release checklist (owner)

Nothing is submitted until the last step, and that one is yours.

### 1. Before archiving
- [ ] `git pull` on `main` and confirm the bump is in the checkout:
      `grep -m1 "CURRENT_PROJECT_VERSION = 36" ios/SnapWorth.xcodeproj/project.pbxproj`.
- [ ] CI green on that `main` commit.

### 2. Archive
- [ ] Xcode 27 (`.xcode-version`), scheme **SnapWorth**, Release. Organizer
      shows **1.8.3 (36)**.
- [ ] Distribute → App Store Connect → Upload, once. A re-archive needs 37.
- [ ] Record the commit and Organizer's creation date under *Build 36*.

### 3. TestFlight
- [ ] 36 has no *Missing Compliance* badge.
- [ ] Paste *What to Test*, then run the device checks above once.

### 4. Version page
- [ ] Create version `1.8.3` (or rename the unsubmitted `1.8.2` version to
      1.8.3 if it was created), select build **36**.
- [ ] Screenshots: the new set from 1.8.2's work, at each display size.
- [ ] What's New, every store language (above); no Chinese (Simplified).
- [ ] App Privacy: confirm **Usage Data → Product Interaction** is declared
      (the feature vote falls under it). Nothing else changes.

### 5. Release
- [ ] Phased Release for Automatic Updates: on.
- [ ] Submit for review (owner only).

### Build 36 — the archive

- **Archive commit:** _to fill in_
- **Organizer creation date:** _to fill in_

### Build 35 — superseded, not to be submitted

- **Archive commit:** `745f9c2` (#474), the `main` HEAD when it was archived.
- **Organizer creation date:** 2026-11-17 21:18:00 EET (`SnapWorth-1.8.3-35.xcarchive`),
  Xcode 27.0 (27A266a). The app and its Share, Stickers and Widgets extensions
  all read 1.8.3 (35).
- Uploaded to App Store Connect once, at about 21:18 EET, with
  `xcodebuild -exportArchive` (`app-store-connect`, upload, version and build
  numbers left as built); not submitted for review. Superseded by 36, which
  adds the yearly trial badge (#476).
