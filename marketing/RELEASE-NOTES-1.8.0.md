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
| this PR | 1.8.0, build 31 | No |

The backend half of #450 (`POST /assist`, `/privacy` dated November 5) deploys
to Railway from the merge on `main`. It must be live before testing Ask Tag:
an older backend answers 404, which the app shows as "Tag couldn't answer
right now".

26 new strings, all in `App.json` in five languages. "Ask Tag" is a name and
stays Ask Tag.

### Not yet written

What's New and promotional text in the five locales. Write them before
submitting, not before TestFlight.

### TestFlight — What to Test

```
Please test on this build:
• First launch after updating: the "Before your first scan" screen appears once. Agree, then scan normally.
• Pro: open a result, tap "Ask Tag a question", try a suggested question and one of your own.
• Ask in another app language (e.g. Romanian): Tag answers in that language.
• Long-press an answer → "Report this answer" opens a pre-filled email.
• Free: the Tag's tips card says "Unlock Tag's tips" and the paywall shows the Ask Tag row.
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
- [ ] Free: no Ask Tag button; paywall shows the Ask Tag row.

---

## Release checklist (owner)

Nothing is submitted until the last step, and that one is yours.

### 1. Before archiving
- [ ] `git pull` on `main` and confirm the bump is in the checkout:
      `grep -m1 "CURRENT_PROJECT_VERSION = 31" ios/SnapWorth.xcodeproj/project.pbxproj`.
- [ ] CI green on that `main` commit (iOS, backend, secrets).
- [ ] Railway deploy of #450 finished; `https://api.snapworth.eu/privacy`
      shows November 5, 2026.

### 2. Archive
- [ ] Xcode 27 (`.xcode-version`), scheme **SnapWorth**, Release. Organizer
      shows **1.8.0 (31)**.
- [ ] Distribute → App Store Connect → Upload, once. A re-archive needs 32.
- [ ] Record the commit and Organizer's creation date under *Build 31*.

### 3. TestFlight
- [ ] 31 has no *Missing Compliance* badge.
- [ ] Paste *What to Test*; run the device checks above.

### 4. Version page
- [ ] Create version `1.8.0`, select build **31**.
- [ ] What's New and Promotional Text, all locales (not yet written).
- [ ] **App Privacy:** Ask Tag questions are *User Content → Other User
      Content*, used for *App Functionality*, not linked to identity, no
      tracking. Add it if it is not already declared.
- [ ] `PrivacyInfo.xcprivacy`: matches the App Privacy answers.
- [ ] Age rating: re-answer only if the questionnaire asks about AI chat or
      user-to-user messaging (Ask Tag is neither user-to-user nor shared).
- [ ] App Review notes: Ask Tag is Pro-only; give the Sandbox tester account
      and say where the button is (open any result → Tag's tips →
      "Ask Tag a question"). Mention the consent screen (5.1.2(i)).

### 5. Release
- [ ] Phased Release for Automatic Updates: on.
- [ ] Submit for review (owner only).

### Build 31 — the archive

- **Archive commit:** _not yet_
- **Organizer creation date:** _not yet_
