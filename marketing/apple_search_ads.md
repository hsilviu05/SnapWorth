# Apple Search Ads — Starter Plan (SnapWorth)

Goal: buy the top-of-funnel impressions ASO is slow to build, and learn which
search terms actually convert. Your product page converts ~42% (page view →
install), so paid taps are unusually efficient — this is the point of the test.

- **Product:** Apple Search Ads **Advanced** (keyword-level control + Search Match).
  Not Basic — you want to see per-keyword data.
- **Where:** [searchads.apple.com](https://searchads.apple.com) → set up billing.
- **Storefront:** **US first** (matches your listing). Add UK/CA/AU later — your
  ASO is already localized there.
- **Total budget:** **$10/day** to start. Concentrate, don't spread thin.
- **App:** SnapWorth (id 6788521307), live.

---

## Campaign structure (3 campaigns, one ad group each)

Separate campaigns so budgets and intent don't blur.

### A. Generic / category — **$6/day**  ⭐ the core test
High-intent terms describing what the app does. **Exact match.**

| Keyword | Match | Starting max CPT |
|---|---|---|
| resale value | Exact | $1.00 |
| what's it worth | Exact | $0.80 |
| thrift flip | Exact | $1.00 |
| resale price | Exact | $0.90 |
| reseller app | Exact | $1.20 |
| what sells on ebay | Exact | $0.90 |
| thrift store finds | Exact | $0.80 |
| sell my clothes | Exact | $0.90 |
| price checker resale | Exact | $0.90 |

> Use Apple's **Suggested Bid** as a sanity check; start near it, not above.

### B. Competitor / adjacent — **$2/day**
People searching reseller marketplaces are your buyers. Lower intent for a
*scanner*, so smaller budget and watch CPA closely.

| Keyword | Match | Starting max CPT |
|---|---|---|
| poshmark | Exact | $1.50 |
| depop | Exact | $1.50 |
| mercari | Exact | $1.50 |
| whatnot | Exact | $1.30 |
| vinted | Exact | $1.30 |

### C. Discovery (Search Match) — **$2/day**
One ad group with **Search Match ON** and **no keywords** — Apple matches you to
queries it thinks fit. This *mines* new keywords cheaply. Review weekly: move
winners into Campaign A as Exact, add junk as negatives.

### D. Brand (optional, later) — **$1/day**
`snapworth` Exact. Cheap, defensive (stops competitors bidding on your name).
Skip at first if budget is tight; add once the above are tuned.

---

## Negative keywords (add to A, B, and especially C)
Block wasted spend on unrelated intent:
`free`, `games`, `game`, `wallpaper`, `hack`, `cheats`, `jobs`, `loan`,
`stock`, `crypto`, `dating`, `music`, `movie`, `real estate`, `car`, `coupon`.

---

## Creative
Start with your **default product page** (current screenshots — they convert at
42%, so don't touch them yet). Later, test a **Custom Product Page** aimed at
resellers (lead with the My Flips / profit screenshot) and run it as an ad
variation.

---

## Targets & guardrails
- **Cost per tap (CPT):** aim $0.40–$1.50 depending on term.
- **Cost per install (CPI):** target **≤ $2.00** to start; pause terms far above.
- **Daily cap:** hard $10/day so a runaway keyword can't drain you.
- No conversion-to-paid data yet, so optimize on **CPI + install volume** first;
  layer in trial/subscribe once your analytics (now in build 1.1.1) reports it.

---

## Weekly optimization (15 min)
1. **Pause** keywords with high taps + 0 installs, or CPI ≫ $2.
2. **Raise bids** modestly on keywords with good CPI + high tap-through.
3. **Harvest** Search Match: move converting discovered terms into Campaign A
   (Exact), and add non-converting/irrelevant ones as **negatives**.
4. **Reallocate** budget from B (competitor) → A (generic) if A wins on CPI.
5. After ~2 weeks, decide: scale the winners, or conclude paid isn't worth it
   yet and double down on ASO + organic.

---

## First-session checklist
- [ ] Create ASA Advanced account, add payment method
- [ ] Set account/campaign daily caps ($10 total)
- [ ] Build Campaign A (Exact, 9 keywords) — $6/day
- [ ] Build Campaign B (competitors) — $2/day
- [ ] Build Campaign C (Search Match, no keywords) — $2/day
- [ ] Add the negative keyword list to all three
- [ ] Storefront = US; ages/gender = all; devices = iPhone
- [ ] Launch, then check back in 3–4 days (not hourly)
