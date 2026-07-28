# SnapWorth — screenshot designer handoff

Condensed production sheet. Full rationale in `SCREENSHOT-SPEC.md`.

---

## 1. Screen inventory

Eight screenshots. Column 2 is the **real app screen** to capture — never a
redraw. Column 3–4 is the marketing copy composited above it.

| # | App screen to capture | Headline (≤5 words) | Subhead (≤15 words) |
|---|---|---|---|
| 1 | `ResultView` — scrolled to value card | **Know before<br>you buy.** | An AI resale estimate from one photo. |
| 2 | `ThriftFlipView` — verdict card visible | **Profit, after<br>fees.** | Shop price in. Marketplace fees out. A clear verdict. |
| 3 | `ScanView` — viewfinder, item framed | **Four seconds,<br>in the aisle.** | Point, snap, decide. No account, no typing. |
| 4 | `ResultView` ×2, overlapping cards | **It tells you<br>when it's unsure.** | Every estimate shows its confidence. No false precision. |
| 5 | `ResultView` — Listing Draft + Snap → Sell | **Your listing,<br>already written.** | A title and description tailored to where you sell. |
| 6 | `FlipsView` — sold / profit / ROI | **Every flip,<br>tracked.** | Paid, listed, sold. See what you actually made. |
| 7 | `HistoryView` dimmed + privacy lockup | **Your photos<br>stay yours.** | Analysed, then discarded. Never stored on our servers. |
| 8 | `PaywallView` — plan cards | **Three free scans,<br>every day.** | Go unlimited with Pro when you're ready. |

### Required UI state per shot

| # | Must be visible on device |
|---|---|
| 1 | `ESTIMATED RESALE VALUE` · `$45–$90` · `High confidence` badge · `AI estimate` label |
| 2 | Shop price `$8` · marketplace `eBay` · verdict **`Worth flipping`** in sage |
| 3 | Corner accents · "Center the item — tags & logos help" · `3 free scans left today` pill |
| 4 | Front card `High confidence`; back card `Low confidence`, scaled 0.94, 60% opacity, +40px offset |
| 5 | Marketplace chips **eBay · Vinted · Facebook · OLX** only · `PRO` badge visible |
| 6 | Items sold · profit · ROI · last 6 months |
| 7 | `lock.shield` glyph + 3 lines: Never stored on our servers / No account required / History stays on your device |
| 8 | **Live StoreKit prices for the target storefront** |

### Never show

Sold listings · comps · "market data" · a listing count · a numeric confidence
score · four price points · Depop / Poshmark / Mercari / StockX · any marketplace
**logo** (plain text names only) · UI that does not exist in the build.

---

## 2. Device + canvas

### Required uploads

| Slot | Pixels | Device to capture on | Status |
|---|---|---|---|
| **iPhone 6.9″** | **1320 × 2868** | iPhone 17 Pro Max | **Required** — matches your existing assets |
| iPhone 6.5″ | 1242 × 2688 | iPhone 11 Pro Max | Optional — Apple scales from 6.9″ if omitted |
| **iPad 13″** | **2064 × 2752** | iPad Pro 13″ | **Required** — see blocker below |

> **6.7″ (1290 × 2796) is no longer a separate upload.** It was the required
> iPhone size before 6.9″ superseded it; Apple now scales 6.9″ down to cover it.
> Do not produce it.

> ### ⚠️ iPad blocker
> `TARGETED_DEVICE_FAMILY = "1,2"` — the app **declares iPad support**, so App
> Store Connect will require iPad screenshots before submission. You have none.
>
> Two options, and this is a product decision, not a design one:
> - **Produce iPad screenshots** — the app is portrait-locked and camera-first,
>   so it will look sparse on a 13″ canvas.
> - **Drop iPad support** — set `TARGETED_DEVICE_FAMILY = 1`. Honest if iPad was
>   never a real target; removes the requirement entirely.
>
> Recommend dropping it unless iPad is a deliberate market.

### Canvas layout — 1320 × 2868

```
y=0     ┌─────────────────────────┐
        │        160 px           │   top margin
y=200   │   HEADLINE  (2 lines)   │
y=470   │                         │
y=500   │   subhead   (1–2 lines) │
y=620   │                         │
y=760   │   ┌─────────────────┐   │
        │   │                 │   │   device: 940 px wide
        │   │  DEVICE MOCKUP  │   │   centred on x
        │   │   940 × 1900    │   │
y=2660  │   └─────────────────┘   │   ← device baseline
        │        120 px           │   bottom margin
y=2868  └─────────────────────────┘
```

**Nothing below y=2660.** The current set wastes ~40% of the canvas under the
device, and the App Store gallery crops the bottom — the device is half-lost in
the preview grid.

### Device frame

- iPhone 17 Pro, **Natural Titanium**
- Flat, front-on. No perspective, no tilt, no drop shadow
- One identical frame across all eight
- Screen content composited from a real 3× simulator capture

### Safe areas (for capture, not composition)

