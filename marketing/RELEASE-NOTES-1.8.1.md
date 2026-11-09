# What's New — SnapWorth 1.8.1

## Scope

1.8.1 (33) adds six app languages and RevenueCat as a purchase observer. It is
built on 1.8.0 (32), submitted for review on 2026-11-06 with manual release.
If 1.8.0 is not live when 1.8.1 is submitted, What's New also has to carry
1.8.0's items (`RELEASE-NOTES-1.8.0.md`).

`chore` caveat as always: this bump is a lower bound for 33, not the build.
Record the archive commit and Organizer's date below.

### Ships in the binary (users get it only with 33)

| PR | What | Visible? |
|----|------|----------|
| this PR | **French, Italian, Portuguese (Brazil), Polish, Japanese, Dutch**: all 1,111 strings in `App.json`, `Widgets.json`, `Share.json` and `InfoPlist.json`. The app now speaks 11 languages | **Yes** |
| this PR | Ask Tag answers in the six new languages. `pt-PT` phones get Brazilian Portuguese; other Portuguese and every unlisted language get English, as before | Yes |
| #456 | **RevenueCat** observes purchases for subscription analytics. StoreKit still makes, verifies and finishes every purchase; the analytics opt-out turns RevenueCat off too | No |
| #456 | Privacy manifest: Analytics purpose on Device ID and Purchase History. Both policy copies name RevenueCat | Yes, in Settings |
| this PR | 1.8.1, build 33 | No |

**Backend first.** `backend/scan/assist.py` names the six languages for
the prompt. An older backend does not know `fr`, `it`, `pt-BR`, `pl`, `ja`
or `nl` and answers in English. Deploy `main` to Railway before 33 reaches
testers. The RevenueCat policy text from #456 deploys the same way.

**Native readers.** None of the six translations has had a native reader.
Japanese is the riskiest, because its word order needed positional format
specifiers in eight strings. Polish is next, because of its four plural forms.
If you can get one native reader before release, pick Japanese.

### TestFlight — What to Test

```
Please test on this build:
• Set the iPhone language to French, Italian, Portuguese (Brazil), Polish, Japanese or Dutch: the app, widgets and share sheet all follow it.
• Look for text that is cut off or overflows, especially on the paywall and in widgets.
• Pro: ask Tag a question in that language. Tag answers in it.
• Purchases, restore and Redeem a code work as before.
```

---

## What's New, all eleven locales

Which store locale gets which file:

- `en-US` also goes into English (U.K.).
- `es` goes into Spanish (Spain) and Spanish (Mexico).
- `fr` goes into French (France) and French (Canada).
- `pt-BR` goes only into Portuguese (Brazil). Do not add Portuguese (Portugal).

The new lines name the languages in the reader's own language. RevenueCat
gets only the policy line, because nothing a user sees changes.

### `en-US` (also English (U.K.))

```
• SnapWorth now speaks French, Italian, Portuguese (Brazil), Polish, Japanese and Dutch. Ask Tag answers in them too.
• Updated Privacy Policy.
• Fixes and polish.
```

### `ro`

```
• SnapWorth vorbește acum franceză, italiană, portugheză (Brazilia), poloneză, japoneză și neerlandeză. Și Ask Tag răspunde în ele.
• Politica de confidențialitate, actualizată.
• Remedieri și îmbunătățiri.
```

### `es` (Spain and Mexico)

```
• SnapWorth ya habla francés, italiano, portugués (Brasil), polaco, japonés y neerlandés. Ask Tag también responde en ellos.
• Política de privacidad actualizada.
• Correcciones y mejoras.
```

### `de`

```
• SnapWorth spricht jetzt Französisch, Italienisch, Portugiesisch (Brasilien), Polnisch, Japanisch und Niederländisch. Auch Ask Tag antwortet darin.
• Datenschutzerklärung aktualisiert.
• Fehlerbehebungen und Verbesserungen.
```

### `zh-Hans`

```
• SnapWorth 现已支持法语、意大利语、葡萄牙语（巴西）、波兰语、日语和荷兰语，Ask Tag 也会用这些语言回答。
• 隐私政策已更新。
• 问题修复与改进。
```

### `fr` (France and Canada)

```
• SnapWorth est maintenant en français ! L’app parle aussi italien, portugais (Brésil), polonais, japonais et néerlandais, et Ask Tag vous répond dans votre langue.
• Politique de confidentialité mise à jour.
• Corrections et améliorations.
```

### `it`

```
• SnapWorth ora parla italiano! E anche francese, portoghese (Brasile), polacco, giapponese e olandese. Ask Tag ti risponde nella tua lingua.
• Informativa sulla privacy aggiornata.
• Correzioni e miglioramenti.
```

### `pt-BR`

```
• O SnapWorth agora fala português! E também francês, italiano, polonês, japonês e holandês. O Ask Tag responde no seu idioma.
• Política de Privacidade atualizada.
• Correções e melhorias.
```

### `pl`

