# SnapWorth — App Store screenshot system

**Version:** 1.0 · 2026-07-28
**Status:** ready for design production
**Supersedes:** the current screenshot set, which is blocked — see `SCREENSHOT-COMPLIANCE.md`

This spec is built against **verified shipping UI only**. Every element named
below exists in the app today. Nothing is aspirational.

---

## 0. Ground truth — what the app actually shows

Verified by reading `ios/SnapWorth/Views/` on 2026-07-28. Design must not
exceed this.

| Screen | Renders |
|---|---|
| **ScanView** | Dark viewfinder, corner accents, "Center the item — tags & logos help", shutter, library button, "Flip" button, "3 free scans left today" pill |
| **ResultView** | Hero photo · item name · brand + condition chips · "ESTIMATED RESALE VALUE" · `$45–$90` range · confidence badge · **"AI estimate"** · Condition selector · "What did you pay?" · Flip status · Listing Draft · Snap → Sell (Pro) |
| **ThriftFlipView** | "Should you flip it?" · resale range · marketplace picker · "Shop price" + "Scan tag" · verdict card: **"Worth flipping"** / **"Skip it"** · profit after fees |
| **HistoryView** | "Your finds are worth $X" banner · `N items scanned` · search · 2-col grid |
| **FlipsView** | Items sold · profit · ROI · last 6 months |
| **PaywallView** | Yearly / Monthly cards · benefits · trial (only when StoreKit reports one) |

### Hard constraints

| ❌ Never show | Why |
|---|---|
| "Sold listings", comps, a listing count | No marketplace retrieval exists |
| "Real market data", "based on recent sales" | Same |
| A numeric confidence score (e.g. "72") | Client renders a band only |
| Four price points (quick/expected/best/worst) | Backend has them; client does not decode them |
| Explainability lists (visual evidence, assumptions) | Same |
| Depop, Poshmark, Mercari, StockX logos | Snap → Sell supports **eBay, Vinted, Facebook, OLX** |
| "Guaranteed", "accurate to the dollar", "instant profit" | Unsupportable |

### ✅ Truthfully claimable

- AI resale **estimate** from a photo, as a range
- Confidence level, shown as High / Medium / Low
- **Profit after marketplace fees** — the fee table is real and exact
  (`MarketplaceFees.swift`, `Decimal` math, eBay 13.25% + $0.40)
- Listing draft tailored to eBay / Vinted / Facebook / OLX
- Local scan history and a profit ledger
- **Photos are never stored on our servers** — verified true
- **No account needed** — verified true
- 3 free scans per day

---

## 1. Strategy

### The moment we are selling to

A person is standing in a charity shop holding a jacket. Phone in one hand,
jacket in the other. They have maybe fifteen seconds before they either buy it
or put it back.

Their question is **not** "what is this worth?" It is **"will I make money on
this?"** Every competitor answers the first question. SnapWorth is the only one
that answers the second, because the fee math is real.

So the sequence leads with the *decision*, not the technology.

### Sequence logic

The App Store gallery shows roughly **three screenshots** before a swipe. Those
three must carry the entire pitch. Screens 4–8 are for the minority who swipe —
they convert on depth, so they get proof rather than repetition.

| # | Question answered | Job |
|---|---|---|
| 1 | What is this? | Hook — the answer, not the process |
| 2 | Why is this better? | Differentiator — profit after fees |
| 3 | Is it fast enough for a shop aisle? | Objection — speed |
| 4 | Is it honest? | Trust — confidence + "AI estimate" |
| 5 | What else do I get? | Listing draft |
| 6 | Does it help long-term? | Ledger / retention |
| 7 | Is my data safe? | Privacy |
| 8 | What does it cost? | Pro, framed as unlock not paywall |

---

## 2. Visual system

Taken from `DesignSystem.swift`. Do not invent colours.

| Token | Light | Dark | Use in screenshots |
|---|---|---|---|
| Terracotta | `#D96C47` | `#E8845F` | Primary accent, CTA, one word per headline |
| Sage | `#6F8F6B` | `#8FB08A` | Money, profit, positive verdicts |
| Amber | `#EBB868` | `#E5BE7C` | Badges only — use sparingly |
| Espresso | `#2B211C` | `#F0E9E2` | Headline text |
| Warm grey | `#6E6055` | `#B0A297` | Supporting text |
| Cream | `#FBF7F2` | — | Light backgrounds |
| Deep espresso | — | `#17120F` | Dark backgrounds |
| Charcoal | `#1C1714` | `#1C1714` | Camera frames, both themes |

