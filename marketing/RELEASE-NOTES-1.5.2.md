# What's New — SnapWorth 1.5.2

## Scope

1.5.1 (21) is live: the iTunes lookup gives `currentVersionReleaseDate`
2026-10-02 23:05 UTC. 1.5.2 (22) is a patch release in the *Measured
accuracy* milestone. It carries the app halves of the evidence work: the
export the gold set is built from (#214), per-card attribution for shared
images (#221) and the default-plan lever for the trial experiment (#220).
It is also the next version that can carry the four localized store pages,
which did not reach 1.5.1 (#269 §1). Target submit ~Thu 2026-11-05
(roadmap #233). It can go earlier if the gold-set work doesn't need to
wait for it.

**What 21 contains is settled for the app, though its archive is not
recorded.** No file under `ios/` changed between the last 1.5.1 PR
([#262](https://github.com/hsilviu05/SnapWorth/pull/262), `8bc8b01`,
2026-09-28) and the first 1.5.2 one
([#200](https://github.com/hsilviu05/SnapWorth/pull/200), 2026-10-05):
`git diff 8bc8b01 b359888 -- ios` is empty. So however late 21 was archived
before submission, its app code is `8bc8b01`'s, and every iOS change below
is new in 22. 21's archive commit and Organizer date are still owed
(#269 §3), so that `build-21` can be tagged.

### Ships in the binary (users get it only with 22)

| PR | What | Visible? |
|----|------|----------|
| [#270](https://github.com/hsilviu05/SnapWorth/pull/270) | My Flips → ⋯ → **Export photos and estimates (ZIP)**, Pro: each sold flip's photo and a JSON record of the scan-time estimate beside the sale (#214). New scans keep the scan prompt's version | **Yes**, Pro |
| [#271](https://github.com/hsilviu05/SnapWorth/pull/271) | The result, month, haul and guess cards each encode `snapworth.eu/get/<card>`, which redirects to that card's App Store campaign (#221) | The QR codes differ; nothing to read |
| [#273](https://github.com/hsilviu05/SnapWorth/pull/273) | The paywall preselects the plan `/lever plan` sets; unset is yearly, as today. `paywall_viewed` and `purchase_*` carry `default_plan` (#220) | No, while the lever is unset |
| [#200](https://github.com/hsilviu05/SnapWorth/pull/200) | The rare-find easter egg, **off in Release** (`Config.rareFindEasterEggEnabled` is `#if DEBUG`) | No. See *The easter egg* below |
| [#274](https://github.com/hsilviu05/SnapWorth/pull/274) | `chore: 1.5.2, build 22`, a lower bound for 22, not the build | No |

### Server and website (deployed on merge; already serving 21)

| PR | What |
|----|------|
| [#263](https://github.com/hsilviu05/SnapWorth/pull/263)–[#267](https://github.com/hsilviu05/SnapWorth/pull/267) | Dependency bumps: uvicorn, pyjwt, pyright, google-genai, starlette |
| [#268](https://github.com/hsilviu05/SnapWorth/pull/268) | Homepage lists Haul mode; the growth log says the baseline week was mixed |
| [#242](https://github.com/hsilviu05/SnapWorth/pull/242) | Every App Store link on the site is a campaign link (`pt=129137132`, #204) |
| [#271](https://github.com/hsilviu05/SnapWorth/pull/271) | The four `/get/*` redirects; `check_live.py` asks production for each |
| [#273](https://github.com/hsilviu05/SnapWorth/pull/273) | `/lever plan` and `paywall_default_plan` on the token; the site's trial copy names no length |

### Repository only

| PR | What |
|----|------|
| [#272](https://github.com/hsilviu05/SnapWorth/pull/272) | `python -m eval.intake`: the ZIP or a CSV → draft gold records; photos and evidence links kept out of git (#213) |

### Not in this build

* Anything merged after this file and before the archive is in 22 too. Add
  it to the tables above when the archive commit is recorded below.

### The easter egg

#200 is merged and dark. To ship it in 22, before the archive:

1. A one-line PR replaces the `#if DEBUG` in `Config.rareFindEasterEggEnabled`
   with `true`.
2. A native reader checks the ro, es, de and zh-Hans jokes.
3. Decide the brand line (`RareFind.brandName` is `nil`, so there's no brand
   line).
4. Add the *Easter egg* paragraph below to the App Review notes, with the
   reference photo `ios/SnapWorthTests/rare-find-reference-shirt.png`
   attached. Guideline 2.3.1 covers hidden features. A reviewer can't find
   this one by chance, so it has to be described.

If it stays dark, leave the paragraph out. Then nothing here mentions it.

---

## What's New, all five locales

Paste each block into its locale's What's New field on the 1.5.2 version
page. They are deliberately short, because only one change is visible to a
user. They don't name a trial length, don't use an accuracy word, and don't
say "unlimited". The feature names come from `ios/Localization/App.json`
("My Flips" and the menu item), so the store and the app use the same words.
None of the four translations has had a native reader yet.

Counts are Unicode characters, newlines included. App Store Connect's limit
is 4000.

| Locale | Characters |
|---|---|
| `en-US` | 195 |
| `ro` | 212 |
| `es` | 198 |
| `de` | 238 |
| `zh-Hans` | 77 |

**Line that depends on a device check.** The export line depends on the
export check below. If that check fails, the release has nothing user-facing
to announce. Then use the last line alone in every locale.

### `en-US`

```
New in Pro: export your sold flips. In My Flips, tap ⋯ → Export photos and estimates (ZIP) to get each sale with its photo and the estimate you saw when you scanned it.

• Fixes and improvements.
```

### `ro`

```
Nou în Pro: exportă-ți vânzările. În Flipuri, atinge ⋯ → Exportă fotografii și estimări (ZIP) și primești fiecare vânzare cu fotografia ei și estimarea pe care ai văzut-o la scanare.

• Remedieri și îmbunătățiri.
```

### `es`

```
Novedad en Pro: exporta tus ventas. En Mis reventas, toca ⋯ → Exportar fotos y estimaciones (ZIP) y tendrás cada venta con su foto y la estimación que viste al escanearla.

• Correcciones y mejoras.
```

### `de`

```
Neu in Pro: exportier deine Verkäufe. Tippe in Meine Flips auf ⋯ → Fotos und Schätzungen exportieren (ZIP) und du bekommst jeden Verkauf mit Foto und der Schätzung, die du beim Scannen gesehen hast.

• Fehlerbehebungen und Verbesserungen.
```

### `zh-Hans`

```
Pro 新增：导出已卖出的转卖记录。在“我的转卖”中点按 ⋯ → 导出照片和估价（ZIP），每笔售出都附带照片和扫描时看到的估价。

• 问题修复与改进。
```

### What these leave out

The per-card QR links (nothing a user reads), the default-plan lever
(invisible while unset), `prompt_version`, the easter egg (dark unless
shipped as above), and every server, website and repository change.

### TestFlight — What to Test

```
Please test on this build:
• Export (Pro): mark two or three items as sold in My Flips, then tap ⋯ → Export photos and estimates (ZIP). Send it to a Mac and open it: each sale should have its photo and a .json file.
• Share cards: share a result, a month card, a haul summary and a guess reveal, including as a story. Scan each QR code with another phone's Camera: it should open SnapWorth on the App Store.
• Paywall: open it from any Pro feature. Yearly should be preselected, as before.
```

---

## Testing on a device — required before submitting

All of these are owner checks, on the TestFlight build against production.
**Remove and re-add every widget first** (CLAUDE.md). Tick each check, or
open an issue for the failure that names the build its fix needs.

### Export (#214, [#270](https://github.com/hsilviu05/SnapWorth/pull/270))

- [ ] With sold flips, ⋯ → *Export photos and estimates (ZIP)* shows
      "Preparing your export…", then the share sheet. AirDrop it to a Mac:
      it unzips to one `SnapWorth-Flips-<date>/` folder, a `.jpg` and a
      `.json` per sale, and each photo is the right item.
- [ ] A flip scanned on 22 exports `"prompt_version": "v2"` or `"v2.1"`,
      whatever Railway's `SCAN_PROMPT_VERSION` is. One scanned on 21 or
      earlier exports `"unknown"`.
- [ ] Mark an item sold and export straight away: it is in the ZIP.
- [ ] As a free user, the menu item opens the paywall. With nothing sold,
      it is greyed out.
- [ ] The intake reads it: `cd backend && python -m eval.intake --dry-run zip
      <the zip>` lists one draft per sale. This is the gold-set start
      (#213), so a real run can follow straight away.

### Share-card QR codes (#221, [#271](https://github.com/hsilviu05/SnapWorth/pull/271))

- [ ] Share each card at real size: a result (also as the 9:16 story), the
      My Flips month card, a Haul summary and a Guess reveal. Each QR scans
      from a second iPhone's Camera and opens SnapWorth's App Store page.
- [ ] `python3 website/seo/check_live.py` reports 0 problems on the day
      (the four redirects are live; checked 2026-10-05).

### Default plan (#220, [#273](https://github.com/hsilviu05/SnapWorth/pull/273))

- [ ] With `/lever plan` unset, the paywall preselects **yearly**.
- [ ] Optional, before release only, because until 22 is live the lever
      reaches TestFlight devices alone: `/lever plan monthly yes`, wait for
      a new token (relaunch after an hour, or delete and reinstall), and
      the paywall preselects **monthly**. Then `/lever plan default yes`.
      **Set it back before releasing.** Arm 0 needs it unset.
- [ ] Tapping the other card after the default applies keeps your tap.

### Carried from 1.5.1 (#269 §4)

The Haul, Sandbox-purchase and paid-path checks in
`RELEASE-NOTES-1.5.1.md` were never recorded. Run them on 22. The code they
test has not changed since 21.

---

## Pre-submit checklist (owner)

- [ ] **Localized store pages on the 1.5.2 version page (#269 §1).** First
      record the live English subtitle and keywords in #269. Then add
      Romanian, Spanish (Spain), Spanish (Mexico), German and Simplified
      Chinese, and paste the name, subtitle, keywords, description and
      promotional text from each `app_store_listing.<locale>.md`, plus the
      What's New above. Re-paste the English description. Have each new
      translated line read by a native speaker, or record "no native reader".
- [ ] `/lever plan` is **unset**: `/lever plan` shows "yearly (app default)".
- [ ] `SANDBOX_ENTITLEMENTS` is `bounded` while 22 is with App Review
      (RUNBOOK §17), and there is no public TestFlight link.
- [ ] The easter-egg decision is made (above), and if it ships, its PR is
      merged before the archive.
- [ ] **Archive** with Xcode 27 from a fresh pull of `main`, after the last
      1.5.2 PR has merged. First confirm the bump is in the checkout:
      `git merge-base --is-ancestor <#274's merge commit> HEAD && echo ok`.
      An archive from a stale checkout silently reuses 21, and App Store
      Connect rejects it.
- [ ] Record the archive below, then tag it.
- [ ] Upload 22 **once**. A rejection that needs a new archive bumps to 23
      first.
- [ ] What's New pasted in all five locales, from the blocks above.
- [ ] App Review notes pasted (below).
- [ ] Phased release on.

### Build 22 — the archive

The `chore: 1.5.2, build 22` commit is a lower bound for what 22 contains,
not the build (CLAUDE.md). This record is the build.

- **Archive commit** (`git rev-parse HEAD` in the checkout, at archive time):
  **TODO(owner):** ______
- **Organizer creation date:** **TODO(owner):** ______
- **Tag** it:

  ```sh
  git tag build-22 <archive-commit> && git push origin build-22
  ```

### App Review notes (paste into *App Review Information → Notes*)

```
No account is needed. SnapWorth works from the first launch without signing in.

In-app purchases made in the review Sandbox unlock SnapWorth Pro immediately. To see the Pro features, buy from the paywall, for example from "Unlock why this price" on a scan result. You can then open "Why this price", create listing drafts, use Haul mode (tap "Haul" above the shutter), and export sold items from My Flips (mark an item as sold, then tap ⋯ → Export photos and estimates).

The iMessage stickers are in the sticker drawer. In Messages, open the emoji keyboard and go to Stickers to find Tag's pack.
```

Only if the easter egg ships, add:

```
Easter egg: scanning one particular printed shirt (photo attached) plays a short, clearly labelled joke appraisal ("EASTER EGG · Just for fun — not a real valuation") before the real estimate, which is shown below it unchanged. Nothing from the joke is saved, priced or sold. It triggers only on that shirt's printed text, read on the device.
```

---

## After approval (owner)

- [ ] Storefront check: the iTunes lookup on `us`, `de`, `es`, `ro`
      (`&lang=ro_ro`) and `cn` returns the localized description. Record the
      dated result in `app_store_listing.md` (#269 §1).
- [ ] A week after release: ASC → Campaigns shows `share_*` rows, or #221
      records zero.
- [ ] Arm 0 of #220 runs untouched. `/lever plan monthly` waits for Arm 2.
- [ ] `/minbuild 22` only if a 21 bug requires it, and only once 22 is live.
