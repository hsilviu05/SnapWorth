# What's New — SnapWorth 1.8.2

## Scope

1.8.2 (34) exists to ship the new App Store screenshots (iPhone medium
display, Duo and large display). Screenshots belong to a version and can only
be changed while that version is editable, and a new version needs a build.
App Store Connect lists only builds uploaded after the last released version,
so 33 cannot be attached to 1.8.2 once 1.8.1 is live. Hence 34: same app as 33
plus the one in-app change below.

Built on 1.8.1 (33), archived from `f2a5da7`. **If 1.8.1 is not live when
1.8.2 is submitted**, What's New also has to carry 1.8.1's language line
(`RELEASE-NOTES-1.8.1.md`).

`chore` caveat as always: this bump is a lower bound for 34, not the build.
Record the archive commit and Organizer's date below.

### Ships in the binary (users get it only with 34)

| PR | What | Visible? |
|----|------|----------|
| #458, #460 | In-app Privacy Policy: RevenueCat paragraph says the server sent it, once, existing subscriptions and a one-way hash of other installs' pseudonymous IDs | Yes, in Settings |
| this PR | 1.8.2, build 34 | No |

Backend-only since 33, already live from `main` and not part of the binary:
`/revenue rcimport` (#458–#460) and four Dependabot bumps (#461–#463, #465).

### TestFlight — What to Test

```
Please test on this build:
• Settings → Privacy Policy shows the updated RevenueCat paragraph.
• Scans, purchases, restore and Redeem a code work as before.
```

---

## What's New, all eleven locales

Same locale mapping as 1.8.1: `en-US` also goes into English (U.K.), `es`
into Spain and Mexico, `fr` into France and Canada, `pt-BR` only into
Portuguese (Brazil). The two lines are 1.8.1's, word for word, so every
translation has been through one release already.

### `en-US`
```
• Updated Privacy Policy.
• Fixes and polish.
```

### `ro`
```
• Politica de confidențialitate, actualizată.
• Remedieri și îmbunătățiri.
```

### `es`
```
• Política de privacidad actualizada.
• Correcciones y mejoras.
```

### `de`
```
• Datenschutzerklärung aktualisiert.
• Fehlerbehebungen und Verbesserungen.
```

### `zh-Hans`
```
• 隐私政策已更新。
• 问题修复与改进。
```

### `fr`
```
• Politique de confidentialité mise à jour.
• Corrections et améliorations.
```

### `it`
```
• Informativa sulla privacy aggiornata.
• Correzioni e miglioramenti.
```

### `pt-BR`
```
• Política de Privacidade atualizada.
• Correções e melhorias.
```

### `pl`
```
• Zaktualizowana polityka prywatności.
• Poprawki i ulepszenia.
```

### `ja`
```
• プライバシーポリシーを更新しました。
• 不具合の修正と改善。
```

### `nl`
```
• Privacybeleid bijgewerkt.
• Oplossingen en verbeteringen.
```

Promotional text for 1.8.2 is in the paste sheet. It is not tied to a build,
so it can go on the live version today.

---

## Release checklist (owner)

Nothing is submitted until the last step, and that one is yours.

### 1. Before archiving
- [ ] `git pull` on `main` and confirm the bump is in the checkout:
      `grep -m1 "CURRENT_PROJECT_VERSION = 34" ios/SnapWorth.xcodeproj/project.pbxproj`.
- [ ] CI green on that `main` commit.

### 2. Archive
- [ ] Xcode 27 (`.xcode-version`), scheme **SnapWorth**, Release. Organizer
      shows **1.8.2 (34)**.
- [ ] Distribute → App Store Connect → Upload, once. A re-archive needs 35.
- [ ] Record the commit and Organizer's creation date under *Build 34*.

### 3. TestFlight
- [ ] 34 has no *Missing Compliance* badge.
- [ ] Paste *What to Test*, then run it once on a device.

### 4. Version page
- [ ] Create version `1.8.2`, select build **34**.
- [ ] Screenshots: check each display size shows the new set (a new version
      starts with the previous version's screenshots).
- [ ] What's New, all locales (above).
- [ ] App Privacy answers: unchanged from 1.8.1.

### 5. Release
- [ ] Phased Release for Automatic Updates: on.
- [ ] Submit for review (owner only).

### Build 34 — the archive

- **Archive commit:** _not yet archived_
- **Organizer creation date:** _not yet archived_