**Type**
- Headlines: **Fraunces Bold**, 96–112pt, tracking −2%, max 2 lines
- Supporting: **DM Sans Regular**, 40–44pt, warm grey, max 2 lines
- Never mix a third face.

**Canvas** — 1320 × 2868 (iPhone 6.9″). Required.

```
┌─────────────────────────┐  ← 0
│         160px           │     top margin
│   HEADLINE (2 lines)    │  ← 200–470
│   supporting (1–2)      │  ← 500–620
│                         │
│   ┌─────────────────┐   │  ← 760
│   │                 │   │
│   │  DEVICE MOCKUP  │   │     device: 940px wide,
│   │  (940 × 1900)   │   │     centred, 2660 baseline
│   │                 │   │
│   └─────────────────┘   │
│         120px           │     bottom margin
└─────────────────────────┘  ← 2868
```

**Fix the current composition problem.** The existing screenshots waste ~40% of
the canvas as empty space below the device, and the App Store gallery crops the
bottom — so the device is half-lost in the preview. Device baseline sits at
**2660px**, bottom margin 120px. Nothing below the device.

**Device frame** — iPhone 17 Pro, Natural Titanium, flat front-on, no
perspective, no shadow tilt. One consistent frame across all eight. Real
screenshots at 3× from the simulator; never a redraw.

**Backgrounds** — flat or a single soft radial, ≤8% luminance variance. No
photography behind the device. No confetti. No floating UI chips.

---

## 3. The eight screenshots

---

### 1 — Know before you buy

**Goal** State the product in one glance.
**Emotional objective** Recognition. "That's my exact problem."

> # Know before
> # you buy.
>
> An AI resale estimate from one photo.

**Device** ResultView, light mode, scrolled to the value card.
**UI state** Item: a well-worn Patagonia-style fleece (generic, no logo
close-up). Chips: brand + `Good`. `ESTIMATED RESALE VALUE` · `$45–$90` ·
`High confidence` · `AI estimate` visible.
**Background** Cream `#FBF7F2`, radial lift to `#FFFFFF` behind the device.
**Accent** "buy" in terracotta. Nothing else coloured.
**Icons** None. The UI is the illustration.
**Motion (for video)** Value figure counts up `$0 → $45–$90` over 400ms,
spring, no bounce past target.

**Why it converts** The headline is the user's own internal sentence. "From one
photo" pre-empts "how much work is this?" before it is asked. Showing the real
confidence badge on screenshot 1 signals honesty immediately, which is the
opposite of the category's usual posture — and this category has a trust
problem.

---

### 2 — Profit, after fees

**Goal** Land the differentiator. This is the screenshot that wins the install.
**Emotional objective** "Oh — it does the maths I keep doing wrong."

> # Profit, after
> # fees.
>
> Shop price in. Marketplace fees out. A clear verdict.

**Device** ThriftFlipView, verdict card visible.
**UI state** Shop price `$8` · marketplace `eBay` · verdict **`Worth flipping`**
in sage · profit line beneath.
**Background** Cream, with a very soft sage wash (`#6F8F6B` at 6%) behind the
lower third — colour reinforcing the meaning of the verdict.
**Accent** "fees" in terracotta; the verdict stays sage inside the UI.
**Motion** Fee amount ticks down from gross to net, then the verdict chip scales
in from 0.9 with a spring.

**Why it converts** Every rival stops at a price. Resellers know the price is
not the point — fees, shipping and the walk-away floor are. This is the one
claim no competitor built on a raw vision model can match, and it is
*verifiable*: the fee table is real, exact, and in the repository.

**Compliance note** The verdict copy is "Worth flipping" / "Skip it" — a
suggestion, not a guarantee. Do not add "you'll make $32". Keep the app's own
wording.

---

### 3 — Four seconds, in the aisle

**Goal** Kill the "too slow / too fiddly" objection.
**Emotional objective** "I could actually use this while shopping."

> # Four seconds,
> # in the aisle.
>
> Point, snap, decide. No account, no typing.

**Device** ScanView, dark viewfinder, item centred in the corner-accent frame,
"3 free scans left today" pill visible top-right.
**Background** Deep espresso `#17120F` — the only dark screenshot in the set.
The tonal break makes it the visual anchor of the gallery strip.
**Accent** "Four seconds" in terracotta against the dark ground.
**Icons** None.
**Motion** Corner accents pulse once inward, shutter depresses, frame freezes.

