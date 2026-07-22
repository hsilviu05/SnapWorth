# SnapWorth — ASO + Launch Kit

Goal: fix the real bottleneck — **impressions** (only 106 to date, while the
listing converts at ~42%). More discovery + more ratings = more installs.

---

## 1. App Store Optimization (ASO)

App Store search weights fields roughly: **Title > Subtitle > Keyword field**.
Rules: no spaces in the keyword field, no word repeated across fields (wasted),
singular forms only (Apple matches plurals), don't include your app name.

### Title (30 char max) — most weight
Current: `SnapWorth: Resale Scanner` (25)
Options:
- **`SnapWorth: Resale Value`** (23) — trades "scanner" for higher-intent "value"
- `SnapWorth – Resale Scanner` (26) — keep as-is
- `SnapWorth: Thrift Resale` (24) — leads with the niche

### Subtitle (30 char max) — second most weight
Current: `Resale Value in Seconds` (23)
Options:
- **`Thrift & Resale Price Finder`** (28) — packs thrift + resale + price + finder
- `Scan Thrift Finds for Value` (27)
- `What's It Worth to Resell?` (26) — matches how people search

### Keyword field (100 char max) — paste-ready
Avoids words already used in title/subtitle above. Exactly ~100 chars:
```
reseller,poshmark,depop,vinted,mercari,ebay,flip,secondhand,vintage,comps,worth,priced,sneakers,pawn
```
> App Store Connect enforces the 100-char cap — if it trims, drop `pawn` then `sneakers`.

### Also do
- **Use Apsly** to check which of these actually have volume + low difficulty, and swap the weak ones. Re-check every few weeks.
- **Localize keywords** to other English storefronts (en-GB, en-AU, en-CA) — free extra keyword slots that also index for the US.
- **Screenshots:** your first 2 screenshots do the converting. Add the new
  Share Card + My Flips shots once captured (My Flips is a strong hook).
- **Prompt ratings** — now shipped in code (`ReviewPrompt`). Ratings volume is a
  ranking factor; this compounds ASO.

---

## 2. Product Hunt launch kit

**When:** Tue–Thu, post at **12:01 AM PT** (PH day resets then; you want a full
day to gather votes). Avoid holidays / big-tech launch days.

**Prep:** line up a **hunter** with a following (or self-launch), and rally 15–20
friends to upvote + comment in the first 2 hours (early velocity drives ranking).

### Name
SnapWorth

### Tagline (60 char max)
- **Know what any thrift find is worth — instantly** (44)
- AI resale-value scanner for thrifters & resellers (49)

### Description (~260 char)
> Point your camera at any secondhand item — a jacket, sneakers, a vintage
> camera — and SnapWorth's AI checks real sold listings to give you an instant
> resale value range. New: Share Cards with a scan-me QR, and My Flips to track
> your real profit.

### Topics
`iPhone` · `Shopping` · `Artificial Intelligence` · `Side Projects`

### Gallery
Reuse `marketing/ig/out/*.png` + the 5 App Store screenshots. Lead with a short
Reel/GIF of a scan → valuation if you can.

### Maker's first comment (template)
> Hey PH 👋 I'm Silviu, maker of SnapWorth.
>
> I kept picking things up at thrift stores wondering "is this actually worth
> something?" So I built an app that tells you instantly — snap any item and the
> AI checks real sold listings across eBay, Poshmark, Depop & more for a resale
> range in seconds.
>
> Just shipped 1.1: **Share Cards** (shareable valuations with a scan-me QR) and
> **My Flips** (a profit ledger that tracks what you actually made).
>
> Free to try, no account needed. Would love your feedback — what item should I
> test-scan for you? 👇

---

## 3. Other high-leverage channels (beyond IG/TikTok/Reddit/Shorts)

| Channel | Why | Effort |
|---|---|---|
| **Apple Search Ads** | Your page converts ~42% → cheap installs. Test $5–10/day on "thrift", "resale value", competitor brand terms. | Low |
| **Facebook reseller Groups** | Biggest reseller audience online. Answer "what's this worth?" posts with a SnapWorth screenshot. | Low, ongoing |
| **Reseller Discords / Whatnot** | Engaged flippers who share tools. | Low |
| **SEO pages** | "How much is [item] worth to resell" pages on snapworth.eu — compounding Google traffic feeding installs. | Medium |
| **Micro-influencer seeding** | Gift Pro to 5–50k thrift/reseller creators; authentic > ads. | Medium |
| **Apple "Featuring" nomination** | Well-designed utility = good candidate. Submit via Apple's featuring request form. | Low |
| **Niche newsletters/press** | Reselling & thrifting newsletters for a backlink + install spike. | Medium |

**Suggested order:** ASO + ratings (live now) → Product Hunt → Search Ads test →
FB groups + SEO in parallel.
