# SnapWorth — screenshot handoff, 1.5.1

The production sheet for #203: what to capture, on what, in which state, and
how it becomes the uploaded set. The reasons, and the source line behind every
fact, are in `SCREENSHOT-SPEC.md` §0.

**Owner steps:** the captures (§3–§5), compositing (§6) and the upload (§7),
all before 1.5.1 is submitted. Screenshots belong to the version: once 1.5.1
is in review they cannot change until 1.5.2.

---

## 1. The set

Eight frames at iPhone 6.9″, in gallery order. Column *Capture* is the file
`build_screenshots.py` reads; *Output* is what it writes to
`marketing/screenshots/v3/` and what gets uploaded.

| # | Frame | Tier | Headline | Subhead | Capture | Output |
|---|---|---|---|---|---|---|
| 01 | Thrift Flip | Free | Profit, after *fees.* | Shop price in, fees out: a verdict before you buy. Free with your daily scan. | `raw_01_thrift-flip.png` | `en_69_01_profit-after-fees.png` |
| 02 | Haul mode | **Pro** | Scan a whole *haul.* | With Pro: snap item after item while each is valued, with a running total. | `raw_02_haul.png` | `en_69_02_scan-a-whole-haul.png` |
| 03 | Result | Free | Know before you *buy.* | An AI resale estimate from one photo, with its confidence level. | `raw_03_result.png` | `en_69_03_know-before-you-buy.png` |
| 04 | Snap → Sell | **Pro** | Your listing, already *written.* | With Pro: a title and description tailored to where you sell, on nine marketplaces. | `raw_04_snap-sell.png` | `en_69_04_your-listing-already-written.png` |
| 05 | My Flips | Free | Every flip, *tracked.* | What you paid, what it sold for, and what you made after fees. | `raw_05_my-flips.png` | `en_69_05_every-flip-tracked.png` |
| 06 | Widgets | Free | Your finds, at a *glance.* | Home Screen and Lock Screen widgets: total value, recent finds, one-tap scan. | `raw_06_widgets.png` | `en_69_06_your-finds-at-a-glance.png` |
| 07 | Privacy | Free | Your photos stay *yours.* | Never stored on our servers. No account. Your scan history stays on your device. | `raw_07_my-finds.png` | `en_69_07_your-photos-stay-yours.png` |
| 08 | Plans | Free | One *free* scan, every day. | Then Pro, when you want more. Cancel anytime. | `raw_08_plans.png` | `en_69_08_one-free-scan-every-day.png` |

The accent word is in *italics*. Frames 02 and 04 also get a **PRO** tag above
the headline, in the app's own badge colours (Guideline 2.3.2). The captions
live in `build_screenshots.py`; change them there, and `--check` holds them to
the limits (five words, fifteen words, two lines each) and to the listing's
do-not-claim list.

**Tier** is the tier the device must be on *when that frame is captured*.
It is not a detail: a Pro screen under a free caption is a 2.3.2 problem, and
a Pro capture of a free screen shows Pro-only things (the 0–100 number in
*Why this price*, *All-time profit*, "∞ Unlimited scans" on the widget).

---

## 2. Device, build and canvas

| | Required | Why |
|---|---|---|
| **Build** | **1.5.1 (21), installed from TestFlight** — the binary that is submitted | A TestFlight build is Release: no `#if DEBUG` code is compiled in, and it cannot receive `-mock-scans`, so every result is production output from `api.snapworth.eu` (`Config.swift:6-25`). Not an Xcode run, not a Debug build |
| **Device** | **A physical 6.9″ iPhone** — iPhone 16 Pro Max or 17 Pro Max (or newer 6.9″); native screenshots are 1320 × 2868 | The Simulator cannot use App Attest, so every Simulator scan against production fails (`Config.swift:12-19`). `--check` refuses any capture that is not 1320 × 2868 |
| **Backend** | Production, as is. A TestFlight purchase is Sandbox, which production honours on bounded terms: Pro on one device, for up to 24 hours per sync (`backend/entitlements.py:125-191`) | So the Pro frames work on the TestFlight build without a real subscription |
| **Devices** | **iPhone only** (`TARGETED_DEVICE_FAMILY = 1`, `project.pbxproj:951`, `:983`) | App Store Connect asks for the iPhone set alone |
| **Other iPhone sizes** | None. Apple scales the 6.9″ set down | — |

