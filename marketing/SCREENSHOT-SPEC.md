# SnapWorth — App Store screenshot system

**Version:** 2.0 · 2026-09-27 · for **1.5.1 (build 21)**, #203
**Supersedes:** 1.0 (2026-07-28), and the live four-panel set uploaded
2026-09-01 (#35, `screenshots/store_1..4`)
**Production sheet:** `SCREENSHOT-HANDOFF.md` — the shot list, the capture
rules and the upload steps. This file is the why.

Every element named below exists in the 1.5.1 app, and each fact carries the
line it was read from. Nothing is aspirational. When the app changes, re-read
the line, not this file.

---

## 0. Ground truth — 1.5.1 as `origin/main` builds it

Read from the code on 2026-09-27, after Haul (#187), Q (#237), R (#238), S
(#239) and T (#240) merged. Design must not exceed it.

### The facts the old spec had wrong

| Fact | 1.5.1 | Source |
|---|---|---|
| Free tier | **One free scan a day.** A Thrift Flip scan spends it too. The first-day allowance is a server lever and off | `ios/SnapWorth/Config.swift:124`; `backend/quota.py:44`, `:60`; `ThriftFlipViewModel.swift:58` |
| Marketplaces | **Nine**, in the app's chip order: eBay · Poshmark · Mercari · Depop · Facebook · Vinted · OLX · 闲鱼 (Xianyu) · Kleinanzeigen. Both pickers (Thrift Flip, Snap → Sell) list all nine | `Services/ListingService.swift:11-21`, names `:27-38`; `ThriftFlipView.swift:186`; `ResultView.swift:1449` |
| The Pro price ladder | Four price points, floor to ceiling: **Floor · Quick sale · Expected · Best case**, Expected in sage. Pro-only, inside *Why this price*; a free result shows that card blurred behind "Unlock why this price" | `Services/ScanAPIClient.swift:363-378`; `ResultView.swift:1080-1099`, `:1813-1836`, `:1323` |
| The number behind the band | The same Pro panel prints the 0–100 score, e.g. **"72 / 100 confidence"**. That is why no frame shows the Pro panel | `ResultView.swift:1843-1848` |
| Pro prices | **$4.99 a month, $39.99 a year** (US). The paywall prints StoreKit's own price in the storefront's currency, adds a per-week figure ($0.77) and **SAVE 33%** (rounded down) to the yearly card. A trial is shown only when StoreKit says *this* account can take one | `SnapWorth.storekit:32`, `:60`; `PaywallView.swift:97-139`; `StoreKitPurchaseService.swift:198-219`, `:347-355` |
| Trial length | An App Store Connect setting. The local StoreKit file has 3 days; the listing names no length (#245) and no frame does either | `SnapWorth.storekit:64-69`; `app_store_listing.md`, *Paste with 1.5.1* |
| Devices | **iPhone only.** The app target's device family is iPhone, so App Store Connect asks for iPhone screenshots and nothing else. (The widget extension's `"1,2"` does not change the app's family.) | `project.pbxproj:951`, `:983` (app, `TARGETED_DEVICE_FAMILY = 1`) |

### What 1.5.1 renders, screen by screen

| Screen | Renders | Tier | Source |
|---|---|---|---|
| **Thrift Flip** | Item header with *Resale $X–$Y* · *Where you'd sell* (nine chips, eBay first and selected) · *Shop price* + *Scan tag* · *Expected resale*, seeded from the model's likely price at the graded condition (R, #238) · *Shipping (optional)* · verdict **Worth flipping** (sage) or **Skip it** · "Resell at $X → +$Y profit after fees." · *Net profit* · *ROI* · Resale / − Fees / − Paid · "Resale is an AI estimate and fees are approximate — treat the verdict as a guide, not a guarantee." | **Free** (a scan from the daily allowance) | `ThriftFlipView.swift:9-15`, `:154-264`, `:284-359`, `:406`; `ThriftFlipViewModel.swift:111-112` |
| **Haul mode** (#93) | Live camera · top bar: *Estimated total* and the running figure, *N items*, **Finish** · strip of thumbnails, each with its value · shutter. Finish opens a summary with a share card and Snap → Sell drafts | **Pro.** The Scan tab's entry pill reads *Haul* with a PRO tag for free users | `HaulView.swift:348-430`, `:646-800`; `ScanView.swift:248-258`, `:954-957` |
| **Result** | Hero photo · item name · chips: brand, and the **condition grade** (#192: the grade, no longer a cut of the model's notes) · *Estimated Resale Value* · range · band **High / Medium / Low confidence** · **AI estimate** · *Why this price* (PRO for free users) · *Sharpen this estimate* (fresh results) · *Condition*: New · Like New · Good · Used | Free; *Why this price* and the tag re-read are Pro | `ResultView.swift:684-691`, `:820-846`, `:1080-1099`; `DesignSystem.swift:670-672`; `ScanResult.swift:580-586` |
| **Snap → Sell** | "A marketplace-ready listing, tailored to where you sell." · nine chips · generated title, description, **Ask** and **Floor** · Copy · Share · Open *marketplace* · Regenerate · "SnapWorth writes it — you paste & post. We never post for you." The PRO badge renders only for free users | **Pro** | `ResultView.swift:1398-1540`, `:1404`, `:1422` |
| **My Flips** | *Profit this month* (free) or *All-time profit* (Pro) · sold count · Invested (all-time) · Avg ROI · Best flip · Not sold yet · *Last 6 months* · filters · rows. Profit is sold − paid − fees | Free, capped at the 10 latest sold flips; all-time totals and CSV are Pro | `FlipsView.swift:83-139`, `:185`; `Config.swift:128`; `FlipsViewModel.swift:111-123`; `ScanResult.swift:528-531` |
| **Widgets** (T, #240) | *Haul Value* (small, medium) · *Quick Scan* (small) · *Haul on the Lock Screen* (three accessory sizes) · *Recent finds* (medium, large) · *Scans left* (small, circular) · *Profit this month* (small) · the Thrift run Live Activity · a Scan control (iOS 18+) | Free: no widget checks the tier | `SnapWorthWidgetsBundle.swift:7-16`; `HaulWidget.swift:270-272`; `QuickScanWidget.swift:106-108`; `LockScreenWidgets.swift:173-175`; `MoreWidgets.swift:165-167`, `:247-249`, `:304-309`, `:368-370` |
| **Stickers** (1.5.0) | 20 stickers of Tag, 4 animated (hi-wave, snooze, worth-it-flip, yay-bounce), in the emoji keyboard's sticker drawer | Free | `ios/SnapWorthStickers/…/Sticker Pack.stickerpack` (20 `.sticker`, 4 APNG) |
| **My Finds** | *Your finds are worth* · *N items scanned* · *Trending at the thrift* · search · two-column grid of your own photos | Free; value history and the week's notable finds are Pro | `HistoryView.swift:95-160`, `:366-381` |
| **Paywall** | Headline (the trigger's pitch, or *Unlock SnapWorth Pro* — *Try SnapWorth free for N days* only with an eligible offer) · Yearly and Monthly cards with StoreKit prices · seven benefits, Unlimited scans and Haul first · the fair-use footnote · Subscribe · Restore | — | `PaywallView.swift:38-139`, `:389-394`, `:519-535`, `:647` |

Stickers are not one of the eight: an app with an iMessage extension gets its
own **iMessage App** screenshot slot in App Store Connect, and that is where
the sticker drawer goes (`RELEASE-NOTES-1.5.0.md:48-56`).

### Never show

The listing's *Claims this listing does not make* (`app_store_listing.md`)
binds every frame and every caption. On top of it, for pictures:

| ❌ | Why |
|---|---|
| "Sold listings", comps, market data, "Based on recent sales", a listing count | SnapWorth has no such source. The result's caption must read **AI estimate**; the comps caption exists in code (`ScanAPIClient.swift:26`) and must never be in a frame |
| "Accurate", "exact", "precise" | No accuracy has been measured (`docs/EVALUATION.md`) |
| A numeric confidence score — e.g. "72 / 100 confidence", "Confidence 72 out of 100" | The listing names a *level*. The number is Pro detail in *Why this price* (`ResultView.swift:1843-1848`), and the free teaser's fallback line prints it too, blurred (`:1295-1308`) |
| The Pro *Why this price* panel | It carries the number above, directly under the ladder |
| "Unlimited" without "fair use" | Pro scans are capped per hour on the server (paywall footnote, `PaywallView.swift:647`) |
| A trial length, or a trial badge | ASC setting; shown in-app only to eligible accounts |
| A Pro feature in a frame whose caption does not say Pro | Guideline 2.3.2 |
| Anything DEBUG: the `-mock-scans` canned results, the Simulator's synthetic numbered photos, any build other than the one submitted | See `SCREENSHOT-HANDOFF.md` §4 |
| Another device or platform, an Android phone | iPhone only; Guideline 2.3.10 |
| Marketplace logos or brand colours | Names as the app renders them: plain text in neutral chips |

### ✅ Claimable

- An AI resale **estimate** from one photo, as a range, with a confidence
  **level** (High, Medium, Low)
- **Profit after marketplace fees**, before you buy — free. The fee table is
  real `Decimal` maths with a cited source per rate (`MarketplaceFees.swift:144-186`),
  and approximate, as the screen itself says
- **Haul mode — Pro:** snap item after item while each is valued, with a
  running total. Not "in one go": a haul shares the hourly cap and can pause
  partway (`PaywallView.swift:521-526`)
- **Snap → Sell — Pro:** a listing for the marketplace you pick, nine of them
- A profit ledger: paid, sold, and what was made after fees
- Home Screen and Lock Screen widgets
- Photos never stored on our servers; no account; scan history on the device
  (`LegalView.swift:39`, `:64`). **Not** "your data never leaves your
  phone": the server keeps each scan's item name, brand, category and range,
  without the photo or the device, for 35 days (`LegalView.swift:66`)
- One free scan every day; Pro when you want more

---

## 1. Strategy

### The moment

Someone in a charity shop holding a jacket, with about fifteen seconds before
they buy it or put it back. Their question is not "what is this worth?" but
**"will I make money on this?"** Thrift Flip is the screen that answers it,
and it has been free since 1.4.3 (#128). The homepage already leads with it
(`website/index.html`); the store did not.

### Why the order changed

The search card, not the product page, is the bottleneck: 2.1% of 16.8K
impressions tapped through while 18.6% of page views installed
(`app_store_listing.md`, *Subtitle*). The card shows the name, the subtitle
and the first screenshots, so the first three carry the whole pitch:

| # | Question | Frame | Tier |
|---|---|---|---|
| 1 | Will I make money on this? | **Thrift Flip** — the verdict, after fees | Free |
| 2 | What if I've got a whole pile? | **Haul** — the strip and the running total | **Pro** |
| 3 | Can I trust the number? | **Result** — range, confidence band, *AI estimate* | Free |
| 4 | Then what? | **Snap → Sell** — the listing, written | **Pro** |
| 5 | Does it help over time? | **My Flips** — the ledger | Free |
| 6 | Do I have to open the app? | **Widgets** | Free |
| 7 | Is my data safe? | **Privacy** — over My Finds | Free |
| 8 | What does it cost? | **Plans** — one free scan a day, then Pro | — |

Frame 1 is the decision; frame 2 is what 1.5.1 adds; frame 3 is the honesty
that makes the first two believable. Frames 4–8 are proof for the minority
who swipe.

### Free and Pro, said on the frame

Two of the eight show Pro features. Their captions say **Pro**, in words and
with a PRO tag, because Guideline 2.3.2 asks the screenshots to make clear
what needs a purchase — and because a free user who installs for Haul and
meets a paywall writes the one-star review. Thrift Flip's caption says it is
free, with the daily scan. The Plans frame closes on what is free.

The frames are captured to match: every frame not captioned Pro is captured
on the **free** tier, so nothing Pro-only (the ladder, *All-time profit*,
value history) appears under a free caption.

---

## 2. Visual system

From `DesignSystem.swift` (`SnapLightHex`) and `SnapDarkHex`
(`Services/WidgetDataStore.swift`). Do not invent colours.

| Token | Light | Dark | Use in screenshots |
|---|---|---|---|
| Terracotta | `#D96C47` | `#E8845F` | The accent word |
| Terracotta fill | `#A8482C` | — | The PRO tag, as the app's own badge |
| Sage | `#6F8F6B` | `#8FB08A` | Money, profit, positive verdicts; washes |
| Espresso | `#2B211C` | `#F0E9E2` | Headline text |
| Warm grey | `#6E6055` | `#B0A297` | Subhead text |
| Cream | `#FBF7F2` | — | Light ground; text on the PRO tag |
| Deep espresso | — | `#17120F` | The dark ground (Haul) |
| Charcoal | `#1C1714` | `#1C1714` | Camera chrome, both themes |

**Type** — headlines Fraunces Bold 96–104pt, two lines, five words at most;
subheads DM Sans Regular 42pt, two lines, fifteen words at most; the PRO tag
DM Sans Bold. No third face. Both fonts are in `ios/SnapWorth/Fonts/`.

**Canvas** — 1320 × 2868 (iPhone 6.9″), the only slot 1.5.1 needs: Apple
scales it down for the smaller iPhones, and the app runs on nothing else.

```
y=0     ┌─────────────────────────┐
y=96    │        [ PRO ]          │   Pro frames only
y=200   │   HEADLINE  (2 lines)   │
y=500   │   subhead   (1–2 lines) │
        │   ┌─────────────────┐   │
        │   │  device, 940 px │   │   real capture in a drawn bezel
y=2660  │   └─────────────────┘   │   ← device baseline
y=2868  └─────────────────────────┘   120 px bottom margin
```

Nothing below the device: the gallery crops the bottom. **The frame is drawn;
the screen never is.** Every device screen is a real capture from the
submitted build, composited by `marketing/build_screenshots.py`. The only
drawn marks are the bezel, the ground and the caption (with its PRO tag) —
no floating UI chips, no lockups over the screen, no generated UI.

**Grounds** — cream with a soft radial lift; sage wash under the two money
frames (1, 5); terracotta wash at the base of Plans (8); **deep espresso for
Haul (2)**, the only dark frame, because it is a camera screen and because a
tonal break in the strip stops a scroll.

---

## 3. The eight frames

Captions as `build_screenshots.py` sets them; the accent word is in
*italics*. Capture states and sample items are in `SCREENSHOT-HANDOFF.md` §3.

### 1 — Thrift Flip · free

> **Profit, after *fees.***
> Shop price in, fees out: a verdict before you buy. Free with your daily scan.

**Screen** Thrift Flip with the verdict card: shop price, eBay selected,
**Worth flipping**, the profit line, Net profit, ROI and the fee breakdown.
**Ground** Cream, sage wash across the lower half.
**Why it converts** Every rival stops at a price. The fee maths is the one
claim a raw vision model cannot copy, it is verifiable, and it is free.
**Compliance** The app's words only: *Worth flipping*, never "you'll make
$32". The Expected resale field stays as the model seeded it.

### 2 — Haul mode · **Pro**

> [PRO] **Scan a whole *haul.***
> With Pro: snap item after item while each is valued, with a running total.

**Screen** Haul's camera: the running *Estimated total* at the top, a strip
of six to eight valued thumbnails, the shutter, a real pile in the viewfinder.
**Ground** Deep espresso.
**Why it converts** It is the new thing in 1.5.1, and the most visual:
the number climbing as the strip fills is the whole feature in one picture.
**Compliance** Guideline 2.3.2: PRO tag and "With Pro" in the subhead.
"Scan a whole haul" is the paywall's own pitch for this trigger
(`PaywallView.swift:572-574`); never "in one go" — a haul shares the hourly
cap. No pause banner in frame.

### 3 — Result · free

> **Know before you *buy.***
> An AI resale estimate from one photo, with its confidence level.

**Screen** The result sheet from the top: photo, item name, brand and
**condition grade** chips, *Estimated Resale Value*, range, the confidence
band, **AI estimate**, and *Why this price* locked with its PRO badge.
**Ground** Cream, radial lift.
**Why it converts** Showing the confidence level and the *AI estimate* label
on frame 3 signals honesty in a category built on overclaiming.
**Compliance** Captured free, so the Pro panel and its 0–100 number never
render. The live `store_2` shows the cut-off chip #192 fixed ("Appears unworn
with or"); this frame is the fix in public.

### 4 — Snap → Sell · **Pro**

> [PRO] **Your listing, already *written.***
> With Pro: a title and description tailored to where you sell, on nine marketplaces.

**Screen** Snap → Sell with a generated listing: eBay chip selected, title,
description, Ask and Floor, Copy / Share / Open eBay, and the "We never post
for you" line.
**Ground** Cream.
**Why it converts** Listing copy is the chore resellers hate most.
**Compliance** Captured on Pro, so the in-app PRO badge is absent (it renders
only for free users); the caption carries Pro instead. "Tailored to where you
sell" is the card's own line. Nine is `Marketplace.allCases`.

### 5 — My Flips · free

> **Every flip, *tracked.***
> What you paid, what it sold for, and what you made after fees.

**Screen** My Flips on the free tier: *Profit this month*, the four stat
cards, *Last 6 months*, the first rows.
**Ground** Cream, sage wash across the lower half.
**Why it converts** A tool you keep, not a novelty; "what you made" reads as
a ledger, not a vanity dashboard.
**Compliance** Free tier, so the header is *Profit this month* — *All-time
profit* is Pro. Real flips only.

### 6 — Widgets · free

> **Your finds, at a *glance.***
> Home Screen and Lock Screen widgets: total value, recent finds, one-tap scan.

**Screen** A Home Screen page of SnapWorth widgets: Haul Value (medium),
Recent finds (medium), Scans left and Quick Scan (small).
**Ground** Cream.
**Why it converts** Widgets have shipped since 1.4.0 and have never been in
the gallery; they say "this lives on your phone" without a word.
**Compliance** "Finds", not "haul": Haul is the Pro mode in frame 2, and
the widget named *Haul Value* is free. No tier check in any widget.

### 7 — Privacy · free

> **Your photos stay *yours.***
> Never stored on our servers. No account. Your scan history stays on your device.

**Screen** My Finds, scrolled to the grid of the user's own finds.
**Ground** Cream.
**Why it converts** Camera plus AI is the highest-anxiety pair in consumer
apps; all three claims are the privacy policy's own.
**Compliance** Every line matches `LegalView.swift:39` and `:64` and the
listing's PRIVACY paragraph. Photos and history only — the server does keep
anonymous per-scan facts (`:66`), so no broader "data" claim. The v1 spec's
lock lockup over a dimmed grid is dropped: it was drawn UI over a capture.

### 8 — Plans

> **One *free* scan, every day.**
> Then Pro, when you want more. Cancel anytime.

**Screen** The paywall from Settings → Upgrade: *Unlock SnapWorth Pro*,
"$39.99/year. Cancel anytime.", the Yearly card ($39.99 · $0.77 per week ·
SAVE 33%) and the Monthly card ($4.99), the first benefits.
**Ground** Cream, terracotta wash at the base.
**Why it converts** Leading with what is free sets an accurate expectation,
which is also what App Review looks for.
**Compliance** Real StoreKit prices from the US storefront, the same figures
the en-US description names. **No trial line**: capture from an account that
has already used the trial, so the paywall shows none — its length is an ASC
setting the store page must not freeze.

---

## 4. Preview video — not in 1.5.1

Not produced, and not needed for 1.5.1. If one is made later: 1080 × 1920,
30 fps, silent-first, real captures only, the frame order above, and the last
card reads **One free scan a day** (not three). The rules in §0 apply to
every frame of it.

---

## 5. Localisation

The app speaks English, Romanian, Spanish, German and Simplified Chinese; the
v1 tables for French and Italian were for languages it does not speak and are
gone. 1.5.1 uploads the English set only. A localised set is **captured in
that language** — a German caption over an English screen is worse than the
English set — and follows once that locale's listing is live and has been
read by a native speaker (#203, *Notes*): German first. Localised captions
are written from this set then, under the same §0 rules in that language's
words (the listing files name the forbidden ones).

---

## 6. Measuring it

Tap-through (page views ÷ impressions) two and four weeks after 1.5.1 goes
live, against the baseline week 2026-09-28 → 10-04 on 1.5.0 (20)
(`docs/GROWTH-DASHBOARD.md:61`, `:76`). 1.5.1 changes the subtitle and adds
four localizations in the same release, so a movement is the release's, not
the screenshots' alone. Product Page Optimization could test captions later;
at ~2.8K impressions a week a test needs weeks, so nothing waits on it.

---

## 7. Apple compliance

| Guideline | Item | How this set meets it |
|---|---|---|
| **2.3.1 / 2.3.7** Accurate metadata | Screenshots show the app as it is | Real captures from the submitted build; §0 is the limit |
| **2.3.2** In-app purchase disclosure | Pro features labelled | Frames 2 and 4: PRO tag + "With Pro"; free frames captured free |
| 2.3.3 | Show the app in use | Every frame is an in-use screen, none a splash or title card |
| 2.3.10 | No other platforms | iPhone only, no Android |
| 3.1.2 | Subscription terms | Real prices; no trial claim the viewer may not get |
| 5.1.1 | Privacy accuracy | Frame 7 quotes the policy |
| Trademarks | Marketplace names | Plain text as the app renders them; no logos or brand colours |

### Rejection risks, ranked

1. **A Pro feature under a caption that does not say Pro** (2.3.2).
2. **Anything from a DEBUG build or a mock** in a frame — the reason §0 and
   the handoff exist.
3. **The 0–100 number** in frame 3 or 4.
4. **A trial badge** the viewer's account may not get.
5. **A marketplace logo** in a mockup.
