# What's New — SnapWorth 1.5.1

## Scope

1.5.0 (20) is approved and live (iTunes lookup, 2026-09-26). 1.5.1 (21) is a
patch release: it ships what merged on 09-26 and 09-27, and it is the version
that carries the four localized store pages (#202). Cut Wed 2026-09-30,
submit Fri 2026-10-02.

**Where 20 ends is a lower bound, not a fact.** `RELEASE-NOTES-1.5.0.md`
records only that 20 was to be archived "from `main` after the merge of
`feature/imessage-stickers`": [#179](https://github.com/hsilviu05/SnapWorth/pull/179),
merge `46b84ba` (2026-09-25 19:51 +0300), bump `435cba6`. No archive commit
and no Organizer date were written down for 20. So the tables below list
every PR merged to `main` after `46b84ba`. The four PRs merged 2026-09-26
13:52 (#180–#183) are counted as new on #201's reading that 20 was already
live by the 09-26 lookup. If Organizer's 1.5.0 archive is dated after
2026-09-26 13:52 +0300, they were in 20 already. Only #181 touches the app,
and it shows nothing new while comps are off, so either way no user sees a
difference.

**TODO(owner):** Organizer's creation date for the 1.5.0 (20) archive: ______

### Ships in the binary (users get it only with 21)

| PR | What | Visible? |
|----|------|----------|
| [#181](https://github.com/hsilviu05/SnapWorth/pull/181) | `valuation_source` decoded; the result's caption worded by it (#40) | No. Every result still says "AI estimate" while `COMPS_ENABLED` is false |
| [#187](https://github.com/hsilviu05/SnapWorth/pull/187) | Haul mode, Pro: scan a pile item after item, with a running total (#93) | **Yes** |
| [#190](https://github.com/hsilviu05/SnapWorth/pull/190) | iOS half: the in-app privacy policy says notable finds wait for three devices | Privacy text |
| [#192](https://github.com/hsilviu05/SnapWorth/pull/192) | Readable Pro panel (no raw tokens), honest share card, condition chip, VoiceOver and copy fixes | **Yes** |
| [#193](https://github.com/hsilviu05/SnapWorth/pull/193) | Notifications: free-scan reminder at the UTC reset, weekly digest, trial reminder in waking hours, a stale thrift run refreshed on open, in-app permission ask; review prompt after the reveal | **Yes** |
| [#194](https://github.com/hsilviu05/SnapWorth/pull/194) | Paid path: SAVE badge, full detail after buying from "Why this price", subscription resync, a scan survives a lock | **Yes** |
| [#195](https://github.com/hsilviu05/SnapWorth/pull/195) | iOS half: the paywall's portfolio line sells its history, not its value | One paywall line |
| [#196](https://github.com/hsilviu05/SnapWorth/pull/196) | Purchase History in `PrivacyInfo.xcprivacy`; the in-app policy matches the server | Privacy text; manifest |
| [#235](https://github.com/hsilviu05/SnapWorth/pull/235) | P2, iOS half: the fair-use footnote under "Unlimited scans"; Haul's comments on the new buckets | One paywall line |
| [#236](https://github.com/hsilviu05/SnapWorth/pull/236) | P3, iOS half: referral client hardening, dormant while referrals are off; the "trial ends tomorrow" reminder now covers any free offer period (4f1c591) | The reminder only |
| [#237](https://github.com/hsilviu05/SnapWorth/pull/237) | Q: server errors and confidence reasons worded in the app's language from their codes; `X-SnapWorth-Build` on every request; 426 update screen | **Yes**, outside English |
| [#238](https://github.com/hsilviu05/SnapWorth/pull/238) | R: "likely" is the model's expected price, not the range midpoint, and one figure everywhere; `LedgerMath`; a one-time recompute of stored values on first launch | **Yes**, figures |
| [#239](https://github.com/hsilviu05/SnapWorth/pull/239) | S: "Next free scan at …" and one-tap "Remind me"; the paywall headline matches what opened it | **Yes** |
| [#240](https://github.com/hsilviu05/SnapWorth/pull/240) | T: five-figure totals fit the widget; tinted Home Screen; ISRG root pins (enforcement stays off); off-main photo decode; Xcode 27 CI | **Yes**, widgets |

### Server-side (deployed on merge; already serving 1.5.0 (20))

| PR | What |
|----|------|
| [#180](https://github.com/hsilviu05/SnapWorth/pull/180) | Comps in shadow mode: run beside every scan, serve nothing (#39) |
| [#182](https://github.com/hsilviu05/SnapWorth/pull/182) | Sell-through from the comp set (#43), dormant with comps |
| [#183](https://github.com/hsilviu05/SnapWorth/pull/183) | Tests stay out of the developer's `.env` |
| [#184](https://github.com/hsilviu05/SnapWorth/pull/184) | CI: deploy one commit at a time, newest wins |
| [#185](https://github.com/hsilviu05/SnapWorth/pull/185) | #92 research; the Nuptse page stops claiming prices peak (website) |
| [#186](https://github.com/hsilviu05/SnapWorth/pull/186) | `docs/AUDIT-2026-09-26.md` |
| [#188](https://github.com/hsilviu05/SnapWorth/pull/188) | Request bodies capped as they arrive; client hang-ups; the calling build |
| [#189](https://github.com/hsilviu05/SnapWorth/pull/189) | Refunds and reinstalls: required writes, REFUND_REVERSED, DeviceCheck flag |
| [#190](https://github.com/hsilviu05/SnapWorth/pull/190) | Server half: ops bot `/sub` IDs, `/checkup`, `/clear`; trends count distinct devices |
| [#191](https://github.com/hsilviu05/SnapWorth/pull/191) | AI pipeline: refuse partial prices, bound listing asks, classify 429s (its iOS change is a test only) |
| [#192](https://github.com/hsilviu05/SnapWorth/pull/192), [#194](https://github.com/hsilviu05/SnapWorth/pull/194), [#196](https://github.com/hsilviu05/SnapWorth/pull/196) | Server halves; #196's served `/privacy` is already updated |
| [#195](https://github.com/hsilviu05/SnapWorth/pull/195) | Website: Thrift Flip is free, support page, nine marketplaces, analytics |
| [#197](https://github.com/hsilviu05/SnapWorth/pull/197) | CI and ops: secret scan of pushed commits, eval gate, uptime probe, Redis runbook |
| [#198](https://github.com/hsilviu05/SnapWorth/pull/198) | Sandbox purchases honoured in production, bounded (RUNBOOK §17) |
| [#199](https://github.com/hsilviu05/SnapWorth/pull/199) | `vercel.json` moved to `website/`, where Vercel reads it |
| [#234](https://github.com/hsilviu05/SnapWorth/pull/234) | P1: ops bot tells an unreachable Apple from a rejected key; one welcome rule; `/experiment export` |
| [#235](https://github.com/hsilviu05/SnapWorth/pull/235) | P2: separate rate buckets, Pro 60 scans an hour, spend-alert warning, no thinking on `/listing` |
| [#236](https://github.com/hsilviu05/SnapWorth/pull/236) | P3: referral pre-flight (referrals stay off) |
| [#237](https://github.com/hsilviu05/SnapWorth/pull/237), [#238](https://github.com/hsilviu05/SnapWorth/pull/238), [#239](https://github.com/hsilviu05/SnapWorth/pull/239) | Server halves: error `code`s and the 426 gate; `likely_price_usd`; the quota reset header |
| [#241](https://github.com/hsilviu05/SnapWorth/pull/241) | U: scan prompt v2.1 (v2 still selectable) |
| [#243](https://github.com/hsilviu05/SnapWorth/pull/243) | #210: startup and Checkup say when `AUDIT_SALT` is a published placeholder; the Sandbox notification route gets the notification body cap |
| [#244](https://github.com/hsilviu05/SnapWorth/pull/244) | The Telegram bot token no longer reaches the logs (httpx URLs were not redacted). Rotate it: RUNBOOK §8.6 |
| [#250](https://github.com/hsilviu05/SnapWorth/pull/250) | Per-IP limits keyed on the caller, not on Railway's CDN edge, which every user behind one edge shared. **Must be live before 21 is released** (pre-submit list) |

### Repository only (no user or server effect)

| PR | What |
|----|------|
| [#245](https://github.com/hsilviu05/SnapWorth/pull/245) | The five listing files for 1.5.1 (#202 steps 1–6): Haul and portfolio-history lines, no trial length, Spanish (Mexico) |
| [#246](https://github.com/hsilviu05/SnapWorth/pull/246) | CI: one `All required checks` job for the ruleset (#209) |
| [#248](https://github.com/hsilviu05/SnapWorth/pull/248) | `chore: 1.5.1, build 21` — a lower bound for 21, not the build |
| [#249](https://github.com/hsilviu05/SnapWorth/pull/249) | `docs/GROWTH-DASHBOARD.md` and the 1.5.0 baseline log (#205) |

### Not in this build

* [#200](https://github.com/hsilviu05/SnapWorth/pull/200), the rare-find easter egg (V), is **held** and does not ship. It stays
  unmerged until the owner says otherwise.
* Anything merged after this file and before the archive is in 21 too. Add
  it to the tables above when the archive commit is recorded below.

---

## What's New, all five locales

Paste each block into its locale's What's New field on the 1.5.1 version
page. The `release-notes` skill drafted `en-US` and `ro`. `de`, `es` and
`zh-Hans` were written by hand in the voice of
`app_store_listing.<locale>.md`, with feature names taken from
`ios/Localization/App.json` so the store page and the app use the same words.
None of the four translations has had a native reader yet. Have each one read
before pasting, and record the reader in its listing file (#202 step 11).

The blocks follow *Claims this listing does not make* in
`app_store_listing.md`. They give no trial length and no accuracy word, and
they don't mention sold listings, comps or market data. They also avoid
"unlimited". Haul mode is introduced as Pro.

Counts are Unicode characters, newlines included. App Store Connect's limit
is 4000.

| Locale | Characters |
|---|---|
| `en-US` | 618 |
| `ro` | 703 |
| `es` | 736 |
| `de` | 778 |
| `zh-Hans` | 259 |

**Lines that depend on a device check.** If the check fails, drop the line
in every locale:
- The Haul paragraph depends on the Haul checks (#93). If Haul is held back,
  open with the free-scan line instead.
- "A scan keeps going if you lock your phone" depends on the lock check
  (#194).
- The widgets line depends on T's widget checks. T's own commit says the
  tinted rendering was not checked by eye.

### `en-US`

```
New in Pro: Haul mode. Snap a whole pile, item after item, and each one is valued as you go, with a running total.

• Used today's free scan? See when the next one is back, and set a reminder in one tap.
• Each find now has one value everywhere — your total, Thrift Flip, listing drafts and widgets — based on the AI's expected price.
• Scan errors now appear in your language.
• A scan keeps going if you lock your phone.
• Upgrade from "Why this price" on a new scan and the full breakdown appears right away.
• Widgets fit five-figure totals and read clearly on a tinted Home Screen.
• VoiceOver and reminder fixes.
```

### `ro`

```
Nou în Pro: modul Lot. Fotografiezi un lot întreg, obiect după obiect, iar fiecare e evaluat pe loc, cu totalul la vedere.

• Ai consumat scanarea gratuită de azi? Vezi când revine și îți pui un memento dintr-o atingere.
• Fiecare găselniță are acum o singură valoare peste tot — în total, în Thrift Flip, în anunțuri și în widgeturi — pornind de la prețul așteptat de AI.
• Erorile de scanare apar acum în limba ta.
• Scanarea continuă și dacă blochezi telefonul.
• Treci la Pro din „De ce prețul ăsta” la o scanare nouă și vezi imediat explicația completă.
• Widgeturile încap acum totaluri de cinci cifre și se citesc bine și pe ecranul principal nuanțat.
• Remedieri pentru VoiceOver și memento-uri.
```

### `es`

```
Novedad en Pro: el modo Lote. Fotografía un montón entero, artículo tras artículo, y cada uno se valora al momento, con el total a la vista.

• ¿Has gastado el escaneo gratis de hoy? Verás cuándo vuelve y puedes activar un aviso con un toque.
• Cada hallazgo tiene ahora un solo valor en todas partes (el total, Thrift Flip, los anuncios y los widgets), a partir del precio esperado que da la IA.
• Los errores de escaneo aparecen ahora en tu idioma.
• El escaneo sigue aunque bloquees el móvil.
• Hazte Pro desde «Por qué este precio» en un escaneo nuevo y verás al momento la explicación completa.
• Los widgets muestran totales de cinco cifras y se leen bien en una pantalla de inicio tintada.
• Mejoras en VoiceOver y en los avisos.
```

### `de`

```
Neu in Pro: der Stapel-Modus. Fotografier einen ganzen Stapel, Artikel für Artikel – jeder wird nebenbei bewertet, mit laufender Summe.

• Gratis-Scan von heute verbraucht? Du siehst, wann der nächste wieder da ist, und stellst dir mit einem Tipp eine Erinnerung.
• Jeder Fund hat jetzt überall denselben Wert – in der Summe, bei Thrift Flip, in Inseraten und in Widgets –, ausgehend vom erwarteten Preis der KI.
• Scan-Fehler erscheinen jetzt in deiner Sprache.
• Ein Scan läuft weiter, auch wenn du das iPhone sperrst.
• Hol dir Pro über „Warum dieser Preis“ bei einem neuen Scan, und die ganze Erklärung ist sofort da.
• Widgets zeigen fünfstellige Summen vollständig und bleiben auf einem getönten Home-Bildschirm gut lesbar.
• Verbesserungen bei VoiceOver und Erinnerungen.
```

`app_store_listing.de.md` says "Why this price" stayed English in the app. It
no longer has: `App.json` has „Warum dieser Preis“ for `de`, so this block
uses that.

### `zh-Hans`

```
Pro 新增：批量模式。一件接一件拍，边拍边估价，实时显示总额。

• 今天的免费扫描用完了？能看到下一次什么时候恢复，点一下就能开启提醒。
• 每件好物现在处处都是同一个价值：总值、Thrift Flip、商品描述和小组件，都按 AI 给出的预期价格计算。
• 扫描出错时的提示现在会用你的语言显示。
• 扫描时锁屏，扫描也会继续。
• 在新扫描的结果里通过“为什么是这个价”升级 Pro，马上就能看到完整说明。
• 小组件能完整显示五位数的总值，主屏幕着色后也看得清。
• 改进了旁白（VoiceOver）和提醒。
```

### What these leave out

These changes are not mentioned: the paywall headline per trigger, the
`X-SnapWorth-Build` header and the 426 screen, `valuation_source`, the
privacy manifest and policy text, referral hardening (dormant), certificate
pins, off-main decode, Xcode 27 CI, and every server-side PR.

### TestFlight — What to Test

```
Please test on this build:
• Haul mode (Pro): tap Haul above the shutter and photograph 12 items in a row. The running total should fill in and no photo should be lost, even if you lock the phone.
• Free scans: use the day's free scan and try another. The scan screen should say when the next free scan is back; tap Remind me.
• Buying Pro: on a new scan, tap "Unlock why this price" and buy. The breakdown should fill in with no "We couldn't confirm" alert.
• Language: set the phone to Romanian, Spanish, German or Chinese and scan something that isn't for sale. The message should be in that language.
• Widgets: remove and re-add them first. A total over $10,000 should fit, and on a tinted Home Screen the Haul widget's Scan button should show.
```

---

## Testing on a device — required before submitting

All of these are owner checks, on the TestFlight build against production.
**Remove and re-add every widget first**, because iOS keeps widget snapshots
across an extension update (CLAUDE.md). Tick each check, or open an issue for
the failure that names the build the fix needs.

### Haul (#93; close #93 only when these pass)

- [ ] 12 items in under 2 minutes on Wi-Fi, with no dropped capture. Release
      builds don't log `haul: N valued in Xs`, so use a stopwatch. P2
      ([#235](https://github.com/hsilviu05/SnapWorth/pull/235)) merged
      2026-09-27 and removed the shared 20-an-hour device bucket that would
      have paused a 12-item haul plus drafts (~24 requests). Pro scans now
      have their own 60-an-hour bucket (`PRO_SCAN_RATE_MAX_REQUESTS`), and so
      do drafts (`LISTING_RATE_MAX_REQUESTS`), under the 60-an-hour
      per-address cap (`IP_RATE_MAX_REQUESTS`). Run this only once that
      deploy is live.
      **The per-address cap was not per address on 09-27.** Behind Railway's
      CDN, `X-Forwarded-For` arrives as *client, Fastly edge*, and the server
      keyed the cap on the edge, so everyone routed through one edge shared
      one 60-an-hour bucket (#206's smoke test). One tester's haul still fits
      under it; a launch day would not. The fix must be deployed before 21 is
      released, and this check passing does not show it is.
- [ ] A 429 shows the countdown, and no photo is lost across lock, kill and
      relaunch. TestFlight can't take `-mock-scan-429`, which is DEBUG only:
      run this one from Xcode with the argument set, on the same device.
- [ ] Airplane mode pauses the queue with the offline banner. The photos
      don't turn red.
- [ ] VoiceOver lands on the summary header, and the camera underneath can't
      be reached.
- [ ] At AX3 with two banners showing, the shutter and Finish stay on screen.

### Sandbox purchase (#198)

- [ ] On a fresh result, buy from "Unlock why this price". The panel fills in
      and no "We couldn't confirm your subscription" alert appears. Listing
      drafts and the tag re-read work.
- [ ] `/subs` gains no row and no "New Pro" alert fires. Railway logs
      `sandbox entitlement recorded on bounded terms` (RUNBOOK §17; read-only
      `railway logs`).
- [ ] A second device on the same Sandbox account takes Pro over. The first
      reads free on its next request.
- [ ] Refund path: `backend/tools/appstore_test_notification.py --sandbox`
      succeeds. It needs the Sandbox Server URL from the ASC config issue
      (#208). If a real Sandbox REFUND can't be produced for a TestFlight
      purchase, record that here.

### Paid path, copy, widgets

- [ ] Yearly shows SAVE 33% (#194) in `en` and one other locale.
- [ ] The Pro panel shows no raw tokens (`cannot_verify`, `high`) in any of
      the five languages (#192).
- [ ] Locking the phone during "Analyzing…" still delivers the result
      (#194).
- [ ] A stale thrift run turns live when the app opens. With notification
      settings never asked, the app asks in-app (#193).
- [ ] VoiceOver: My Finds' "Unlock value history" is reachable and the
      insight line is read (#192). The paywall and the Haul summary can be
      navigated.

### Q — error codes, localized errors, build header, 426 ([#237](https://github.com/hsilviu05/SnapWorth/pull/237))

- [ ] Set the phone to German, or any of the four non-English languages, and
      scan a plate of food. The alert reads „Das sieht nicht nach etwas mit
      Wiederverkaufswert aus. …“, not the server's English. In English the
      server's own words still show, including the model's reason.
- [ ] In the same language, a Pro result's confidence reasons are
      translated. The one-line confidence summary stays English, as
      `ios/Localization/README.md` records.
- [ ] **Do not test the 426 screen against production.** `/minbuild 22`
      would refuse every 1.5.0 (20) install, and there is no newer build for
      them to update to. The routing is covered by `ServerErrorCodeRoutingTests`
      and the header by `BuildHeaderTests.test_everyAPIRequestSaysWhichBuildItIs`.
      Only builds from 21 on send the header, so the server answers 426 only
      to them. Everything older gets the 422 (RUNBOOK §1b). The 426 screen
      will first be seen for real when a later `/minbuild` gates 21.

### R — likely price, LedgerMath ([#238](https://github.com/hsilviu05/SnapWorth/pull/238))

- [ ] Install 21 over 1.5.0 (20) with saved finds. My Finds' total may move
      once on first launch, from the one-time recompute (`PricingRules.current = 2`).
      Relaunch: it doesn't move again.
- [ ] Take a Pro scan and note the Expected price in "Why this price". With
      the condition unchanged, Thrift Flip's "Expected resale" starts at that
      figure. My Finds' total rises by it, and the Home Screen widget's total
      matches My Finds.

### S — return loop, paywall per trigger ([#239](https://github.com/hsilviu05/SnapWorth/pull/239))

- [ ] Free user: spend the day's scan and try another. The paywall reads
      "Keep scanning today", with "Next free scan at <local time>" under the
      subheadline. From two days in a row on, "🔥 N-day streak" is prefixed.
      Close it: the Scan tab shows "Next free scan at …" and "Remind me". The
      time is 00:00 UTC in local time (03:00 in Romania in summer).
- [ ] "Remind me" on a phone never asked for notifications brings up iOS's
      prompt. Once allowed, it reads "Reminder set for …".
- [ ] The headline matches the trigger. The Haul pill as a free user gives
      "Scan a whole haul". "Unlock why this price" on a fresh result gives "See
      why this price". A find reopened from My Finds does **not** say "See why
      this price". Check one of these in a second language.

### T — widgets, pins, off-main decode, Xcode 27 ([#240](https://github.com/hsilviu05/SnapWorth/pull/240))

- [ ] With a library whose best case passes $9,999, the small Haul widget
      shows the whole range (for example "$8,400–$15,600"), not "$8,400–$15,…".
      Check an iPhone SE-sized tile too if one is available.
- [ ] On a Tinted (and a Clear) Home Screen, the medium Haul widget's Scan
      button is an outline with a readable label, not a blank pill. The
      headline figures take the tint.
- [ ] Scanning a full-resolution library photo shows the analysing overlay
      with no stall. In Haul, quick successive captures keep the shutter
      responsive.
- [ ] `/checkup`'s TLS line shows the served chain pinned, with no ⚠️
      (server-side; pinning enforcement stays off).
- [ ] Organizer shows the 21 archive was built with Xcode 27.

---

## Pre-submit checklist (owner, Fri 2026-10-02)

- [ ] **The listing issue (#202) is done on the 1.5.1 version page:**
      - the name is decided and recorded in `app_store_listing.md`, with the
        keyword line that matches it (step 8);
      - the live subtitle and keywords were posted on #202 before being
        overwritten (step 9);
      - Romanian, Spanish (Spain), Spanish (Mexico), German and Simplified
        Chinese are added. Name, subtitle, keywords, description, promotional
        text and the What's New above are pasted from each file (step 10).
        Spanish (Mexico) gets the `.es.md` text too, with its offer-led
        promotional text, because it is the Spanish most Latin American
        storefronts show (`.es.md` records why and Apple's sources);
      - the English description is re-pasted;
      - each new translated line has had a native reader, or the file says
        "no native reader" (step 11).
- [ ] **The App Privacy label is published** (ASC config issue #208): Purchases
      → Purchase History, App Functionality, linked to the user, not
      tracking. It has to match `PrivacyInfo.xcprivacy` in this binary.
- [ ] Screenshots (#203) are uploaded, or it is recorded that 1.5.1 goes with
      the old ones.
- [ ] **#250 is deployed** and Railway's log shows `x-forwarded-for carried 2
      hop(s), skipped 1 known proxy hop(s)` for app traffic (RUNBOOK §5.8).
      Until it is, every user behind one CDN edge shares one 60-an-hour
      per-address bucket, and a launch day would trip it.
- [ ] `SANDBOX_ENTITLEMENTS` is `bounded` on Railway while 21 is with App
      Review (RUNBOOK §17: `off` refuses the reviewer's purchase). The same
      section says: no public TestFlight link while it is `bounded` (#208).
      **TODO(owner):** confirmed on ______.
- [ ] **Archive** with Xcode 27 from a fresh pull of `main`, after the last
      1.5.1 PR has merged. First confirm the bump is in the checkout:
      `git merge-base --is-ancestor <bump-sha> HEAD && echo ok`. An archive
      from a stale checkout silently reuses 20, and App Store Connect rejects
      it.
- [ ] Record the archive below, then tag it.
- [ ] Upload 21 **once**. A rejection that needs a new archive bumps to 22
      first. Never upload 21 twice.
- [ ] What's New pasted in all five locales, from the blocks above.
- [ ] App Review notes pasted (below).
- [ ] Phased release on: a seven-day rollout that can be paused.
- [ ] Don't touch `/minbuild`. Set `/minbuild 21` only if a 1.5.0 bug
      requires it, and only once 21 is live (#188).

### Build 21 — the archive

The `chore: 1.5.1, build 21` commit is a lower bound for what 21 contains,
not the build (CLAUDE.md). This record is the build.

- **Archive commit** (`git rev-parse HEAD` in the checkout, at archive time):
  **TODO(owner):** ______
- **Organizer creation date:** **TODO(owner):** ______
- **Tag** it, so that "is fix X in 21" becomes
  `git merge-base --is-ancestor X build-21`:

  ```sh
  git tag build-21 <archive-commit> && git push origin build-21
  ```

### App Review notes (paste into *App Review Information → Notes*)

```
No account is needed. SnapWorth works from the first launch without signing in.

In-app purchases made in the review Sandbox unlock SnapWorth Pro immediately. To see the Pro features, buy from the paywall, for example from "Unlock why this price" on a scan result. You can then open "Why this price", create listing drafts, and use Haul mode.

Haul mode starts from the camera: tap "Haul" above the shutter. It is part of SnapWorth Pro.

The iMessage stickers are in the sticker drawer. In Messages, open the emoji keyboard and go to Stickers to find Tag's pack.
```

---

## After approval (owner)

- [ ] Storefront check: the iTunes lookup on `us`, `de`, `es`, `ro`
      (`&lang=ro_ro`) and `cn`. Record the dated result in
      `app_store_listing.md` (#202 step 12).
- [ ] The day 21 is live, and not before, add Haul mode to the homepage Pro
      cards (#202 step 13).
- [ ] Close #93 if the Haul checks passed.
- [ ] `/minbuild 21` only if a 1.5.0 bug requires it, and only once 21 is
      live.