**Check the build contains what the frames show.** Thrift Flip's seeded
*Expected resale* is R (#238, merged 2026-09-27 19:59 EEST) and the widgets
are T (#240, merged 20:21 the same day). The `chore: 1.5.1, build 21` commit is
not proof either way (see `CLAUDE.md`): open Xcode's Organizer and confirm the
build-21 archive was created **after** both merges. If it was not, the frames
must wait for an archive that is.

**Canvas** 1320 × 2868; device 940 px wide, baseline y = 2660; PRO tag at
y = 96, headline from y = 200, subhead from y = 500. All in
`build_screenshots.py` — change geometry there, in one place.

---

## 3. The shot list

Capture with the side button + volume up. AirDrop each PNG to the Mac as-is
(no crop, no edit, no markup) and rename it to the *Capture* name in §1.

### 01 — Thrift Flip · free

- **Screen:** Scan tab → **Flip** (bottom right of the shutter) → *Scan item*
  → photograph the sample → enter the shop price (or *Scan tag* on the price
  tag) → *Done* on the keyboard.
- **State:** the verdict card reads **Worth flipping**, in sage. **eBay**
  selected (the default). *Expected resale* **as the app seeded it** — do not
  type over it; that field is the model's likely price, and a hand-typed
  figure would be a number the app never gave. *Shipping* empty. Keyboard
  dismissed. The whole card in view: item header, *Where you'd sell*, *Shop
  price*, *Expected resale*, the verdict, *Net profit*, *ROI* and the
  Resale / − Fees / − Paid rows. No *Save to My Flips* tap needed.
- **Sample item:** a real thrift find with a recognisable brand and its real
  shelf price — e.g. a branded fleece, denim jacket or pair of trainers
  bought for $8–$15. If the verdict comes back *Skip it*, that is the honest
  answer for that item: choose another, do not change the numbers.
- **Spends** the day's free scan. The find is saved to My Finds, which frame
  03 uses.

### 02 — Haul mode · **Pro**

- **Screen:** Scan tab → the **Haul** pill above the shutter → Haul's camera.
- **State:** 6–8 items photographed, **every cell valued** (a price under each
  thumbnail, no spinner beside *Estimated total*, no failed cell), camera
  pointed at the pile, **no banner** (no "You've hit the scan limit.", no
  hold). The top bar reads *Estimated total*, the total, *N items*, *Finish*.
  Do not tap Finish.
- **Sample items:** a real, well-lit pile on a table — varied categories
  (trainers, denim, a bag, a jacket, a camera, a record…). Eight at most:
  Pro scans share an hourly cap, and a pause banner in frame is a reshoot.
- **Tier:** Pro (TestFlight Sandbox subscription, §5).
- **#187's DEBUG synthetic camera** may stand in for the device camera **only
  if nothing DEBUG shows**. In practice it cannot: it exists only in a DEBUG
  build on the Simulator with `-mock-scans` (`CameraManager.swift:266-279`),
  so every thumbnail is a numbered colour card ("#1", "#2", …,
  `:326-352`) and every value is a canned mock result — not the submitted
  build, not production output. Use it to rehearse the framing; capture the
  frame on the device.
- **If Haul is not in 1.5.1** (#93's device checks, or P2 not deployed so a
  haul pauses): skip this frame (§6) and add it in 1.5.2.

### 03 — Result · free

- **Screen:** My Finds → the frame-01 find (or a fresh scan from the Scan tab
  on another day, with *Reveal* tapped and no guess typed). The sheet at the
  top, not scrolled.
- **State:** hero photo, item name, the **brand** and **condition grade**
  chips (e.g. *Good*), *Estimated Resale Value*, the range, the confidence
  band, and **AI estimate** beside it. Below: *Why this price* **locked**, with
  its PRO badge. No guess verdict line.
- **Check before keeping it:** the caption beside the band reads **AI
  estimate** — if it reads "Based on recent sales", use another find. The
  locked card's first line is blurred by the app; confirm no "out of 100"
  is legible at full size.
- **Sample item:** the frame-01 find — one item, from verdict to estimate.
  Prefer a photo on a textured or darker ground: a white background leaves
  the top third of the sheet empty.
- **Tier:** free. On Pro, *Why this price* opens and prints "NN / 100
  confidence" under the ladder — never capture this frame on Pro.

### 04 — Snap → Sell · **Pro**

- **Screen:** My Finds → a find from the frame-02 haul → scroll to *Snap →
  Sell* → **eBay** selected → *Generate eBay listing* → wait for the listing.
- **State:** scrolled so the card fills the screen from the *Snap → Sell*
  header to *Open eBay*: chips, title, description, **Ask** and **Floor**,
  Copy / Share, *Open eBay*, and "SnapWorth writes it — you paste & post. We
  never post for you." **Nothing of *Why this price* in frame** — on Pro it
  holds the 0–100 number.
- **Sample item:** a haul find with a clear brand, so the title reads well.
- **Tier:** Pro. The in-app PRO badge does not render for a subscriber; the
  caption carries Pro.

### 05 — My Flips · free

- **Screen:** the **My Flips** tab, at the top.
- **State:** *Profit this month* with a positive figure, the four stat cards
  (Invested, Avg ROI, Best flip, Not sold yet), *Last 6 months* with more
  than one bar if the ledger has them, the first rows below.
- **Sample data:** the owner's own ledger — real flips, marked Sold, with
  what was paid and what they sold for. Do not enter sales that did not
  happen.
- **Tier:** free. On Pro the header is *All-time profit*, a Pro feature.

### 06 — Widgets · free

- **Screen:** an otherwise empty Home Screen page, light appearance, a plain
  light wallpaper, dock left as it is (Apple's own apps only).
- **State:** *Haul Value* (medium) at the top, *Recent finds* (medium) below,
  *Scans left* and *Quick Scan* (small) side by side. *Scans left* shows
  **1 · free scan left today**.
- **Before capturing:** after installing build 21, **remove and re-add every
  SnapWorth widget** — iOS keeps drawing an extension's old snapshot across an
  update (`CLAUDE.md`). Open the app once so the widgets have today's data.
- **Order:** capture this **before** frame 01 on the same day: after the
  scan, *Scans left* reads 0 · "Back tomorrow, or go Pro".
- **Tier:** free. On Pro, *Scans left* reads "∞ Unlimited scans", which
  breaks the fair-use rule in a picture.

### 07 — Privacy (My Finds) · free

- **Screen:** the **My Finds** tab.
- **State:** scrolled so the two-column grid of the owner's own finds fills
  the screen; *Trending at the thrift* above it out of frame. No search text,
  not in Edit mode.
- **Sample data:** eight or more real finds with varied, well-lit photos.
- **Tier:** free (Pro adds value history to the header).

### 08 — Plans

- **Screen:** **Settings → Upgrade** (the paywall with no trigger pitch).
  Wait for the close button to appear, and for both plan cards to show
  prices (no grey placeholders).
- **State:** headline **Unlock SnapWorth Pro**, "$39.99/year. Cancel
  anytime.", Yearly selected: **$39.99 · $0.77 per week · SAVE 33%**, Monthly
  **$4.99 · Flexible, cancel anytime**, the first benefits in view.
- **No trial line.** The paywall names a trial only for an account StoreKit
  says can take one, and the listing names no trial length. Capture from an
  Apple Account that has **already used** SnapWorth's trial. If the headline
  reads "Try SnapWorth free for …", it is the wrong account — capture later
  (§5), not with the trial in frame.
- **US storefront:** the prices must be the US ones above — the same figures
  the en-US description names. Another currency means another storefront.
- **Tier:** free (a subscriber has no Upgrade row).

---

## 4. Device setup — once, before any frame

| Setting | Value |
|---|---|
| Language / Region | English (US) / United States |
| Appearance | **Light** (Settings → Display & Brightness) |
| Text Size, Bold Text | Default, off |
| Display Zoom | Default |
| SnapWorth → Settings → *Guess before the estimate* | Either — frame 03 comes from My Finds, which never covers the price; for a fresh scan, tap *Reveal* without typing a guess |
| Thrift run | **Off** — its Live Activity would sit in the Dynamic Island |

**A clean status bar on a device.** `xcrun simctl status_bar … override`
works only on the Simulator, and these captures cannot come from it. On the
iPhone:

- Battery **100%**, **unplugged** (no charging bolt), **Low Power Mode off**.
- **Wi-Fi** connected at full strength; cellular on if the phone has it.
- **Focus off**, no call, timer, music, screen recording, hotspot or
  navigation running — each puts something in the status bar or the island.
- **9:41** (optional, the convention): Settings → General → Date & Time →
  *Set Automatically* off → 9:41, **same date**. Crossing midnight would move
  the free-scan day. Turn *Set Automatically* back on afterwards.
- iOS's green camera-in-use dot may appear on frame 02: it is system UI;
  leave the capture as it is rather than editing it.

**Nothing DEBUG, no mocks, in any frame.** No DEBUG build, no `-mock-scans`,
no Simulator, no canned results, no synthetic photos, no mock paywall.
The TestFlight build of 1.5.1 (21) makes this automatic.

---

## 5. Capture order

The tiers force an order.

1. **Free, same morning:** 06 Widgets (while *Scans left* is 1) → 01 Thrift
   Flip (spends the scan) → 03 Result (the same find, from My Finds) → 07 My
   Finds → 05 My Flips.
2. **Subscribe** in the TestFlight build: Settings → Upgrade → Yearly. This is
   Sandbox — no charge — and it uses up the trial for that account, which is
   what frame 08 needs later. Wait until the Scan tab stops showing the
   free-scan counter.
3. **Pro:** 02 Haul → 04 Snap → Sell (a find from that haul).
4. **Free again:** cancel the TestFlight subscription and wait for it to
   lapse — until the Scan tab shows the free-scan counter again (Sandbox
   renewals are accelerated; the server's bounded Sandbox Pro lasts at most
   24 hours after the last sync). Then 08 Plans from Settings → Upgrade, with
   no trial line.

If a frame needs retaking on another day, retake it on the tier its row in §1
names.

---

## 6. Compositing

```sh
# the eight captures, named as in §1, go here (git-ignored):
mkdir -p marketing/screenshots/v3/captures

python3 marketing/build_screenshots.py --check   # validates; writes nothing
python3 marketing/build_screenshots.py           # writes marketing/screenshots/v3/en_69_*.png
```

`--check` fails, naming the file, for a capture that is missing, unreadable
or not 1320 × 2868, and for a caption over its limits, missing "Pro" on a Pro
frame, or saying anything on the do-not-claim list. The build runs the same
checks first and writes nothing unless every one passes — no partial set.

**Haul fallback** (#203, *Notes*): `--skip 02` builds and checks the other
seven. Upload them in the same order; the numbering gap is only in the file
names.

Then look at the whole set at **1/6 scale** — that is the size in the search
results. A headline that does not read there fails. Commit the eight
composites in `marketing/screenshots/v3/`; the captures stay out of git.

---

## 7. Upload — owner, before Submit

1. App Store Connect → SnapWorth → **1.5.1** → **English (U.S.)** → *iPhone
   6.9″ Display*: delete the four live panels, upload the eight composites
   **in 01 → 08 order**.
2. The localizations #202 adds (Romanian, German, Simplified Chinese, and
   Spanish for Spain and Mexico) get no screenshots of their own in 1.5.1,
   so App Store Connect shows them this English set. A German set captured in German can follow
   in a later version (`SCREENSHOT-SPEC.md` §5).
3. **iMessage App** section: if App Store Connect blocks the submission for
   iMessage screenshots, see `RELEASE-NOTES-1.5.0.md:48-56` — the sticker
   drawer is a separate upload, not one of these eight.
4. After upload, point `README.md`'s screenshot row at `marketing/screenshots/v3/`
   (it shows `store_1..4` today).
5. Two and four weeks after release, compare tap-through with the baseline
   week in `docs/GROWTH-DASHBOARD.md`.

---

## 8. Gate — every frame, before upload

- [ ] Captured on the 1.5.1 (21) TestFlight build, on a 6.9″ iPhone, against
      production — nothing DEBUG, no mock, no Simulator
- [ ] Captured on the tier its row in §1 names
- [ ] 02 and 04 carry the PRO tag and say Pro
- [ ] No "sold listings", comps, market data, "Based on recent sales"
- [ ] No numeric confidence score anywhere: no "/ 100", no "out of 100"
- [ ] The result reads **AI estimate** and a confidence **band**
- [ ] No trial line or trial length on 08; US prices
- [ ] "Unlimited" appears nowhere without fair use (the Pro *Scans left*
      widget is why 06 is free)
- [ ] Marketplace names as plain chips, no logos
- [ ] Status bar clean; no banner, spinner or keyboard left in frame
- [ ] `python3 marketing/build_screenshots.py --check` passes
- [ ] The set reads at 1/6 scale

### Export (what the script writes)

| Setting | Value |
|---|---|
| Size | 1320 × 2868 |
| Format | PNG |
| Colour | sRGB, 8-bit, no alpha. The captures are Display P3; the script converts them, so the app's own colours match the caption's |
| Naming | `{locale}_{device}_{NN}_{slug}.png` → `en_69_01_profit-after-fees.png` |
