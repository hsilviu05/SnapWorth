# What's New — SnapWorth 1.6.2

## Scope

1.6.2 (26) is a patch release that carries one new free feature, "Type it
instead", plus the 2026-10-27 audit fixes, the eBay fee update and the earlier
rating request. It is also the version that takes the store listing rework from
#386 (name, subtitle, keywords), which did not go onto 1.6.1.

**What 25 contains is recorded.** It was archived from `f889ac8`
(`RELEASE-NOTES-1.6.1.md`, *Build 25*). Every app change below is new since
then, from `git log --first-parent f889ac8..main -- ios/`.

### Ships in the binary (users get it only with 26)

| PR | What | Visible? |
|----|------|----------|
| [#403](https://github.com/hsilviu05/SnapWorth/pull/403) | **Type it instead**: a button under the viewfinder opens a sheet to describe an item in words, with "Add a photo instead" beside it. Calls the new `POST /scan/text`. Spends a free user's daily scan; confidence is capped below High; the find is saved without a photo | **Yes** |
| [#401](https://github.com/hsilviu05/SnapWorth/pull/401) | Thrift Flip and the profit ledger use eBay's current fee: 13.6% + $0.40 per order ($0.30 at $10 or less) | Yes, in eBay profit figures |
| [#402](https://github.com/hsilviu05/SnapWorth/pull/402) | The rating request comes after the first confident, revealed estimate instead of the third scan. Other gates unchanged | Rarely (iOS decides) |
| [#404](https://github.com/hsilviu05/SnapWorth/pull/404) | Audit fixes in the app: in-app privacy policy dated October 27, 2026 and covering typed descriptions; `PrivacyInfo.xcprivacy` declares Other User Content; typed-scan refusals worded for a description in all five languages; a paste with a line break no longer submits; "Add a photo instead" waits for the sheet to close; a shared sale still sending when "Delete my shared sales" completes is deleted after it | Partly |
| this PR | `chore: 1.6.2, build 26`, a lower bound for 26, not the build | No |

### Server (must be live before review)

`POST /scan/text` (#403, hardened in #404) is deployed on merge. 1.6.1 never
calls it, but 26 does, and App Review will tap "Type it instead". Confirm it is
serving before submitting (checklist step 1).

### Store listing (version-scoped, pasted onto 1.6.2)

From [#386](https://github.com/hsilviu05/SnapWorth/pull/386), unchanged since
it was checked for 1.6.1: name `SnapWorth: Thrift Resale Value`, subtitle
`What's It Worth? Price Checker`, keywords with no other company's name
(Guideline 2.3.7). The per-locale files carry the matching names and keyword
lines for en-GB, de, es (Spain and Mexico), ro and zh-Hans.

### Not in this build

* Anything merged after the archive commit recorded below.

---

## What's New, all five locales

Paste each block into its locale's What's New field on the 1.6.2 version page.
`es` goes into both Spanish (Spain) and Spanish (Mexico); `en-US` also goes
into English (U.K.). The feature name in each language is the app's own button
label from `ios/Localization/App.json`. None of the four translations has had a
native reader.

Counts are Unicode characters, newlines included. The limit is 4000.

| Locale | Characters |
|---|---|
| `en-US` | 250 |
| `ro` | 302 |
| `es` | 305 |
| `de` | 341 |
| `zh-Hans` | 112 |

### `en-US`

```
• New: Type it instead. No photo? Describe the item (brand, model, size) and get a resale estimate. On the free plan it uses your daily scan.
• Thrift Flip uses eBay's current fees, so profit after fees is closer to what you keep.
• Fixes and polish.
```

### `ro`

```
• Nou: Scrie-l în schimb. N-ai poză? Descrie articolul (marcă, model, mărime) și primești o estimare de revânzare. În planul gratuit folosește scanarea zilei.
• Thrift Flip folosește comisioanele actuale eBay, așa că profitul după comisioane e mai aproape de ce îți rămâne.
• Remedieri și îmbunătățiri.
```

### `es`

```
• Nuevo: Mejor escríbelo. ¿Sin foto? Describe el artículo (marca, modelo, talla) y obtén una estimación de reventa. En el plan gratis usa tu escaneo del día.
• Thrift Flip usa las comisiones actuales de eBay, así que el beneficio tras comisiones se acerca más a lo que te quedas.
• Correcciones y mejoras.
```

### `de`

```
• Neu: Stattdessen eintippen. Kein Foto? Beschreib den Artikel (Marke, Modell, Größe) und erhalte eine Schätzung für den Wiederverkauf. Im Gratis-Plan nutzt das deinen Scan des Tages.
• Thrift Flip rechnet mit den aktuellen eBay-Gebühren, so liegt der Gewinn nach Gebühren näher an dem, was dir bleibt.
• Fehlerbehebungen und Verbesserungen.
```

### `zh-Hans`

```
• 新功能：改为输入文字。没有照片？描述物品（品牌、型号、尺码），即可获得转售估价。免费版会使用当天的扫描次数。
• Thrift Flip 采用 eBay 最新费率，扣除费用后的利润更接近你实际所得。
• 问题修复与改进。
```

### Promotional text (`en-US`, after 1.6.2 is live)

144 of 170 characters. Not tied to a build, but paste it only once 26 is live,
or the store page promises a feature people can't find.

```
No photo? Just type it. Describe any thrift find, like "Levi's 501, size 32", and get an AI resale estimate in seconds. One free scan every day.
```

### TestFlight — What to Test

```
Please test on this build:
• Tap "Type it instead" under the camera, describe an item (e.g. "Levi's 501, size 32") and check the estimate, then that it is saved in My Finds without a photo.
• In that sheet, tap "Add a photo instead": the photo library opens and the photo is valued as a normal scan.
• Thrift Flip with eBay: profit uses 13.6% + $0.40 per order.
```

---

## Testing on a device — required before submitting

Owner checks, on the TestFlight build against production.

### Type it instead ([#403](https://github.com/hsilviu05/SnapWorth/pull/403), [#404](https://github.com/hsilviu05/SnapWorth/pull/404))

- [ ] Free account with today's scan unused: type a description. A result
      appears, confidence at most Medium, and the scan counter is spent.
- [ ] Same account again: "Type it instead" leads to the paywall, and the
      paywall stays up (it is not dropped while the sheet closes).
- [ ] "Add a photo instead": the sheet closes, then the library opens.
- [ ] Paste a description containing a line break: it does not submit on its
      own.
- [ ] Type `size 10½`: the result does not read "101".
- [ ] The typed find opens from My Finds with the placeholder, and offers no
      photo re-read.
- [ ] Phone in German (or any of the four): the button, sheet and a refusal (type
      something that isn't an item, e.g. `my weekend plans`) are in that
      language, and the refusal talks about a description, not a photo.

### eBay fees ([#401](https://github.com/hsilviu05/SnapWorth/pull/401))

- [ ] Thrift Flip, eBay, sale $50: fees $7.20 (13.6% + $0.40). Sale $10:
      fees $1.66 (13.6% + $0.30).

### Rating prompt ([#402](https://github.com/hsilviu05/SnapWorth/pull/402))

TestFlight never shows the real sheet. On a **development build from the same
commit**, fresh install:

- [ ] First High or Medium scan with the price shown and no sheet open: the
      rating request appears about two seconds later.

### Upgrade

- [ ] Install 26 over 25 with finds saved: every find opens, Legit checks and
      ledger entries are intact.

---

## App Store Connect walkthrough (owner)

In order. Nothing here is submitted until the last step, and that one is yours.

### 0. Before touching 1.6.2

- [ ] **1.6.1 is live** (or removed from review). A new version cannot be
      created while 1.6.1 is still waiting for or in review.
- [ ] If a Product Page Optimization test is running, decide whether to end
      it first: the new name and subtitle change the search card mid-test.
- [ ] Still open from 1.6.1: record the live subtitle and first screenshot in
      `app_store_listing.md` (*Subtitle*, TODO), and tag build 25
      (`git tag build-25 f889ac8 && git push origin build-25`).

### 1. Server and archive

- [ ] `/scan/text` is live: `curl -s -o /dev/null -w '%{http_code}\n' -X POST https://api.snapworth.eu/scan/text`
      prints anything but `404`.
- [ ] `git pull` on `main` after this PR merges, then confirm the bump is in
      the checkout: `git merge-base --is-ancestor <this PR's merge commit> HEAD && echo ok`.
      A stale checkout silently re-archives 25, and App Store Connect rejects
      a build number already used.
- [ ] Archive with **Xcode 27** (`.xcode-version`), scheme **SnapWorth** (not
      *Mock scans*), Release configuration. Organizer shows **1.6.2 (26)**.
- [ ] Record the commit and Organizer's creation date under *Build 26* below,
      then tag it.
- [ ] Distribute → App Store Connect → Upload, **once**. If a rejection needs
      a new archive, bump to 27 first.
- [ ] Answer export compliance the same way as for 25.

### 2. TestFlight validation

- [ ] 26 appears with no *Missing Compliance* or warning badge.
- [ ] Paste *What to Test* (above). Internal testers or a small invite-only
      group. **No public TestFlight link** while `SANDBOX_ENTITLEMENTS` is
      `bounded` (RUNBOOK §17).
- [ ] Run the device checks above and tick them.

### 3. Create the 1.6.2 version

- [ ] *+ Version or Platform* → iOS → `1.6.2`. Build: select **26**.

### 4. Listing, every locale

Run `python3 tools/check_listing_limits.py` first (0 failures on this branch).

- [ ] **English (U.S.)**: name `SnapWorth: Thrift Resale Value`, subtitle
      `What's It Worth? Price Checker`, keywords from `app_store_listing.md`.
- [ ] The other locales' names and keyword lines from their files
      (`app_store_listing.<locale>.md`), adding English (U.K.) if 1.6.1 did
      not. Part 5 of the 1.6.1 launch sheet has them as paste blocks; only
      What's New differs.
- [ ] The new name reverses the 2026-09-28 call to keep "SnapWorth: Resell &
      Flip". Keep or revert before pasting.

### 5. App Privacy and privacy manifest

- [ ] *App Privacy* → edit → add **Other User Content**: used for **App
      Functionality**, **not linked** to the user, **not used for tracking**.
      This is the typed description. `PrivacyInfo.xcprivacy` already declares
      it, and the label and the manifest must agree.
- [ ] Everything already declared stays as for 25 (Photos or Videos, and the
      rest unchanged). Publish the label change before submitting.
- [ ] No new permission, so no new purpose string. Camera and photo library
      strings are unchanged.
- [ ] The live privacy policy at `https://api.snapworth.eu/privacy` mentions
      typed descriptions, as the in-app one does.

### 6. What's New and review notes

- [ ] What's New in all five locales, plus en-GB (blocks above).
- [ ] App Review notes: copy 1.6.1's, and add one line: "New in 1.6.2: tap
      *Type it instead* under the camera and describe an item (e.g. Levi's
      501, size 32) to get an estimate without a photo."

### 7. Release options

- [ ] *Phased Release for Automatic Updates*: **on**. Seven days, pausable if
      Organizer shows a crash; manual updates are not held back.
- [ ] *Version release*: manual if you want to pick the day the new name goes
      live, otherwise automatic.

### 8. Submit (owner only)

- [ ] *Add for Review* → *Submit to App Review*. Claude does not do this step.

### Build 26 — the archive

The `1.6.2, build 26` commit in this PR is a lower bound, not the build
(CLAUDE.md). Fill this in at archive time.

- **Organizer creation date:** 2026-10-28, 23:53 (owner, read from Organizer
  on 2026-10-30).
- **Archive commit:** not recorded. The last merge before that time was
  `5fa8cea` (#405, 23:46), and nothing between the bump `296a89a` and
  `5fa8cea` touches `ios/`, so the app in 26 is `296a89a`'s whichever of
  those was checked out. The next app change, #414, merged on 2026-10-29.

---

## After approval (owner)

- [ ] Paste the promotional text above (en-US).
- [ ] A week after release: Acquisition → Sources → Search terms, and the
      impression-to-tap rate, against the 1.6.1 values.
- [ ] Analytics: typed scans as a share of all scans.
- [ ] Organizer → Crashes on 1.6.2.