| | 6.9″ (440 × 956 pt) |
|---|---|
| Top inset | 59 pt (Dynamic Island) |
| Bottom inset | 34 pt (home indicator) |
| Status bar | Override to 9:41, 100% battery, full bars |

---

## 3. Type system

| Role | Font | Size | Weight | Tracking | Colour | Max |
|---|---|---|---|---|---|---|
| Headline | **Fraunces** | 96–112 pt | Bold (700) | −2% | Espresso `#2B211C` | 2 lines |
| Headline accent | Fraunces | same | Bold | −2% | Terracotta `#D96C47` | **1 word** |
| Subhead | **DM Sans** | 40–44 pt | Regular (400) | 0 | Warm grey `#6E6055` | 2 lines |

- Line height: headline **1.05**, subhead **1.35**
- Never a third typeface
- Exactly one accent word per headline — the verb or the payoff
- German: allow 3 headline lines at **88 pt** (runs 30–35% longer)

Both fonts are in the repo: `ios/SnapWorth/Fonts/Fraunces-Variable.ttf`,
`DMSans-Variable.ttf`.

---

## 4. Colour + background

From `DesignSystem.swift`. Do not invent values.

| Token | Light | Dark | Use |
|---|---|---|---|
| Terracotta | `#D96C47` | `#E8845F` | Accent word, CTA |
| Sage | `#6F8F6B` | `#8FB08A` | Money, profit, positive verdict |
| Amber | `#EBB868` | `#E5BE7C` | Badges only — sparingly |
| Espresso | `#2B211C` | `#F0E9E2` | Headline text |
| Warm grey | `#6E6055` | `#B0A297` | Subhead text |
| Cream | `#FBF7F2` | — | Light background |
| Deep espresso | — | `#17120F` | Dark background |
| Charcoal | `#1C1714` | `#1C1714` | Camera chrome, both themes |

### Per-screenshot background

| # | Background |
|---|---|
| 1 | Cream `#FBF7F2`, radial lift to `#FFFFFF` behind device centre |
| 2 | Cream + sage wash `#6F8F6B` at **6%** across lower third |
| 3 | **Deep espresso `#17120F`** — the only dark frame in the set |
| 4 | Cream, flat |
| 5 | Cream, flat |
| 6 | Cream + faint sage tint, lower half |
| 7 | Cream, flat — most whitespace of the set |
| 8 | Cream → terracotta gradient at **≤6%**, base only |

### Lighting

- Backgrounds: **flat or a single soft radial**, ≤8% luminance variance
- No photography behind the device
- No confetti, no floating UI chips, no glow
- Device screen at 100% opacity — never dimmed except screenshot 7's deliberate
  scrim

Screenshot 3's dark ground is deliberate: the tonal break makes it the visual
anchor of the gallery strip.

---

## 5. Compliance, localisation, export

### Compliance gate — check every frame before upload

- [ ] No "sold listings", "comps", "market data", or a listing count
- [ ] Only **eBay · Vinted · Facebook · OLX** named; **no logos**, no brand colours
- [ ] Screenshot 8 shows **live localised prices** per storefront (never hardcoded USD)
- [ ] No trial badge in a storefront where the offer isn't configured
- [ ] Privacy lines match `PrivacyInfo.xcprivacy` and the policy exactly
- [ ] Every UI element exists in the shipping build
- [ ] `AI estimate` label visible in at least one frame

Guidelines in play: **2.3.1 / 2.3.7** (accurate metadata — the blocker that
already exists), **2.3.2** (pricing), **3.1.2** (subscription terms), **5.1.1**
(privacy).

### Localisation

Six locales, adapted not translated. Full headline table in
`SCREENSHOT-SPEC.md` §5.

| Locale | Note |
|---|---|
| `en` | Master. Validate before localising anything |
| `ro` | **Reorder screenshot 5 to put OLX chip first** — OLX dominates Romanian resale |
| `de` | 30–35% longer; 3 lines at 88 pt; re-check every crop |
| `fr` | *vous* throughout — *tu* reads cheap for a paid tool |
| `es` | Neutral Latin-American; no *vosotros* |
| `it` | "al netto" is the standard financial phrasing |

**Do not localise until the English set has run.** Localising a losing variant
six times multiplies the cost of being wrong.

### Export

| Setting | Value |
|---|---|
| Format | **PNG**, no compression |
| Colour space | **sRGB** (App Store Connect rejects Display P3) |
| Bit depth | 8-bit |
| Alpha | **None** — flatten to background |
| Naming | `{locale}_{device}_{NN}_{slug}.png` → `en_69_01_know-before-you-buy.png` |

### Capture procedure

```bash
# 1. Config.mockMode = true
# 2. Boot the simulator, then:
xcrun simctl status_bar <UDID> override \
  --time 9:41 --batteryLevel 100 --batteryState charged \
  --cellularBars 4 --wifiBars 3
```

Capture at 3× from **iPhone 17 Pro Max**, light appearance — except screenshot
3, which is dark appearance.

### Final legibility test

View the full set at **1/6 scale**. If a headline is unreadable, it fails — that
is the size a user actually sees in the gallery strip.
