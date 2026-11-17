# What's New — SnapWorth 1.8.3

## Scope

1.8.3 (35) replaces 1.8.2 (34), which was uploaded but never submitted. It
carries everything 34 did (the Privacy Policy paragraph from #458/#460), so
**submit 1.8.3 and leave 34 unused**. The new App Store screenshots that 1.8.2
existed for go on the 1.8.3 version page instead.

`chore` caveat as always: this bump is a lower bound for 35, not the build.
Record the archive commit and Organizer's date below.

### Ships in the binary (users get it only with 35)

| PR | What | Visible? |
|----|------|----------|
| #458, #460 | In-app Privacy Policy: RevenueCat paragraph (was going to ship in 34) | Yes, in Settings |
| #472 | First result: Pro is offered on the result's own card, not a full-screen paywall; no rating prompt over it | Yes, first result only, free users |
| #473 | "Check sold prices on eBay" under the estimate | Yes |
| #473 | Pro: one price-drop nudge when an item has been listed 30 days without selling (Settings → Notifications → Price-drop nudges) | Yes, Pro |
| #473 | "What should SnapWorth add next?" one-time vote in My Finds, from the third find | Yes, once |
| #473 | Notifications: after "Not now", reminders are delivered quietly; one trial tip the evening before the trial-ending reminder; opt-in measured | Partly |
| this PR | Main button in the brand font; Settings plan card aligned; My Finds shows "Start scanning" first for a new user; a banner for quiet notifications | Yes |
| this PR | 1.8.3, build 35 | No |

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
• Scans, purchases, restore and Redeem a code work as before.
```

### Device checks (once, before submitting)

- [ ] Paywall and onboarding: the main button's label is DM Sans semibold, not the system font.
- [ ] Settings: the Free Plan card's left edge lines up with the sections under it.
- [ ] Fresh install, tap "Not now" on the priming alert, scan once more the next day: Settings → Notifications shows "Reminders arrive quietly". Tapping it shows the iOS alert (or, if iOS shows none, opens Settings).
- [ ] The eBay link sits clear of the estimate card above it.

---

## What's New, all eleven locales

Same locale mapping as 1.8.1: `en-US` also goes into English (U.K.), `es`
into Spain and Mexico, `fr` into France and Canada, `pt-BR` only into
Portuguese (Brazil). The feature names match the in-app strings.

### `en-US`
```
• Check sold prices on eBay, right under your estimate.
• Pro: a nudge when something has been listed 30 days without selling.
• Tell us what SnapWorth should add next, in My Finds.
• Fixes and polish.
```

### `ro`
```
• Vezi prețurile de vânzare pe eBay, chiar sub estimare.
• Pro: un memento când ceva e listat de 30 de zile și nu s-a vândut.
• Spune-ne ce să adauge SnapWorth în continuare, în Găselnițe.
• Remedieri și îmbunătățiri.
```

### `es`
```
• Ver precios de venta en eBay, justo debajo de tu estimación.
• Pro: un aviso cuando algo lleva 30 días publicado sin venderse.
• Dinos qué debería añadir SnapWorth, en Mis hallazgos.
• Correcciones y mejoras.
```

### `de`
```
• Verkaufspreise auf eBay prüfen, direkt unter deiner Schätzung.
• Pro: ein Hinweis, wenn etwas seit 30 Tagen unverkauft eingestellt ist.
• Sag uns in Meine Funde, was SnapWorth als Nächstes bekommen soll.
• Fehlerbehebungen und Verbesserungen.
```

### `zh-Hans`
```
• 估价下方即可在 eBay 查看成交价。
• Pro：商品上架 30 天仍未售出时，提醒你一次。
• 在“我的好物”中告诉我们 SnapWorth 接下来该加什么。
• 问题修复与改进。
```

### `fr`
```
• Voir les prix de vente sur eBay, juste sous ton estimation.
• Pro : un rappel quand un article est en vente depuis 30 jours sans trouver preneur.
• Dis-nous ce que SnapWorth devrait ajouter, dans Mes trouvailles.
• Corrections et améliorations.
```

### `it`
```
• Vedi i prezzi di vendita su eBay, subito sotto la stima.
• Pro: un promemoria quando un articolo è in vendita da 30 giorni senza essere venduto.
• Dicci cosa dovrebbe aggiungere SnapWorth, in Le mie scoperte.
• Correzioni e miglioramenti.
```

### `pt-BR`
```
• Veja preços de venda no eBay, logo abaixo da sua estimativa.
• Pro: um lembrete quando algo está anunciado há 30 dias sem vender.
• Diga o que o SnapWorth deve adicionar, em Meus achados.
• Correções e melhorias.
```

### `pl`
```
• Sprawdź ceny sprzedaży na eBay, tuż pod wyceną.
• Pro: przypomnienie, gdy coś jest wystawione od 30 dni i się nie sprzedało.
• Powiedz nam w Moich znaleziskach, co SnapWorth ma dodać dalej.
• Poprawki i ulepszenia.
```

### `ja`
```
• 査定額のすぐ下から、eBayで落札価格を確認できます。
• Pro：出品から30日たっても売れていないときにお知らせします。
• SnapWorthに次に追加してほしい機能を「マイお宝」で教えてください。
• 不具合の修正と改善。
```

### `nl`
```
• Bekijk verkoopprijzen op eBay, direct onder je schatting.
• Pro: een herinnering als iets 30 dagen te koop staat zonder te verkopen.
• Laat in Mijn vondsten weten wat SnapWorth hierna moet toevoegen.
• Oplossingen en verbeteringen.
```

Promotional text is unchanged; it is not tied to a build.

---

## Release checklist (owner)

Nothing is submitted until the last step, and that one is yours.

### 1. Before archiving
- [ ] `git pull` on `main` and confirm the bump is in the checkout:
      `grep -m1 "CURRENT_PROJECT_VERSION = 35" ios/SnapWorth.xcodeproj/project.pbxproj`.
- [ ] CI green on that `main` commit.

### 2. Archive
- [ ] Xcode 27 (`.xcode-version`), scheme **SnapWorth**, Release. Organizer
      shows **1.8.3 (35)**.
- [ ] Distribute → App Store Connect → Upload, once. A re-archive needs 36.
- [ ] Record the commit and Organizer's creation date under *Build 35*.

### 3. TestFlight
- [ ] 35 has no *Missing Compliance* badge.
- [ ] Paste *What to Test*, then run the device checks above once.

### 4. Version page
- [ ] Create version `1.8.3` (or rename the unsubmitted `1.8.2` version to
      1.8.3 if it was created), select build **35**.
- [ ] Screenshots: the new set from 1.8.2's work, at each display size.
- [ ] What's New, all locales (above).
- [ ] App Privacy: confirm **Usage Data → Product Interaction** is declared
      (the feature vote falls under it). Nothing else changes.

### 5. Release
- [ ] Phased Release for Automatic Updates: on.
- [ ] Submit for review (owner only).

### Build 35 — the archive

- **Archive commit:** _to fill in_
- **Organizer creation date:** _to fill in_
