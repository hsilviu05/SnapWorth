# What's New — SnapWorth 1.8.0

## Scope

1.8.0 (31) adds **Ask Tag** for Pro and a one-time **AI consent** screen for
everyone (#450). It is built on top of 1.7.2 (30). If 1.7.2 is not live when
1.8.0 is submitted, What's New also has to carry 1.7.2's items
(`RELEASE-NOTES-1.7.2.md`).

`chore` caveat as always: this bump is a lower bound for 31, not the build.
Record the archive commit and Organizer's date below.

### Ships in the binary (users get it only with 31)

| PR | What | Visible? |
|----|------|----------|
| #450 | **Ask Tag** (Pro): "Ask Tag a question" on the Tag's tips card opens a chat about that find. 3 suggested questions, answers in the app's language, "AI answer, may be wrong" under each, "Report this answer" drafts a support email. Saved on the find, on the device only. 20 questions per device per day | **Yes** |
| #450 | **AI consent** ("Before your first scan"): once, between onboarding and the app, and once for existing users after updating. Names Google Gemini and lists what is sent. No way into scanning before agreeing | **Yes** |
| #450 | Paywall: Ask Tag benefit row; the `tag_tips` pitch leads with it | Yes |
| #450 | Privacy Policy dated November 5, 2026: names Ask Tag and the consent | Yes, in Settings |
| #451 | 1.8.0, build 31 (archived, superseded by 32) | No |

The backend half of #450 (`POST /assist`, `/privacy` dated November 5) deploys
to Railway from the merge on `main`. It must be live before testing Ask Tag:
an older backend answers 404, which the app shows as "Tag couldn't answer
right now".

26 new strings, all in `App.json` in five languages. "Ask Tag" is a name and
stays Ask Tag.

### Added after build 31 (needs build 32)

These merged after the 31 bump, so 31 does not have them. Build 32 carries
them, together with the consent line's fix below.

| What | Visible? |
|------|----------|
| **Year in Finds**: a My Finds banner in December and January (all year in TestFlight) opens a share card of the year: finds scanned, their summed estimate, top find, most-found brand, busiest month. Pro adds the year's flip profit; free users get a locked row that opens the paywall (trigger `wrapped`). QR campaign `share_wrapped` via `/get/wrapped` | **Yes**, from Dec 1 |
| **Redeem a code** in Settings → Account: Apple's offer-code sheet | Yes |
| **Since you went Pro** (My Flips, Pro): flip profit since the first Pro payment beside what Pro has cost, from StoreKit's own transaction history; "Pro has paid for itself" once profit covers the cost. Hidden with no paid period, or when Pro and the flips are in different currencies | Yes, Pro |
| **Daily guess widget** (small): today's round waiting or played, and the streak; opens the game | Yes |
| **"What brings you to SnapWorth?"** on the last onboarding page (optional: resell, clearing out, just curious). Reorders which benefits the intro paywall leads with; nothing is gated on it. Event `onboarding_goal_chosen` | Yes, new installs |
| **Ask Tag preview**: free users get one question a week (`ASSIST_FREE_WEEKLY_MAX`, default 1; 0 turns it off), then "Keep asking with Pro" | Yes |
| **Local currency line** under the estimate: "≈ a–b · converted from US prices" in the region currency, from the new `GET /rates` (ECB daily). Hidden for USD regions or when no rate is cached | Yes, non-US regions |

The `/get/wrapped` redirect deploys with the website from `main`; the
paywall trigger `wrapped` is counted once the backend from `main` is live
(an older one ignores it). The Ask Tag preview and the currency line need
the backend from `main` deployed **before** build 32 reaches testers: an older
backend answers a free user's question with "Pro required" and has no
`/rates`, so the line just stays hidden. 36 new strings (30 app, 6 widget), all
in five languages.

The consent screen's Ask Tag line drops "(Pro)" in all five languages:
with the weekly free question, a free user's question is sent too, and the
screen has to say so before the first one.

Experiment #220 (1-week trial arm) runs to about Dec 7. The onboarding
question and the Ask Tag preview both change what reaches the paywall, so
ship 32 to the App Store after it ends, or read #220 knowing they landed
mid-test. **Decided 2026-11-06: 1.8.0 ships complete as 32 now**, so #220's
results from 32's release date on are read with that in mind.

**Device checks for 32:**
- [ ] My Finds with 3+ finds this year (TestFlight): the Year in Finds banner shows; the card preview matches what is shared; the QR opens the App Store.
- [ ] Free with a priced sale this year: the locked row opens the paywall headed "Add your profit to your year"; after a Sandbox purchase the toggle replaces it.
- [ ] Settings → Redeem a code opens Apple's sheet (a Sandbox offer code redeems and Pro turns on).
- [ ] Pro (Sandbox) with a sold flip after the purchase: My Flips shows "Since you went Pro" with the profit and the Sandbox price; once the profit covers it, "Pro has paid for itself". Free: no card.
- [ ] Add the Daily guess widget: "Guess today's price"; play; the widget shows the streak and "Done for today"; tap opens the game over the Scan tab.
- [ ] Fresh install: the last onboarding page asks "What brings you to SnapWorth?"; pick "I resell" and the intro paywall leads with Haul / Rack rows; skipping it changes nothing.
- [ ] Free: a result's Tag card offers "Ask Tag one question free"; ask one; the composer is replaced by "Keep asking with Pro". Reopen the same week: a new result offers no free question.
- [ ] Region Romania (Settings → General → Language & Region): a result shows "≈ … lei · converted from US prices" under the range; region US shows no line; airplane mode on first launch shows no line and no error.
- [ ] App Review notes: Year in Finds only appears in December and January.

### TestFlight — What to Test

```
Please test on this build:
• First launch after updating: the "Before your first scan" screen appears once. Agree, then scan normally.
• Pro: open a result, tap "Ask Tag a question", try a suggested question and one of your own.
• Ask in another app language (e.g. Romanian): Tag answers in that language.
• Long-press an answer → "Report this answer" opens a pre-filled email.
• Free: the Tag's tips card says "Unlock Tag's tips" and the paywall shows the Ask Tag row.
• (32) My Finds: the "Year in Finds" banner opens your year's card. Share it.
• (32) Settings → Redeem a code opens Apple's code sheet.
• (32) Add the Daily guess widget and play today's round.
• (32) Free: ask Tag one question free from a result.
• (32) Outside the US: the estimate also shows in your currency.
• (32) Pro: after a sale, My Flips shows "Since you went Pro".
```

---

## What's New, all five locales

1.7.2 is live, so this covers 1.8.0 as shipped in build 32: Ask Tag (Pro, and
one free question a week), the consent screen, the local-currency line, the
Daily guess widget, Year in Finds, Since you went Pro and Redeem a code. The onboarding goal
question is left out: it only reorders the intro paywall for new installs.
`es` goes into Spanish (Spain) and Spanish (Mexico); `en-US` also into English
(U.K.). Counts are Unicode characters, newlines included. Feature and Settings
names follow `App.json` and `Widgets.json`; "Ask Tag" is the same in every
language. None of the four translations has had a native reader yet.

Year in Finds is listed although it only appears from December 1: 1.8.0 is
the version users will have then. Say so in the App Review notes (below).

| Locale | What's New | Promotional Text |
|---|---|---|
| `en` | 774 / 4000 | 162 / 170 |
| `ro` | 883 / 4000 | 164 / 170 |
| `es` | 878 / 4000 | 167 / 170 |
| `de` | 907 / 4000 | 167 / 170 |
| `zh-Hans` | 344 / 4000 | 62 / 170 |

### `en-US` (also English (U.K.))

```
• New: Ask Tag. Ask Tag about any find, like what to charge, where to sell it or how to list it. Tag answers in your language. Pro can keep asking; everyone else gets one free question a week.
• Your currency: outside the US, the estimate also shows converted into your local currency.
• Daily guess widget: today's round and your streak, right on the Home Screen.
• Year in Finds: in December and January, share a card of your year in finds. Pro can add their flip profit.
• Pro: My Flips now shows your flip profit since you went Pro, next to what Pro has cost.
• Redeem a code: now in Settings → Account.
• Before your first scan, a one-time screen shows what the app sends to Google Gemini for analysis and asks for your OK.
• Updated Privacy Policy.
• Fixes and polish.
```

### `ro`

```
• Nou: Ask Tag. Pune-i lui Tag întrebări despre orice găsire, de exemplu cât să ceri, unde s-o vinzi sau cum s-o pui în anunț. Tag îți răspunde în limba ta. Cu Pro poți întreba în continuare; fără Pro ai o întrebare gratis pe săptămână.
• Moneda ta: în afara SUA, estimarea apare și convertită în moneda locală.
• Widgetul Ghicitoarea zilei: runda de azi și seria ta, direct pe ecranul principal.
• Anul în găselnițe: în decembrie și ianuarie, distribuie un card cu anul tău în găselnițe. Cu Pro poți adăuga și profitul din flipuri.
• Pro: în Flipuri vezi profitul din flipuri de când ai Pro, lângă cât te-a costat Pro.
• Folosește un cod: acum în Setări → Cont.
• Înainte de prima scanare, un ecran afișat o singură dată îți arată ce trimite aplicația către Google Gemini pentru analiză și îți cere acordul.
• Politica de confidențialitate, actualizată.
• Remedieri și îmbunătățiri.
```

### `es` (Spain and Mexico)

```
• Novedad: Ask Tag. Pregúntale a Tag sobre cualquier hallazgo: cuánto pedir, dónde venderlo o cómo anunciarlo. Tag responde en tu idioma. Con Pro puedes seguir preguntando; sin Pro, tienes una pregunta gratis a la semana.
• Tu moneda: fuera de EE. UU., la estimación también aparece convertida a tu moneda local.
• Widget Adivinanza diaria: la ronda de hoy y tu racha, en la pantalla de inicio.
• Tu año en hallazgos: en diciembre y enero, comparte una tarjeta con tu año. Con Pro puedes añadir el beneficio de tus reventas.
• Pro: Mis reventas muestra tu beneficio desde que tienes Pro, junto a lo que te ha costado Pro.
• Canjear un código: ahora en Ajustes > Cuenta.
• Antes de tu primer escaneo, una pantalla que solo aparece una vez muestra qué envía la app a Google Gemini para el análisis y te pide permiso.
• Política de privacidad actualizada.
• Correcciones y mejoras.
```

### `de`

```
• Neu: Ask Tag. Stell Tag Fragen zu jedem Fund, etwa welchen Preis du verlangen, wo du ihn verkaufen oder wie du ihn inserieren solltest. Tag antwortet in deiner Sprache. Mit Pro fragst du weiter, ohne Pro hast du eine Gratisfrage pro Woche.
• Deine Währung: Außerhalb der USA zeigt die Schätzung den Betrag auch in deiner Landeswährung.
• Widget „Tagesrätsel“: die heutige Runde und deine Serie direkt auf dem Home-Bildschirm.
• Jahr in Funden: Im Dezember und Januar teilst du eine Karte mit deinem Jahr. Mit Pro kommt dein Flip-Gewinn dazu.
• Pro: Meine Flips zeigt deinen Gewinn, seit du Pro hast, neben dem, was Pro gekostet hat.
• Code einlösen: jetzt unter Einstellungen → Konto.
• Vor deinem ersten Scan zeigt dir ein einmaliger Bildschirm, was die App zur Analyse an Google Gemini sendet, und fragt nach deiner Zustimmung.
• Datenschutzerklärung aktualisiert.
• Fehlerbehebungen und Verbesserungen.
```

### `zh-Hans`

```
• 新功能：Ask Tag。就任何好物向 Tag 提问，比如该定什么价、在哪里卖、怎么发布。Tag 会用你的语言回答。Pro 用户可以继续提问，其他用户每周可免费问一个问题。
• 你的货币：在美国以外，估价还会换算成你的本地货币显示。
• “每日猜价”小组件：在主屏幕上查看今天的回合和连续天数。
• 淘货年度回顾：12 月和 1 月可分享你一年的淘货卡片，Pro 用户还能加上转卖利润。
• Pro：“我的转卖”会显示你开通 Pro 以来的转卖收益，以及 Pro 的累计花费。
• 兑换代码：现在位于“设置”→“账户”。
• 首次扫描前，会显示一次说明页面，告诉你应用会将哪些内容发送给 Google Gemini 进行分析，并征求你的同意。
• 隐私政策已更新。
• 问题修复与改进。
```

## Promotional text (paste with 1.8.0, not before)

It describes Ask Tag, which the live 1.7.2 does not have, so it goes on with
the 1.8.0 version page. Until then, keep 1.7.2's.

`en-US` (also English (U.K.)):

```
New: Ask Tag about any find, from what to charge to where to sell. One free question a week, more with Pro. Plus prices in your currency and a Daily guess widget.
```

`ro`:

```
Nou: întreabă-l pe Tag despre orice găsire, de la preț la unde s-o vinzi. O întrebare gratis pe săptămână, mai multe cu Pro. Plus prețuri în moneda ta și un widget.
```

`es` (Spain and Mexico):

```
Novedad: pregúntale a Tag por cualquier hallazgo, del precio a dónde venderlo. Una pregunta gratis a la semana, más con Pro. Y precios en tu moneda y un widget diario.
```

`de`:

```
Neu: Frag Tag zu jedem Fund, vom Preis bis zum besten Ort zum Verkaufen. Eine Gratisfrage pro Woche, mehr mit Pro. Dazu Preise in deiner Währung und ein Rätsel-Widget.
```

`zh-Hans`:

```
新功能：就任何好物向 Tag 提问，从定价到在哪卖。每周免费一问，Pro 可以问更多。另有本地货币价格和“每日猜价”小组件。
```

---

## Testing on a device — required before submitting

- [ ] Upgrade 30 → 31 with finds saved: the consent screen appears once,
      every find opens, nothing reaches the AI before "Agree and continue".
- [ ] Fresh install: onboarding → consent → app. The consent's privacy link
      opens the policy dated November 5, 2026.
- [ ] Share extension, widget and visual intelligence entry points on a fresh
      install before agreeing: the consent shows first.
- [ ] Ask Tag, Pro (Sandbox): suggested questions, a typed question, close
      and reopen the sheet (the conversation is still there, the button reads
      "Continue with Tag").
- [ ] An answer in Romanian with the app in Romanian.
- [ ] Ask a 21st question in one day: "You've reached Ask Tag's limit for now."
- [ ] Airplane mode: "Tag couldn't answer right now. Check your connection…"
- [ ] Report this answer: Mail opens with item, question and answer filled in.
- [ ] Delete a find while its Ask Tag sheet is open: no crash.
- [ ] Ask Tag on a covered (hidden-price) result is not offered (the card is
      not shown there).
- [ ] VoiceOver: each bubble reads as "You: …" / "Tag: …, AI answer, may be
      wrong", and Report is an action.
- [ ] Free: the Ask Tag button offers one free question (32; on 31 there was
      none); the paywall shows the Ask Tag row.

---

## Release checklist (owner)

Nothing is submitted until the last step, and that one is yours.

### 1. Before archiving
- [ ] `git pull` on `main` and confirm the bump is in the checkout:
      `grep -m1 "CURRENT_PROJECT_VERSION = 32" ios/SnapWorth.xcodeproj/project.pbxproj`.
- [ ] CI green on that `main` commit (iOS, backend, secrets).
- [ ] Railway deploy of `main` finished: `https://api.snapworth.eu/privacy`
      shows November 5, 2026, and `https://api.snapworth.eu/rates` answers
      (the currency line and the free Ask Tag question need it).

### 2. Archive
- [ ] Xcode 27 (`.xcode-version`), scheme **SnapWorth**, Release. Organizer
      shows **1.8.0 (32)**.
- [ ] Distribute → App Store Connect → Upload, once. A re-archive needs 33.
- [ ] Record the commit and Organizer's creation date under *Build 32*.

### 3. TestFlight
- [ ] 32 has no *Missing Compliance* badge.
- [ ] Paste *What to Test*; run the device checks above, both the 31 list
      and *Device checks for 32*.

### 4. Version page
- [ ] Create version `1.8.0`, select build **32**.
- [ ] What's New and Promotional Text, all locales (above, under *What's New*).
- [ ] **App Privacy:** Ask Tag questions are *User Content → Other User
      Content*, used for *App Functionality*, not linked to identity, no
      tracking. Add it if it is not already declared.