**Why it converts** "No account" is the single highest-friction objection for a
utility app and is *true* — worth stating plainly. The dark frame breaks the
cream rhythm, which measurably increases scroll-stop in a gallery strip.

**Truthfulness of "four seconds"** After the client-side downscale
(~280 KB upload), a typical scan is a sub-second upload plus a 2–4s model call.
Four seconds is defensible as typical, not guaranteed. If legal prefers zero
risk, use **"Seconds, in the aisle."** — no measurable conversion cost.

---

### 4 — It tells you when it's unsure

**Goal** Convert the sceptic. Differentiate on honesty.
**Emotional objective** Relief. "It's not going to lie to me."

> # It tells you
> # when it's unsure.
>
> Every estimate shows its confidence. No false precision.

**Device** Two ResultView cards, overlapping, slight vertical offset (front card
+40px down, back card scaled 0.94, 60% opacity). Front: `High confidence`.
Back: `Low confidence` on a blurrier item.
**Background** Cream, flat.
**Accent** "unsure" in terracotta.
**Motion** Back card slides out from behind the front card, 300ms ease-out.

**Why it converts** Advertising a limitation is the strongest possible trust
signal in a category built on overclaiming — and it is the only screenshot here
a competitor cannot copy without building the same computed-confidence system.
It also does quiet legal work: a user who saw "shows its confidence" on the
store page cannot reasonably claim they expected a guarantee.

---

### 5 — Your listing, already written

**Goal** Show the Pro value without a paywall.
**Emotional objective** "That's the boring part done."

> # Your listing,
> # already written.
>
> A title and description tailored to where you sell.

**Device** ResultView scrolled to the Listing Draft + Snap → Sell card.
Marketplace chips visible: **eBay · Vinted · Facebook · OLX** — these four only.
The `PRO` badge stays visible; do not hide it.
**Background** Cream, flat.
**Accent** "already written" in terracotta.
**Motion** Description lines type in left-to-right, 40ms per line, then the
"Copy" button fills.

**Why it converts** Listing copy is the most-hated chore in reselling. Showing
the `PRO` badge rather than hiding it pre-qualifies the install — users arrive
already knowing there is a paid tier, which reduces one-star "it's not free"
reviews and raises trial conversion.

**Compliance** The app's own line — *"SnapWorth writes it — you paste & post. We
never post for you."* — is excellent. Keep it visible in the UI. It pre-empts
any "auto-posting" expectation.

---

### 6 — Every flip, tracked

**Goal** Show this is a tool you keep, not a novelty.
**Emotional objective** "This gets more useful over time."

> # Every flip,
> # tracked.
>
> Paid, listed, sold. See what you actually made.

**Device** FlipsView, showing items sold, profit, ROI, last-6-months.
**Background** Cream with a faint sage tint in the lower half.
**Accent** "tracked" in terracotta; profit figures stay sage in-UI.
**Motion** Bars grow from baseline, staggered 60ms.

**Why it converts** Retention framing raises perceived value at the same price.
Speaks directly to the serious reseller, who is the paying segment. "What you
actually made" is deliberately unglamorous — it signals a real ledger rather
than a vanity dashboard.

---

### 7 — Your photos stay yours

**Goal** Remove the last hesitation.
**Emotional objective** Calm.

> # Your photos
> # stay yours.
>
> Analysed, then discarded. Never stored on our servers.

**Device** HistoryView grid, softly dimmed, with a single centred lockup:
SF Symbol `lock.shield` in terracotta above three short lines:
`Never stored on our servers` / `No account required` / `History stays on your device`
**Background** Cream, flat. Most whitespace of the set.
**Accent** "yours" in terracotta.
**Motion** Lock glyph draws in, lines fade up staggered 80ms.

**Why it converts** Camera permission plus AI is the highest-anxiety combination
in consumer apps. All three claims are verified true in the codebase, and the
privacy policy already states them.

**Compliance** Every line must match `PrivacyInfo.xcprivacy` and the privacy
policy exactly. It currently does. Re-verify if either changes.

---

### 8 — Three free scans, every day

**Goal** Convert the fence-sitter without a hard sell.
**Emotional objective** "No risk in trying."

> # Three free scans,
> # every day.
>
> Go unlimited with Pro when you're ready.

**Device** PaywallView, plan cards visible with **real StoreKit prices**.
**Background** Cream → very soft terracotta gradient (≤6%) at the base.
**Accent** "free" in terracotta.
**Motion** Yearly card border draws in, badge pops last.