```
• SnapWorth jest teraz po polsku! Aplikacja mówi też po francusku, włosku, portugalsku (Brazylia), japońsku i niderlandzku, a Ask Tag odpowiada w Twoim języku.
• Zaktualizowana polityka prywatności.
• Poprawki i ulepszenia.
```

### `ja`

```
• SnapWorth が日本語に対応しました。フランス語、イタリア語、ポルトガル語（ブラジル）、ポーランド語、オランダ語にも対応し、Ask Tag もお使いの言語で回答します。
• プライバシーポリシーを更新しました。
• 不具合の修正と改善。
```

### `nl`

```
• SnapWorth spreekt nu Nederlands! En ook Frans, Italiaans, Portugees (Brazilië), Pools en Japans. Ask Tag antwoordt in jouw taal.
• Privacybeleid bijgewerkt.
• Oplossingen en verbeteringen.
```

## Promotional text

The five existing locales keep 1.8.0's promotional text. The six new ones
take the *Primary* promotional text from their `app_store_listing.<locale>.md`.

---

## The six new store languages

A localized listing ships with the localized binary, so these go on the
1.8.1 version page and not before.

For each of French (France), French (Canada), Italian, Portuguese (Brazil),
Polish, Japanese and Dutch:

1. Add it on the 1.8.1 version page.
2. Paste the name and subtitle under App Information.
3. Paste the description, keywords, promotional text and What's New.

Use `app_store_listing.fr.md` for both French locales.
`python3 tools/check_listing_limits.py` passes for all of them. Japanese gets
the same "two characters or fewer" warning as `zh-Hans`, which is fine,
because two-character words are normal in Japanese.

Screenshots: a locale without its own falls back to the primary language's
(English), which is acceptable for this release.

---

## Testing on a device — required before submitting

- [ ] Phone language set to each of fr, it, pt-BR, pl, ja and nl. Check
      onboarding, the consent screen, a scan result, the paywall, Settings,
      the widgets and the share extension. No English is left (money,
      listing copy and the legal documents stay English, which is intended).
- [ ] Plurals: My Finds with 1, 2, 5 and 22 finds in Polish, which shows
      "1 znalezisko", "2 znaleziska", "5 znalezisk", "22 znaleziska". With 1
      and 2 in French and Dutch.
- [ ] Japanese: the scans-left and price lines read naturally, not word for word.
- [ ] French: there is no line break before `?`, `!`, `:` or `»`.
- [ ] Paywall and small widgets in German, Polish and French (the longest
      languages): nothing is cut off.
- [ ] Ask Tag in French and Japanese (Pro, Sandbox), against the deployed
      backend: the answer comes back in that language.
- [ ] Phone in Portuguese (Portugal): the app shows Brazilian Portuguese.
- [ ] Phone in Korean: English, as before.
- [ ] RevenueCat (#456): a Sandbox purchase and a restore complete exactly as
      on 32. With analytics turned off in Settings, nothing reaches RevenueCat.
- [ ] Remove and re-add each widget before judging its language, because iOS
      caches widget snapshots across an update.

---

## Release checklist (owner)

Nothing is submitted until the last step, and that one is yours.

### 1. Before archiving
- [ ] `git pull` on `main` and confirm the bump is in the checkout:
      `grep -m1 "CURRENT_PROJECT_VERSION = 33" ios/SnapWorth.xcodeproj/project.pbxproj`.
- [ ] CI green on that `main` commit (iOS, backend, secrets).
- [ ] Railway deploy of `main` finished (Ask Tag's new languages, RevenueCat policy text).

### 2. Archive
- [ ] Xcode 27 (`.xcode-version`), scheme **SnapWorth**, Release. Organizer
      shows **1.8.1 (33)**.
- [x] Distribute → App Store Connect → Upload, once. A re-archive needs 34.
- [x] Record the commit and Organizer's creation date under *Build 33*.

### 3. TestFlight
- [ ] 33 has no *Missing Compliance* badge.
- [ ] Paste *What to Test*, then run the device checks above.

### 4. Version page
- [ ] Create version `1.8.1`, select build **33**.
- [ ] What's New, all locales (above). Add the seven new store locales from
      their listing files.
- [ ] **App Privacy (RevenueCat):** add **Analytics** as a purpose under
      *Identifiers → Device ID* and *Purchases → Purchase History*. Not
      linked to identity, no tracking. Keep the existing App Functionality
      purposes.
- [ ] `PrivacyInfo.xcprivacy` matches those answers (#456 already added them).
- [ ] App Review notes: RevenueCat is used for analytics only, and all
      purchases go through StoreKit. Give the Sandbox tester account.

### 5. Release
- [ ] Phased Release for Automatic Updates: on.
- [ ] Submit for review (owner only).

### Build 33 — the archive

- **Archive commit:** `f2a5da7` (#457), the `main` HEAD when it was archived;
  it carries RevenueCat (#456)
- **Organizer creation date:** 2026-11-09 15:03:41 EET (`SnapWorth-1.8.1-33.xcarchive`)
- Uploaded to App Store Connect once, at 15:05 EET; not submitted for review.
  A re-archive needs 34.
