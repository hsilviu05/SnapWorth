# Launch Check-in — Day 4 & Day 7

Launch day: **2026-07-22**. Between now and Day 4, **don't touch anything** — you
need data before decisions. Two scheduled reviews only.

---

## DAY 4 — Fri 2026-07-26  ·  "Is everything working?" (no optimizing yet)

Goal: confirm every pipe is flowing and ads are serving. **Do not pause or edit
Search Ads keywords yet** — 4 days is too little to judge performance.

### 1. Apple Search Ads (searchads.apple.com)
- [ ] All 3 campaigns flipped from "App pending" → **Running**? (If still pending after 3 days, contact ASA support.)
- [ ] Any **Taps** and **Installs**? Note avg **CPT** per campaign.
- **Good:** getting a few taps/day, CPT under ~$2.50.
- **Concerning:** 0 impressions after running 2+ days → your bids are losing every auction. Raise Default Max CPT by ~$0.50 and recheck. (This is the *only* ad change allowed on Day 4.)

### 2. TelemetryDeck (app analytics) — the critical "is it wired?" check
- [ ] Dashboard showing `app_opened`, `scan_started`, etc. from real users?
- **Good:** daily events appearing.
- **Concerning:** still empty after real 1.1.1 usage → ping me, we'll check the wiring. (Likely fine — just needs users on the new build.)

### 3. Vercel Web Analytics
- [ ] Page views showing? Check **top sources** — do you see Product Hunt referrals?
- **Concerning:** zero data → Web Analytics may not be enabled on the project, or the deploy didn't pick up the script. Tell me.

### 4. App Store Connect → Analytics
- [ ] **Impressions** trending up vs. the 146 baseline? (ASO + PH + ads compounding.)
- [ ] Conversion rate holding (~30–40% is healthy).

### 5. Ratings & Reviews
- [ ] Any new ratings? (The in-app prompt fires on a user's 3rd scan — so this grows with usage, not instantly.)

**Day-4 rule: look, don't touch.** Only allowed change = raise a bid if a campaign has 0 impressions.

---

## DAY 7 — Mon 2026-07-29  ·  First real optimization

Now there's enough data to make calls.

### 1. Apple Search Ads — optimize
For each campaign look at **Avg CPA (= cost per install)**:
- [ ] **Keep/scale** any keyword with **CPA ≤ $2** and installs.
- [ ] **Pause** keywords with lots of taps + **0 installs**, or CPA ≫ $2.
- [ ] **Compare campaigns:** is **Generic** beating **Competitor** on CPA? If yes (likely), shift Competitor's $2 → Generic.
- [ ] **Harvest Discovery:** open Campaign C → **Search Terms** report. Move converting queries into Generic as `[exact]` keywords; add junk as negatives.

### 2. Channel attribution — the big question: *what actually drove installs?*
Compare where downloads came from:
- [ ] **App Store Connect → Sources** — App Store Search vs. Referrer vs. App Referrer. (Search Ads + ASO show here.)
- [ ] **Vercel sources** — how much web traffic → App Store came from Product Hunt vs. Instagram vs. direct.
- [ ] Rough tally: of your new installs this week, which channel gets credit?

### 3. Decide (write it down)
- **Winner** (best cost/effort per install): **double down.** More budget if it's ASA; more posts if it's IG; more SEO if it's organic search.
- **Loser:** cut or pause. Don't keep spending on a channel that isn't converting.
- **ASA total:** if after a week CPA is way above what a user is worth (sub is $4.99/mo, $39.99/yr), pause ads and lean on the free channels (ASO, content, communities).

### 4. Housekeeping
- [ ] Confirm the local Xcode components finished updating (so you can build again).
- [ ] Post Instagram posts 2 & 3 if you haven't.

---

## What "good" looks like after a week
- Impressions clearly above the 146 baseline (ASO/ads working).
- ≥ a handful of new ratings (ranking signal building).
- At least one channel with a **CPA you'd happily pay** → that's your growth engine to scale.
- Analytics flowing on all 4 layers so future decisions are data-driven.

**Then bring me the numbers** and we'll decide where to put the next dollar/hour.