- [ ] `PrivacyInfo.xcprivacy`: matches the App Privacy answers.
- [ ] Age rating: re-answer only if the questionnaire asks about AI chat or
      user-to-user messaging (Ask Tag is neither user-to-user nor shared).
- [ ] App Review notes: Ask Tag gives a free user one question a week and
      Pro the rest; give the Sandbox tester account and say where the button
      is (open any result → Tag's tips → "Ask Tag a question"). Mention the
      consent screen (5.1.2(i)), that Year in Finds only appears in December
      and January, and where Redeem a code is (Settings → Account).
- [ ] The subscription retention message (owner, set up in App Store
      Connect) is attached to this submission.

### 5. Release
- [ ] Phased Release for Automatic Updates: on.
- [ ] Submit for review (owner only).

### Build 31 — archived, superseded by 32

- **Archive commit:** `4b66d42` (#451), the `main` HEAD when it was archived
- **Organizer creation date:** 2026-11-05 18:32:49 EET (`SnapWorth-1.8.0-31.xcarchive`)
- Never submitted. It lacks everything under *Added after build 31*, so the
  version page takes 32. If 31 was uploaded, it stays in TestFlight unused.

### Build 32 — the archive

- **Archive commit:** _not yet_ (archive from `main` after this PR merges)
- **Organizer creation date:** _not yet_