**Why it converts** Leading with what is free, not what is paid, is the highest-
converting final frame for freemium utilities. It also sets an accurate
expectation, which is what App Review looks for.

**Compliance — critical** Screenshots must show the **real localised price** for
the storefront. Never hardcode `$39.99` into a non-US screenshot. Trial length
must match App Store Connect exactly. Do not show a trial badge in a storefront
where the offer is not configured.

---

## 4. Preview video — 30 seconds

**Format** 1080 × 1920, 30fps, H.264. **Silent-first**: 85% of App Store
previews autoplay muted, so every beat must read without audio.

| Time | Frame | Motion | On-screen text |
|---|---|---|---|
| 0.0–1.5 | Hands lift a jacket off a rail, shop bokeh | Slow push in, 105mm feel | — |
| 1.5–3.0 | Phone raises into frame, camera opens | Match cut to app UI | **Know before you buy.** |
| 3.0–5.0 | Viewfinder, corner accents pulse, tag centred | Micro-shake, handheld | — |
| 5.0–5.6 | Shutter. Frame freezes, whitens 8% | Impact haptic beat | — |
| 5.6–8.0 | Analysing overlay, sparkle shimmer, copy rotates | Overlay fades in | *Reading the label…* |
| 8.0–10.5 | Result card rises, value counts `$0 → $45–$90` | Spring, no overshoot | **$45–$90** |
| 10.5–12.0 | Confidence badge scales in | Pop, 0.9 → 1.0 | **High confidence** |
| 12.0–14.0 | Hold on `AI estimate` label | Static — let it be read | **AI estimate** |
| 14.0–16.0 | Swipe to Thrift Flip | Push left | **But will you profit?** |
| 16.0–18.5 | Shop price `$8` types in, eBay selected | Number ticks | — |
| 18.5–20.5 | Fees deduct, verdict chip lands | Count-down, spring | **Worth flipping** |
| 20.5–23.0 | Listing draft types itself in | Line-by-line reveal | **Listing, written for you** |
| 23.0–25.5 | My Flips ledger, bars grow | Staggered rise | **Track what you made** |
| 25.5–28.0 | Home screen, app icon settles | Pull back | **Your photos stay yours** |
| 28.0–30.0 | Wordmark on cream | Fade up | **SnapWorth** · *3 free scans a day* |

**Camera language** Only two moves: a slow push-in on real-world footage, and
horizontal pushes between screens. No spins, no parallax, no 3D device tumbling.

**Transitions** Cross-dissolve for real-world → UI (once, at 1.5s). Everything
else is a hard cut on the beat. Cuts read as confidence; dissolves read as
filler.

**Music** Warm, unhurried, acoustic-electronic. 90–100 BPM. Single melodic idea.
Nothing percussive under the shutter — let the visual carry it. Duck to −18dB
under the 12–14s hold.

**Haptics** Not in the video, but the app's own beats should mirror it:
`Haptics.capture()` at the shutter, `Haptics.success()` at the result,
`Haptics.selection()` at the marketplace pick. That correspondence is what makes
the app feel like the video when a user finally installs.

**The 12–14s hold on "AI estimate" is deliberate.** Two seconds of stillness on
an honesty signal, in a category where every other preview is racing. It is the
most memorable beat in the cut precisely because nothing happens.

---

## 5. Localisation

Adapted, not translated. The English relies on rhythm and idiom that will not
survive a literal pass.

### Romanian
| # | Headline | Note |
|---|---|---|
| 1 | **Află cât face,<br>înainte să cumperi.** | "Cât face" is exactly how a Romanian thrifter phrases it |
| 2 | **Profit, după<br>comisioane.** | "Comisioane" is the standard marketplace term |
| 3 | **Câteva secunde,<br>în magazin.** | Avoid a specific count — sentence rhythm differs |
| 4 | **Îți spune când<br>nu e sigur.** | — |
| 7 | **Pozele rămân<br>ale tale.** | — |

**Market note** OLX dominates Romanian resale, and SnapWorth already supports
it. Reorder screenshot 5 to put the **OLX** chip first for the `ro` storefront.

### German
| # | Headline | Note |
|---|---|---|
| 1 | **Wissen, was es<br>wert ist.** | German resists the second-person imperative here |
| 2 | **Gewinn — nach<br>Gebühren.** | "Gebühren" is unambiguous |
| 3 | **Sekunden,<br>im Laden.** | Compounds run long; drop the count |
| 4 | **Sagt dir, wenn es<br>unsicher ist.** | — |
| 7 | **Deine Fotos<br>bleiben deine.** | — |

