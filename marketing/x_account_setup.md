# X account — setup and first posts

Everything here is paste-ready and claims-checked against what the app actually
does. Character counts are against X's limits (bio 160, post 280).

> **Free tier is one scan a day.** Nothing here says three. The App Store
> description, website and server all now say one — keep X consistent with them.

---

## Account

**Handle** — check availability in this order:
`@snapworth` → `@snapworthapp` → `@getsnapworth`

Whichever you take, use the same one on TikTok so the two are findable from
each other.

**Display name:** `SnapWorth`

**Bio** (147 / 160):

```
Point your camera at any thrift find and know what it's worth before you buy. AI resale estimates in seconds. One free scan a day, no account. iOS.
```

**Link:** `https://snapworth.eu`
**Avatar:** the app icon (terracotta tag mark)
**Header:** the 1080×1350 IG post crops badly — export a 1500×500 version of the
same artboard instead (`marketing/ig/dc-1.3.3/FreeScan.dc.html`).

---

## Pinned post (269 / 280)

```
SnapWorth tells you what a thrift find is worth before you buy it.

Point your camera at anything secondhand — jacket, sneakers, camera, bag. You get an AI resale estimate in about four seconds, plus a listing written for you.

One free scan a day. No account, no card.
```

Attach `shot34_scan_to_result.mp4` — the real screen recording of a scan
returning $220–$420. A working demo outperforms any copy you could write.

---

## Why build-in-public is the right strategy here

X does not reward polished app ads from accounts with no following. It rewards
**specific, concrete stories from people making something.** You have unusually
good material for that, and it is all true:

- A paywall that read "free for 3 dayss" in production for weeks, because the
  mock data disagreed with real StoreKit about its own input
- A local StoreKit config broken since July, so the only code path that
  reproduced the bug was the one nobody could run
- $0.0060 per scan, measured — with reasoning tokens as 69% of the cost
- A 2,747-line comparable-sales engine, fully tested, switched off
- An app that told users they had 3 free scans while the server allowed 1

Posts like these get read by other builders, who are also resellers, and who
share things. Ads from a 0-follower account get seen by nobody.

**Ratio to aim for:** roughly 3 build-in-public posts to 1 product post.

---

## Starter posts

**1 — the cost of a scan** (a number people like arguing about)

```
Measured what one AI scan actually costs me: $0.0060.

1,591 prompt tokens. 2,212 output tokens — but 69% of that output is reasoning the user never sees, billed at the output rate.

The thinking is the product and also most of the bill.
```

**2 — the typo** (self-deprecating, very shareable)

```
My paywall said "Try SnapWorth free for 3 dayss" for weeks.

Two layers each pluralised the trial. One built "3-days", the other added another s.

Tests passed because the mock hardcoded the singular — it disagreed with production about its own input.
```

**3 — the honest limit** (the free-tier change, framed straight)

```
Cut my free tier from 3 scans a day to 1.

At 3 almost nobody hit the limit, so nobody ever saw the paywall. Download-to-paid sat at 1.4%.

A free tier nobody reaches isn't generous, it's just expensive.
```

**4 — the demo** (attach `shot5_listing_draft.mp4`)

```
It doesn't just price the item — it writes the listing.

Title, description, condition notes. Copy it, paste it, post it.
```

---

## Claims check

- ✅ One free scan a day — matches `FREE_SCANS_PER_DAY=1`
- ✅ AI resale estimate as a range — that is the result screen
- ✅ About four seconds — measured scan latency
- ✅ Writes a listing draft — free tier; **marketplace-tailored** listings are Pro
- ✅ $0.0060/scan and the token counts — measured, not estimated
- ❌ Never claim sold listings, comps or market data. There is no such source;
  `backend/comps/` is built but disabled.
- ❌ No accuracy percentage. No measured figure exists to quote.
