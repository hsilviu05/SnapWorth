# Instagram Posts — Version 1.3 Launch

3 compact single-image posts marketing **My Finds portfolio** and the **Weekly Summary**.
Graphics: `marketing/ig/out/` (1080×1350). Regenerate:
`python3 marketing/ig/build_1_3.py`

Source of truth for the artwork is `marketing/ig/dc-1.3/*.dc.html` (also published
as an editable design canvas). The build script strips the Design Component wrapper
and swaps the Google Fonts link for the real embedded woff2, so canvas and PNG match.

Brand: Fraunces + DM Sans · terracotta `#D96C47` · sage `#7A9E7E` · cream `#FAF7F4` · dark `#1C1410`.

> **Hold until 1.3 is approved.** These name a version that is not live yet.
> Example figures ($4,820 / 34 items / the three garments) are illustrative UI
> data — swap for real numbers from a live My Finds screen before posting.

---

## POST 1 — "What's New in 1.3" announcement
**Image:** `out/post1_13_whatsnew.png`

```
SnapWorth 1.3 just dropped. 📈

Your finds are no longer a list — they're a portfolio.

✅ My Finds — one number for everything you've scanned, and what's still waiting to be listed
✅ Weekly Summary — an optional Sunday recap, so you know where you stand without opening the app

Pro adds value history: how your portfolio has moved over time.

Update today 👉 link in bio

#reselling #thriftflip #resellercommunity #sidehustle #depop #vinted #ebayreseller #thrifting #thriftstorefinds #appupdate
```

---

## POST 2 — "My Finds" portfolio
**Image:** `out/post2_13_portfolio.png`

```
How much is your closet actually worth? 👀

My Finds now opens with the answer — every item you've ever scanned, totalled. Underneath it tells you what still needs listing, what you actually made on the things you sold, and what's been sitting too long.

Pro adds value history, so you can see the trend instead of guessing.

New in 1.3 👉 link in bio

#reselling #resellercommunity #thriftflip #sidehustle #whatsitworth #depopseller #vinted #ebayreseller #thrifting #flippingforprofit
```

---

## POST 3 — "Weekly Summary"
**Image:** `out/post3_13_weekly.png`

```
Sunday, 11:00. ☕️

Your week in finds, delivered — new items, value added, and how many are still waiting to be listed. No opening the app, no chasing a spreadsheet.

Optional, one a week, and you can switch it off in Settings whenever.

New in 1.3 👉 link in bio

#reselling #resellercommunity #thriftflip #sidehustle #thrifting #depop #vinted #sidehustletips #thriftstorefinds
```

---

## Copy constraints these respect

- **No passive appreciation.** Nothing re-values a saved item on its own; the
  total moves when you scan, re-price or sell. "One number for everything you've
  scanned" — never "watch your wealth grow".
- **No "exact value".** The app returns a range with a confidence score, which is
  why post 2's mock rows read `$135–180`, not `$156`.
- **Only marketplaces the app serves** are named: eBay, Vinted, Facebook
  Marketplace, OLX.
- **The weekly summary is called optional twice** in post 3 — the notification
  permission prompt is the most likely bounce point in this release.