**Layout warning** German runs 30–35% longer. Allow 3 headline lines at 88pt for
`de`, and re-check every device-mockup crop.

### French
| # | Headline |
|---|---|
| 1 | **Sa valeur,<br>avant d'acheter.** |
| 2 | **Le bénéfice,<br>frais déduits.** |
| 3 | **Quelques secondes,<br>en boutique.** |
| 4 | **Il vous dit<br>quand il doute.** |
| 7 | **Vos photos<br>restent les vôtres.** |

Use *vous* throughout — *tu* reads cheap for a paid tool in French.

### Spanish
| # | Headline |
|---|---|
| 1 | **Sabe cuánto vale,<br>antes de comprar.** |
| 2 | **Ganancia, ya sin<br>comisiones.** |
| 3 | **Segundos,<br>en la tienda.** |
| 4 | **Te avisa cuando<br>no está seguro.** |
| 7 | **Tus fotos<br>siguen siendo tuyas.** |

Neutral Latin-American Spanish; avoid *vosotros*.

### Italian
| # | Headline |
|---|---|
| 1 | **Quanto vale,<br>prima di comprarlo.** |
| 2 | **Guadagno, al netto<br>delle commissioni.** |
| 3 | **Pochi secondi,<br>in negozio.** |
| 4 | **Ti dice quando<br>non è sicuro.** |
| 7 | **Le tue foto<br>restano tue.** |

"Al netto" is the standard Italian financial phrasing and lands as precise
rather than promotional.

**Do not localise before the English set is validated.** Localising a losing
variant six times multiplies the cost of being wrong.

---

## 6. A/B tests

Run via App Store Connect Product Page Optimisation. **One variable at a time.**

| # | Test | Control | Variant | Hypothesis | Est. lift | Confidence in estimate |
|---|---|---|---|---|---|---|
| 1 | **Hook order** | S1 = value estimate | S1 = profit verdict | Resellers convert on profit, not price | **+8–15%** | Medium — strong category logic, no data |
| 2 | **Honesty frame** | S4 confidence card | S4 replaced with speed | Trust > speed for a valuation tool | **+3–7%** | Low — genuinely uncertain, worth learning |
| 3 | **Dark screenshot 3** | Dark viewfinder | Light viewfinder | Tonal break increases scroll-stop | **+2–5%** | Medium |
| 4 | **Free-tier framing** | S8 "Three free scans" | S8 "Unlock unlimited" | Leading with free converts better | **+5–10%** | High — well-established for freemium utilities |
| 5 | **Device framing** | Full iPhone frame | Cropped, edge-bleeding UI | Bigger UI is more legible at gallery size | **+2–6%** | Medium |
| 6 | **Icon** | Current sparkle-on-tag | Concept A aperture/tag (`AUDIT-v2.md` §7) | Distinctiveness raises tap-through | **+5–12%** | Medium |

**These percentages are estimates from category convention, not measurements.**
Nothing in this repository has ever been A/B tested. Treat them as a
prioritisation aid, not a forecast.

**Minimum runtime** 7 days or 1,000 impressions per variant, whichever is
longer. Weekday and weekend thrifting behaviour differ enough that a 3-day test
reliably misleads.

---

## 7. ASO

### Metadata

**Name** `SnapWorth` (10/30) — room to extend
**Suggested** `SnapWorth: Resale Value` (24/30) — adds two high-intent keywords
to the most heavily weighted field

**Subtitle** Current: `Resale Value in Seconds` (23/30). Strong. Alternative to
test: `Thrift Finds, Priced in Seconds` (30/30).

**Keywords** (100 chars, no spaces after commas, never repeat name/subtitle terms)

```
thrift,resale,flip,reseller,secondhand,vintage,goodwill,ebay,vinted,scanner,price,value,profit
```

Changes from current: **dropped** `clothing` (implied by `thrift`), `poshmark`
and `depop` (unsupported marketplaces — do not buy intent you cannot serve).
**Added** `goodwill` (very high intent, low competition), `reseller`, `profit`.

**Promotional text** (170 chars, updatable without review)
> Now with Thrift Flip: add the shop price and see your profit after marketplace
> fees before you buy. Three free scans every day.

### Description — first three lines

These are all that shows before "more". Currently a rhetorical question; lead
with the concrete benefit instead.

> Know what a thrift find is worth before you buy it. Photograph any secondhand
> item for an AI resale estimate — then add the shop price to see your profit
> after marketplace fees.

