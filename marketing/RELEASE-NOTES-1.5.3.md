# What's New — SnapWorth 1.5.3

## Scope

1.5.2 (22) was approved on 2026-10-07. 1.5.3 (23) is a patch release in
the *Evidence & growth* milestone. It ships early: the roadmap (#233) had it
for ~10 Dec, and the owner moved the submit to ~Sun/Mon 2026-10-11/12. So it
carries what merged after 22's archive, and nothing that waits on measured
data. The calibrated confidence copy (#226) and a market-aware prompt (#225)
need the gold set (#213). Their tooling is merged, but no user-facing change
depends on them in this build.

**What 22 contains is recorded.** It was archived from `bfcef96`
(`RELEASE-NOTES-1.5.2.md`, *Build 22*). Every app change below is new
since then, from `git log --merges --first-parent bfcef96..main` filtered to
merges that touch `ios/`.

**Referral copy was already in 22.** RUNBOOK §18 said `4f1c591` (the
renewal wording) and `0074d2a` (the trial reminder for offer-code weeks)
would first reach 1.5.3. Both are ancestors of `bfcef96`, so 22 has them.
The minimum build for referrals is still **23**, because 22's in-app privacy
policy has the old invite-code sentence that #306 corrected. A device on 22
would start minting codes that its own policy says it doesn't keep.

### Ships in the binary (users get it only with 23)

| PR | What | Visible? |
|----|------|----------|
| [#298](https://github.com/hsilviu05/SnapWorth/pull/298) | Settings → Privacy → **Share sale prices to improve estimates**, off by default, with a one-time card the first time a sold price is saved, and *Delete my shared sales*. A sold flip has a **Currency** (defaulting from the phone's region), and its paid, sold, fee and profit rows show it (#224) | **Yes** |
| [#306](https://github.com/hsilviu05/SnapWorth/pull/306) | The in-app privacy policy discloses shared sales, and says an invite code is made for every device once Invite a friend is available. Dated October 6, 2026, the same as `/privacy` | Yes, in Settings → Privacy Policy |
| [#288](https://github.com/hsilviu05/SnapWorth/pull/288)–[#293](https://github.com/hsilviu05/SnapWorth/pull/293) | ResultView split into sections with explicit inputs, in six stages (#229). The intent is no visual change | No, if the walkthrough below passes |
| [#311](https://github.com/hsilviu05/SnapWorth/pull/311) | `haulWait` counts polls rather than wall-clock time, so a slow CI runner can't fail `HaulSessionTests` (#299) | No, tests only |
| this PR | `chore: 1.5.3, build 23`, a lower bound for 23, not the build | No |

### Server and website (deployed on merge; already serving 22)

| PR | What |
|----|------|
| [#296](https://github.com/hsilviu05/SnapWorth/pull/296), [#297](https://github.com/hsilviu05/SnapWorth/pull/297) | `POST` and `DELETE /outcomes`, the receiver 23's sale sharing sends to, and the field report over it (#224) |
| [#301](https://github.com/hsilviu05/SnapWorth/pull/301) | Referrals behind `/referrals on` and a minimum build (#227). Still **off** |
| [#287](https://github.com/hsilviu05/SnapWorth/pull/287) | Fee pages for Poshmark, Mercari and Depop, linked from every guide (#228) |
| [#306](https://github.com/hsilviu05/SnapWorth/pull/306) | `/privacy`, the web copy of the policy change above |
| [#295](https://github.com/hsilviu05/SnapWorth/pull/295), [#300](https://github.com/hsilviu05/SnapWorth/pull/300), [#303](https://github.com/hsilviu05/SnapWorth/pull/303), [#305](https://github.com/hsilviu05/SnapWorth/pull/305), [#307](https://github.com/hsilviu05/SnapWorth/pull/307), [#308](https://github.com/hsilviu05/SnapWorth/pull/308) | notify.py split (#230), no behaviour change |
| [#302](https://github.com/hsilviu05/SnapWorth/pull/302), [#304](https://github.com/hsilviu05/SnapWorth/pull/304) | RUNBOOK: Redis runs with AOF and RDB on its volume (#207) |

### Repository only

| PR | What |
|----|------|
| [#286](https://github.com/hsilviu05/SnapWorth/pull/286) | The accuracy gate is armed: fail closed, private photos, pinned config, weekly run (#215) |
| [#309](https://github.com/hsilviu05/SnapWorth/pull/309) | Calibration tools: Platt scaling and a per-band reliability table (#226, part 1) |
| [#312](https://github.com/hsilviu05/SnapWorth/pull/312) | Per-region scoring in dollars from a pinned ECB table (#225) |

### Not in this build

* Anything merged after this file and before the archive is in 23 too. Add
  it to the tables above when the archive commit is recorded below.

---

## What's New, all five locales

Paste each block into its locale's What's New field on the 1.5.3 version
page. The feature names come from `ios/Localization/App.json` ("My Flips",
"Settings", "Privacy", "Currency" and the toggle), so the store and the app
use the same words. Referrals are left out: they are switched on after
approval, with a 7-day soft launch (RUNBOOK §18). None of the four
translations has had a native reader yet.

Counts are Unicode characters, newlines included. App Store Connect's limit
is 4000.

| Locale | Characters |
|---|---|
| `en-US` | 274 |
| `ro` | 311 |
| `es` | 314 |
| `de` | 325 |
| `zh-Hans` | 94 |

### `en-US`

```
New: choose the currency of each sale in My Flips, and your profit is shown in it.

Optional: Settings → Privacy → Share sale prices to improve estimates. It's off unless you turn it on, and it never sends your photos, item names or what you paid.

• Fixes and improvements.
```

### `ro`

```
Nou: alege moneda fiecărei vânzări în Flipuri, iar profitul tău este afișat în ea.

Opțional: Setări → Confidențialitate → Trimite prețurile de vânzare pentru estimări mai bune. Este oprit până îl pornești și nu trimite niciodată fotografiile, numele articolelor sau cât ai plătit.

• Remedieri și îmbunătățiri.
```

### `es`

```
Novedad: elige la moneda de cada venta en Mis reventas y verás tu beneficio en ella.

Opcional: Ajustes → Privacidad → Compartir precios de venta para mejorar las estimaciones. Está desactivado hasta que lo actives y nunca envía tus fotos, los nombres de los artículos ni lo que pagaste.

• Correcciones y mejoras.
```

### `de`

```
Neu: Wähle in Meine Flips die Währung jedes Verkaufs, dein Gewinn wird darin angezeigt.

Optional: Einstellungen → Datenschutz → Verkaufspreise teilen, um Schätzungen zu verbessern. Es ist aus, bis du es einschaltest, und sendet nie deine Fotos, Artikelnamen oder deinen Einkaufspreis.

• Fehlerbehebungen und Verbesserungen.
```

### `zh-Hans`

```
新增：在“我的转卖”中为每笔售出选择币种，利润将以该币种显示。

可选：设置 → 隐私 → 分享售价以改进估价。默认关闭，且绝不会发送你的照片、物品名称或买入价格。

• 问题修复与改进。
```

### What these leave out

Referrals (off until after approval), the ResultView split (no visible
change), the policy wording, and every server, website and repository
change.

### TestFlight — What to Test

```
Please test on this build:
• Sale sharing: mark an item as sold and enter a price. A card should ask once whether to share sale prices. Try both answers on two installs. Then check Settings → Privacy: the toggle matches, and "Delete my shared sales" appears once something has been shared.
• Currency: the sold fields show a Currency menu. Pick one and check the paid, sold, fee and profit rows all use it.
• Scan results: open a fresh scan and a saved find, free and Pro. Everything should look and work as it did in 1.5.2.
```

---

## Testing on a device — required before submitting

All of these are owner checks, on the TestFlight build against production.
**Remove and re-add every widget first** (CLAUDE.md). Tick each check, or
open an issue for the failure that names the build its fix needs.

### Sale sharing (#224, [#298](https://github.com/hsilviu05/SnapWorth/pull/298))

- [ ] On a fresh install, the toggle in Settings → Privacy is **off**, and
      marking a flip sold sends nothing (`/outcomes` count unchanged in the
      field report).
- [ ] The first sold price saved shows the one-time card. *Not now* is
      final: the card does not come back, and the toggle stays off.
- [ ] *Allow* turns the toggle on. Closing the result sheet sends the sale.
      Editing the sold price and closing again replaces it, and doesn't add a
      second record.
- [ ] Un-marking the sale deletes its record. *Delete my shared sales*
      asks for confirmation, then reports the result, and the field report
      drops those records.
- [ ] Airplane mode: a send fails quietly and goes through at the next close
      once online.

### Currency (#224)

- [ ] With the phone's region set to Romania, a new sold flip defaults to
      **RON**. Germany gives **EUR**, the US **USD**, and a region outside
      the server's list gives **USD**.
- [ ] Paid, sold, fees and profit all show the chosen symbol, and a flip
      from 22 (no currency stored) still reads as before.

### ResultView walkthrough (#229 acceptance)

Compare against 22 side by side, on a fresh scan and on a reopened find, as
free and as Pro. Check the guess cover and reveal, the condition chip, mark
listed or sold with profit, Add the tag (camera and library), Why this price
(teaser, re-read, "scanned before Pro"), Snap → Sell generate, copy and
share, listing-photo cleanup, and the share card.

- [ ] Nothing differs. Attach the screenshots to #229 and close it.

### Privacy policy (#306)

- [ ] Settings → Privacy Policy reads "Last updated: October 6, 2026" and
      has the *Share sale prices to improve estimates* paragraph, the same as
      `https://api.snapworth.eu/privacy`.

---

## Pre-submit checklist (owner)

- [ ] **App Privacy label.** Add *Other Financial Info*: not linked to the
      user, not used for tracking, purpose *Analytics*. That matches
      `PrivacyInfo.xcprivacy` as of #298. The label and the manifest must
      agree.
- [ ] **Localized store pages.** Metadata belongs to a version. If the four
      localizations rode 1.5.2, they carry over to 1.5.3's version page.
      Re-check each one before submitting, and don't add a language now
      that 1.5.3 is in review.
- [ ] `SANDBOX_ENTITLEMENTS` is `bounded` while 23 is with App Review
      (RUNBOOK §17), and there is no public TestFlight link.
- [ ] **Referrals decision.** Shipping 23 with referrals off is fine,
      because the switch is server-side. If they are to launch with 23,
      first complete RUNBOOK §18's pre-flight (the two ASC offers, loaded
      pools, `REFERRAL_FRIEND_OFFER`, the live `/i/<code>` page). Then run
      `/referrals build 23`, and add the referral paragraph below to the
      review notes.
- [ ] **Archive** with Xcode 27 from a fresh pull of `main`, after the last
      1.5.3 PR has merged. First confirm the bump is in the checkout:
      `git merge-base --is-ancestor <this PR's merge commit> HEAD && echo ok`.
      An archive from a stale checkout silently reuses 22, and App Store
      Connect rejects it.
- [ ] Record the archive below, then tag it.
- [ ] Upload 23 **once**. A rejection that needs a new archive bumps to 24
      first.
- [ ] What's New pasted in all five locales, from the blocks above.
- [ ] App Review notes pasted (below).
- [ ] Phased release on.

### Build 23 — the archive

The `chore: 1.5.3, build 23` commit is a lower bound for what 23 contains,
not the build (CLAUDE.md). This record is the build.

- **Archive commit** (`git rev-parse HEAD` in the checkout, at archive time):
  **TODO(owner)**
- **Organizer creation date:** **TODO(owner)**
- **Tag** it:

  ```sh
  git tag build-23 <archive-commit> && git push origin build-23
  ```

### App Review notes (paste into *App Review Information → Notes*)

```
No account is needed. SnapWorth works from the first launch without signing in.

In-app purchases made in the review Sandbox unlock SnapWorth Pro immediately. To see the Pro features, buy from the paywall, for example from "Unlock why this price" on a scan result. You can then open "Why this price", create listing drafts, use Haul mode (tap "Haul" above the shutter), and export sold items from My Flips (mark an item as sold, then tap ⋯ → Export photos and estimates).

New in this version: an optional, off-by-default setting, Settings → Privacy → "Share sale prices to improve estimates". When it is on, marking an item as sold sends the sale price, its currency and the estimate shown at scan time to our server, to measure estimate accuracy. It never sends the photo, the item's name, notes, the price paid or a device identifier. The privacy policy (Settings → Privacy Policy) describes it, and "Delete my shared sales" removes everything sent.

The iMessage stickers are in the sticker drawer. In Messages, open the emoji keyboard and go to Stickers to find Tag's pack.
```

Only if referrals launch with 23, add (with a real code from the friend pool):

```
Invite a friend: Settings → Invite a friend shows a share link and this device's invite code. To redeem one, open the paywall, tap "Have an invite code?" and enter TODO-CODE. Apple's offer-code sheet then shows the free week. The invite code, and a record when one is claimed, are described in the privacy policy.
```

---

## After approval (owner)

- [ ] Field report: the first shared outcomes arrive once 23 is live and a
      user opts in. Zero for a week is a real result, so record it in #224.
- [ ] Referrals, if launching: `/referrals on` after approval, then no
      announcement for 7 days (RUNBOOK §18). Don't move the free-scan lever
      or a trial arm inside that window.
- [ ] `/minbuild 23` only if a 22 bug requires it, and only once 23 is live.