### Ratings

`ReviewPrompt.swift` exists. Verify it fires **after a successful scan with High
confidence**, never after an error or a Low-confidence result. Requesting a
review immediately after a bad estimate is how a 4.6 becomes a 3.9.

---

## 8. Apple compliance review

| Guideline | Item | Status |
|---|---|---|
| **2.3.1 / 2.3.7** Accurate metadata | No comps claims; every UI element real | ✅ if this spec is followed |
| 2.3.2 | Pricing shown must be real and localised | ⚠️ **Screenshot 8 must use live StoreKit prices per storefront** |
| 2.3.10 | No other-platform references | ✅ No Android/web mentions |
| 3.1.2 | Subscription terms | ✅ Paywall shows full auto-renew disclosure |
| 5.1.1 | Privacy accuracy | ✅ Screenshot 7 matches the privacy policy exactly |
| 4.2 | Minimum functionality | ✅ Well beyond |
| 1.1 | Objectionable content | ✅ |
| **Trademarks** | eBay / Vinted / Facebook / OLX names | ⚠️ Plain text only. **No logos, no brand colours.** Marketplace names as neutral chips, exactly as the app renders them |

### Rejection risks, ranked

1. **Any residual "sold listings" wording** anywhere in the set — this is the
   blocker that already exists. Re-read every frame before upload.
2. **Hardcoded USD in a non-US storefront** — a 2.3.2 flag and a refund driver.
3. **A trial badge in a storefront with no configured offer.**
4. **A marketplace logo** rendered in a mockup — trademark exposure.
5. **UI that does not exist** — the reason §0 is the first section of this
   document.

---

## 9. Conversion review

### What this set does well

- Screenshot 2 carries a differentiator that is *verifiable*, not asserted.
- Screenshot 4 makes honesty a feature. In a category built on overclaiming,
  that is the position nobody else can take without building the system.
- Screenshot 7 answers the highest-anxiety objection (camera + AI) with three
  claims that are all literally true.
- Every headline is under five words and reads at gallery thumbnail size.

### Common mistakes this set deliberately avoids

| Mistake | Why it costs conversion |
|---|---|
| Feature-listing in headlines | Users scan for *outcomes* |
| Floating UI chips outside the device | Reads as a mockup, not an app |
| Three fonts | Reads cheap instantly |
| Dead space below the device | The gallery crops it — the current set loses ~40% |
| Screenshot 1 explaining *how* it works | Nobody has bought in yet |
| Buzzwords ("revolutionary", "powered by AI") | The audience is transactional |
| Hiding the `PRO` badge | Produces one-star "not really free" reviews |
| Showing the paywall first | Highest-bounce opener in freemium |

### The single highest-leverage change

**Reordering, not redrawing.** The current set opens with a false claim; the
proposed set opens with the user's own question and reaches the profit
differentiator by frame 2. Even with identical artwork, that sequence change is
likely worth more than every visual improvement combined — because frame 2 is
where a reseller decides SnapWorth is not another price-lookup toy.

---

## 10. Production handoff

### Capture
1. `Config.mockMode = true`
2. Simulator: **iPhone 17 Pro Max**, light appearance, 9:41 status bar
3. `xcrun simctl status_bar <UDID> override --time 9:41 --batteryLevel 100 --cellularBars 4 --wifiBars 3`
4. Capture at 3×, PNG, no compression
5. **Screenshot 3 in dark appearance** — the viewfinder is dark in both themes,
   but the surrounding chrome must match

### Verify before upload
- [ ] No frame contains "sold", "comps", "market data", or a listing count
- [ ] Only eBay / Vinted / Facebook / OLX named; no logos
- [ ] Prices in screenshot 8 are live per storefront
- [ ] Device baseline at 2660px; nothing below it
- [ ] Headlines ≤5 words; supporting ≤15
- [ ] One accent word per headline
- [ ] Legible at 1/6 scale (the gallery thumbnail test)
- [ ] `AI estimate` label visible in at least one frame

### AI image-generation prompt (backgrounds and composition only — never the UI)

> Minimal App Store screenshot background, warm cream `#FBF7F2` with a subtle
> radial lift to pure white behind the centre. Flat, premium, generous
> whitespace, no texture, no objects, no text. Soft, even light. 1320 × 2868
> vertical. In the style of Apple's own App Store product pages.

**The device screen must always be a real captured screenshot composited in.**
Never let a generative model draw the UI — it will invent controls, and inventing
UI is precisely the failure this document exists to prevent.
