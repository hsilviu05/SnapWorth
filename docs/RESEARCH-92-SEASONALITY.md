# Issue #92, "Best time to sell": what the evidence supports

**Date:** 2026-09-26 · **Scope:** issue #92 (`needs-research`)

> **Status of the numbers.** The Google Trends figures below were pulled for
> this research from the JSON endpoint behind the Trends web page, which is not
> an official API (see Risks). They are good enough to decide the design. They
> are not the table to ship: regenerate it from manual CSV exports or the
> Trends API alpha, and commit those exports, before any of it reaches the app.

---

## Summary and recommendation

**Recommendation: don't build #92 as written. Change the design, then build a small version.**

**Why the current design doesn't hold up:**

- **No source for price or speed.** Nothing in reach measures how secondhand prices or sell-through change by month, for any category. The only evidence that is reproducible, per country and per category is search interest (Google Trends [1]). That is a measure of demand, not of price.
- **The model can't know when or where.** The scan prompt carries no date and no country (`backend/main.py:1676-1685`, `backend/prompts.py`), and `ScanAPIClient.swift` sends no locale. So any window the model writes is a guess that defaults to one market.
- **Real windows differ, even for the issue's own example.** In the US, searches for "nuptse" in September are only 0.21 of the November peak. Spain's peak is January. Australia and New Zealand peak May–July. Costume demand in Germany and Spain peaks in January–February, not October [1].
- **The acceptance test is broken.** The gate "populated on ≥80% of the gold set" rewards the model for inventing seasonality, because most scanned items aren't seasonal. And the gold set doesn't exist: `docs/EVALUATION.md:3-6` says "There is no gold dataset in this repository".

**Build instead:**

1. The model only classifies the item. A new enum `season_class` is `null` for most items; the model writes no dates and no prose.
2. The app looks the class up in a small, cited table keyed by class and device region.
3. The copy talks about demand ("Demand usually peaks Nov–Dec in the UK"), never "best time" or price.
4. A region without evidence gets no hint. That includes China (Google has 1.41% of search there [17]) and any region not in the table.

**Fixed alongside this document:** the Nuptse page in `website/seo/build_seo.py` contradicted itself and made a price claim nothing supports:
- line 39: "strong year-round demand"
- line 41: "Season (peaks in fall/winter)"
- line 44: "Demand and prices peak in autumn and early winter. Listing in September–December typically gets the best offers."

The data shows a 9–12× swing between peak and trough, with the peak in Nov–Dec, not Sep–Dec. No source supports "prices peak".

---

## What the repo already says

- **The issue's evidence is circular.** #92 says the Sep–Dec window is "the kind of thing the model knows and the SEO pages already say". That SEO copy came from `89b60f8` (2026-07-23), which generated the content with no source cited. So one uncited model output is being used to support another.
- **The honesty precedent is strict.** #35 and `8128f80` removed unsupported marketing claims. `marketing/SCREENSHOT-COMPLIANCE.md` applies FTC Act §5 and EU UCPD reasoning; the FTC standard is that objective claims need a reasonable basis before they go out [20]. `prompts.py` names "fabricated specificity" as the largest hallucination risk.
- **The markets are spread out.** The US is the largest market; Romania was about 35% of installs at #54; China was 12 of 98 installs (`f784da8`). Supported marketplaces include Kleinanzeigen, Xianyu, OLX and Vinted.
- **Model text is always English.** Free text from the model is English whatever the UI language (`ios/Localization/README.md` §listing text). A model-written "Sep–Dec" would show up in English inside the Romanian, Spanish, German and Chinese UIs, and `tools/check_localization.py` can't see server strings.

---

## Findings

### 1. Which resale categories have documented, stable seasonality

**What the marketplaces publish.** It is all qualitative, and none of it covers secondhand prices:

- **Poshmark [6] (US, 2014):** buyers shop "in advance of the upcoming season (about six to eight weeks)". In-season and pre-season items "can call for a higher price". For cold-weather items listed in warm months, "consider lowering your price".
- **Kleinanzeigen [7] (DE):** "Die Nachfrage für Winterjacke oder Sommerkleid ist in der passenden Jahreszeit einfach höher." ("Demand for a winter jacket or a summer dress is simply higher in the matching season.")
- **Wallapop [8][9] (ES), from its own search data:**
  - activity peaks at the end of August with *vuelta al cole* (back to school);
  - searches for *trajes de flamenca* rise 133% in spring;
  - searches for neck fans rise 152% in June.
  - One article is titled "products that will sell faster this spring", but its figures are search increases (garden furniture sets +237%, parasols +300%). Even the platform presents search spikes as sale speed.
- **eBay [10][11][12]:**
  - The US Seller Center seasonal playbook has no category-by-month data.
  - The export guide is a calendar of events (back to school Sep 1, Halloween Oct 31, Singles' Day Nov 11, Black Friday). It notes that events differ by country (Small Business Saturday falls on a different date in the UK than in the US) and never mentions hemispheres.
- **ThredUp [13]:** the Resale Report announcements contain no seasonal data.
- **Vinted, Depop, Mercari:** I found no primary seasonal data. Figures that circulate, such as "80% of such purchases occur" in coat season, trace to third-party Vinted automation tools that cite no source [19]. Don't use them.
- **Academic work:** Soysal & Krishnamurthi (Marketing Science, 2012) [18] show that demand for seasonal apparel shifts through the season. The data is from a new-goods retailer, so it doesn't transfer to secondhand prices.

**What I measured (Google Trends [1]).**

*Method:*
- weekly search interest from 2021-09-01 to 2026-08-31, one keyword per country;
- monthly means, with each Sep–Aug season scaled to its own peak;
- the scaled values averaged over five seasons;
- **window** = months at or above 0.70 of the peak;
- **stable** = the peak month fell inside the window in at least 4 of 5 seasons;
- **strong** = peak-to-trough ratio of at least 3×.

*Result:*
- **Stable and strong:**
  - winter outerwear, winter boots and snow sports;
  - swimwear in northern markets;
  - Halloween costumes, carnival costumes and Christmas decorations;
  - school bags in Romania.
- **Weak or not a single window:** bikes in every market, and backpacks in the US and Spain. Swimwear and skis in Australia are too weak (1.9× and 2.0×).
- **Unknown for every category:** secondhand price and sell-through.

### 2. Hemispheres and the app's markets: one "Sep–Dec" window does not hold

- **Even in the US, "Sep–Dec" is wrong.** September interest as a share of the peak:

  | Search | US | UK | DE | ES |
  |---|---|---|---|---|
  | "puffer jacket" | 0.21 | 0.55 | — | — |
  | "nuptse" | 0.21 | 0.41 | 0.41 | — |
  | "plumífero" (down jacket) | — | — | — | 0.22 |

  Spain's "plumífero" peaks in **January** (0.97).
- **Southern hemisphere.** Climate-driven categories flip: "puffer jacket" peaks May–Jul in both Australia and New Zealand (5/5 seasons), and "winter boots" in Australia peaks Apr–Jun. Calendar-driven categories do not flip: in Australia, Halloween costumes still peak in October and Christmas decorations in November [1]. NOAA defines the meteorological seasons for the northern hemisphere and notes the astronomical seasons are reversed in the south [14].
- **Northern markets differ from each other too:**
  - Costumes: "Kostüm" in Germany and "disfraz" in Spain peak in Jan–Feb (Karneval/Carnaval), with a smaller October bump. The US and Romania peak in October.
  - School bags: "Schulranzen" in Germany peaks Jan–Feb, with a second rise in Jul–Aug. "Ghiozdan" in Romania peaks in August.
- **China:**
  - Google has 1.41% of search share in China (StatCounter, Aug 2026) [17], so Google Trends is not a valid demand measure there. My attempt to query China was also rate-limited.
  - I found no primary Xianyu data on category seasonality.
  - The China Meteorological Administration uses one national temperature threshold for the start of winter. It says timing varies with latitude, altitude, terrain and distance from the sea, and that "有时南方城市比相对北方城市入冬早也是可能的" (a southern city can sometimes enter winter before a more northern one) [15][16].
  - A single national window can't be defended, so China should get no hint.

### 3. A free-text model claim per item cannot be supported; a curated, cited table can

a. **The model has no inputs for it.** It gets no date and no region (`main.py:1676-1685`). The existing `demand` field already shows this problem: it asks for "how sought-after this is right now" (`prompts.py:128`), and the app displays the answer as "Demand high" (`ResultView.swift:1623`), yet the model doesn't know what "now" is.

b. **Nobody can check it.** Each free-text window is a new claim with no source, and it can't be verified per scan. That is exactly the "fabricated specificity" the v2 prompt's honesty rules are there to prevent.

c. **A presence gate rewards fabrication.** In the planned gold set (`EVALUATION.md`), electronics, books, home, collectibles, furniture and toys make up 400 of 1,000 records, and much of the clothing isn't seasonal either. A correct model returns `null` for most items. An 80% presence target pushes it to answer "year-round", which is itself a claim no source supports.

d. **It can't be localized.** Model text is English. A month range the app computes itself can be localized with `DateFormatter` month symbols.

e. **It would quietly lower confidence scores.** If the new field is added to `EXPECTED_OPTIONAL_FIELDS` (`valuation.py:49`), the correct `null` for a non-seasonal item lowers the `completeness` signal (`confidence.py:231-235`: weight 0.05, and the "analysis is complete" text only appears at ≥0.85). Keep the field out of that list.

**What a table gives you instead:**
- one source per cell;
- changes go through review;
- it knows the region;
- it can be regenerated and checked for drift, the same pattern as `tools/build_xcstrings.py`.

### 4. Concrete recommendation for #92

**Backend.** Add a prompt v3 that can be switched on by environment variable, alongside v2. It adds one field:

```
"season_class": "One of: winter_outerwear, winter_boots, snow_sports, swimwear, halloween_costume, carnival_costume, christmas_decor, school_bag — or null if the item is not strongly seasonal"
```

- Validate it with `_enum`.
- Don't add it to `EXPECTED_OPTIONAL_FIELDS`.
- No dates and no prose from the model. This is identification, which the prompt is already built for.

**iOS.**
- **Table:** a Swift table mapping each class and region code to a month range, plus a source string and the retrieval date.
- **Region:** read on the device with `Locale.current.region`. Nothing new leaves the phone, which keeps the principle in `NotificationManager.swift:16-18` and needs no privacy-label change. A region missing from the table shows nothing.
- **Result screen (Pro):** one row with the demand window for the user's region.
- **My Flips:** unlisted items get a badge from about 6 weeks before the window opens, based on Poshmark's 6–8-week lead [6]: "Demand usually rises in Nov".
- **Notifications:** create a new `season` category with its own toggle.
  - Don't reuse `ledger`: that category means "listed → +14 days, did it sell?". Its copy and its per-day grouping assume that (`NotificationManager.swift:24`, `404-407`).
  - Rank it below `ledger` under the one-per-day cap.
  - Send it once per item per season.

**Which copy is safe:**

| Copy | Verdict | Why |
|---|---|---|
| "Best time to sell / list: Sep–Dec" | Don't use | It claims an optimum no source measures, and the window is wrong for most markets |
| "Prices peak in…", "Sells for more in…", "Sells faster in…" | Don't use | Nothing measures secondhand price or speed by month |
| "Tends to sell better in…" | Borderline | It implies sell-through, which only qualitative platform advice supports |
| "Demand usually peaks Nov–Dec (UK)" / "Most searched for in Nov–Dec in the UK" | Safe | Measured, and specific to the region |
| Badge: "Demand rises in Nov" | Safe | Same basis |

- Footnote under the safe copy: "Based on Google search interest, 2021–2026. Not a price forecast. Data source: Google Trends". Google requires that attribution [2].
- Every string goes in `ios/Localization/App.json` in all five languages, with month names from `DateFormatter`.

**How to validate it.** A presence check is not enough, and it rewards the wrong behaviour.

1. **The table.**
   - Build it with a script from Trends CSV exports: either the manual export from the web interface, or the official Google Trends API alpha [4]. Commit the exports with their retrieval date.
   - Put the thresholds in code: window at or above 0.70 of peak, stable in at least 4 of 5 seasons, strong at 3× or more.
   - In CI, diff the Swift table against the generated one.
   - Re-pull every year, so a window that moves shows up in review.
2. **The classifier.**
   - Label about 150 photos. No sale prices are needed.
   - Include hard negatives: fleece versus puffer, rain shell versus down jacket, joggers versus ski pants, dress versus costume, a Christmas jumper.
   - Gate on two numbers: precision of non-null answers at 95% or better, and false positives on non-seasonal items at 2% or less.
   - This replaces #92's "≥80% presence" criterion.
3. **Real outcomes, later.**
   - My Flips already stores `listedDate` and `soldDate` (`ScanResult.swift:30,33`).
   - Once there are enough items, compare days-to-sell for items listed inside and outside the window, per class.
   - Until that is measured with a stated sample size, make no "sells faster" claim anywhere. This follows `EVALUATION.md`'s rule that every number is either measured or labelled.

**SEO fix for `build_seo.py:39,41,44`.**
- Remove "prices peak" and "best offers".
- Replace line 44's answer with: "Search interest for the Nuptse peaks in November and December in the US and UK (Google Trends, 2021–2026), so listing from October puts it in front of that demand."
- Replace "strong year-round demand" in the lede.

---

## Proposed category table (Google Trends [1], 5 seasons from Sep 2021 to Aug 2026)

Each cell reads: window at or above 0.70 of peak, then (seasons where the peak fell in the window, peak-to-trough ratio).
- "none" = measured, but too weak or not a single window, so no hint.
- "—" = not measured.

| season_class | US | GB | DE | ES | RO | AU / NZ | CN |
|---|---|---|---|---|---|---|---|
| winter_outerwear | Nov–Dec (5/5, 11×) | Nov–Dec (5/5, 9.7×) | Oct–Dec (5/5, 12×) | Nov–Jan (5/5, 24×) | Nov–Dec (5/5, 118×) | May–Jul (AU 5/5, 5.2×; NZ 5/5, 4.1×) | no hint |
| winter_boots | Nov–Dec (5/5, 15×) | — | Nov (4/5, 55×) | — | Oct–Dec (5/5, 9.6×)¹ | Apr–Jun (AU 4/5, 4.9×)² | no hint |
| snow_sports | Dec–Feb (5/5, 3.6×) | — | Dec–Feb (5/5, 8.6×)³ | — | — | none (AU 2.0×, two peaks) | no hint |
| swimwear | May–Jul (5/5, 3.9×) | — | May–Jul (5/5, 3.5×) | Jun–Jul (5/5, 6.7×) | Jun–Jul (5/5, 8.3×) | none (AU 1.9×) | no hint |
| halloween_costume | Oct (5/5, 62×) | Oct (5/5, 52×) | — | — | Oct (5/5, trough ≈0) | Oct (AU 5/5, 49×) | no hint |
| carnival_costume | — | — | Jan–Feb (5/5, 10×)⁴ | Feb (4/5; Jan–Feb 5/5, 9×)⁵ | — | — | no hint |
| christmas_decor | Nov (5/5, 48×) | Nov (5/5, 60×) | Nov (5/5, 407×) | — | — | Nov (AU 5/5, 43×) | no hint |
| school_bag | none (2.4×) | — | none (two windows: Jan–Mar and Jul–Aug, 3.0×) | none (2.0×) | Aug (5/5, 4.9×) | — | no hint |
| bicycles (excluded) | none (1.9×) | — | none (2.7×) | none (1.3×) | none (3.1× but Mar–Aug, too wide to act on) | — | — |

**Keywords used:**

| Category | US | GB | DE | ES | RO | AU / NZ |
|---|---|---|---|---|---|---|
| Winter outerwear | puffer jacket | puffer jacket | Daunenjacke | plumifero | geaca puf | puffer jacket |
| Winter boots | winter boots | — | Winterstiefel | — | cizme | winter boots (AU) |
| Snow sports | skis | — | Ski | — | — | skis (AU) |
| Swimwear | swimsuit | — | Badeanzug | bañador | costum de baie | swimsuit (AU) |
| Halloween costume | halloween costume | halloween costume | — | — | costum halloween | halloween costume (AU) |
| Carnival costume | — | — | Kostüm | disfraz | — | — |
| Christmas decorations | christmas decorations | christmas decorations | Weihnachtsdeko | — | — | christmas decorations (AU) |
| School bag | backpack | — | Schulranzen | mochila | ghiozdan | — |
| Bicycles | bike | — | Fahrrad | bicicleta | bicicleta | — |

Brand check with "nuptse": the US, GB and DE all peak in Nov–Dec.

**Notes on the table:**
1. "cizme" means boots in general, not only winter boots.
2. The Australian series has 11 zero weeks, so volume is low.
3. "Ski" in German is ambiguous.
4. "Kostüm" also means a women's skirt suit. Use the Trends *topic* rather than the keyword before shipping.
5. Spain: January is 0.62 and there is a second rise in October (0.68).

The table has 8 classes: winter outerwear, winter boots, snow sports, swimwear, Halloween costumes, carnival costumes, Christmas decorations and school bags. Bicycles were measured and excluded. The model returns `null` for everything else.

---

## Risks

- **Demand is not price.** In-season demand arrives together with more sellers listing, so the net effect on price is unmeasured. The copy must stay on demand.
- **Search is an imperfect stand-in.** It includes people shopping for new items. Trends is a sample and is rescaled per query [3], and keywords can be ambiguous (Kostüm, bike). Use topics, and pull more than once.
- **Terms of use.**
  - Attribution is required [2].
  - For this research I used the JSON endpoint behind the Trends web page, not an official API. `robots.txt` disallows crawling `/explore?` [5].
  - Before shipping, regenerate the table from manual CSV exports or through the API alpha [4], and confirm commercial use is allowed. Given the Discogs/Reverb precedent in this repo, don't scrape anything in production.
- **Region is not always the market.** Some people sell across borders on Vinted, and some travel. Showing the region in the copy lets the user judge it.
- **Weather and staleness.** A mild autumn shifts the window, and five seasons only capture so much. Refresh the table every year.
- **China and uncovered regions get nothing.** China was 12% of installs. An absent hint is the honest outcome.
- **Unknown value.** Whether a hint like this drives Pro conversion is unmeasured. Don't claim it on the paywall.

---

## Sources (all accessed 2026-09-26)

1. Google Trends, weekly search interest 2021-09-01 to 2026-08-31, for the keywords and countries listed above; pulled 2026-09-26. Data source: Google Trends. https://trends.google.com/trends/
2. Google Trends Help, "Export, embed, and cite Trends data". https://support.google.com/trends/answer/4365538
3. Google Trends Help, "FAQ about Google Trends data" (sampling; 0–100 scaling). https://support.google.com/trends/answer/4365533
4. Google Search Central, "Get early access to the Google Trends API alpha". https://developers.google.com/search/apis/trends
5. trends.google.com robots.txt. https://trends.google.com/robots.txt
6. Poshmark Blog, "Posh Tip: Seasonal Selling" (2014-07-24). https://blog.poshmark.com/2014/07/24/posh-tip-seasonal-selling/
7. Kleinanzeigen Magazin, "Was bringt viel Geld, wenn ich es verkaufe?" (updated 2024-12-21). https://themen.kleinanzeigen.de/magazin/verkaufen/was-bringt-viel-geld-wenn-ich-es-verkaufe/
8. Wallapop, "¿Qué han buscado y comprado los españoles en Wallapop durante 2024?" (2024-12-10). https://about.wallapop.com/que-han-buscado-y-comprado-los-espanoles-en-wallapop-durante-2024/
9. Wallapop, "Estos son los productos que se venderán más rápido en Wallapop durante esta primavera" (2025). https://about.wallapop.com/estos-son-los-productos-que-se-venderan-mas-rapido-en-wallapop-durante-esta-primavera/
10. eBay Export, "Seasonal guide: Fall sales period". https://export.ebay.com/in/resources/seasonal-guide-for-ebay-sellers/fall/
11. eBay Seller Center, "Seasonal playbook" (US). https://www.ebay.com/sellercenter/resources/seasonal-playbook
12. eBay Export, "Seasonal guide for eBay sellers". https://export.ebay.com/en/resources/seasonal-guide-for-ebay-sellers
13. ThredUp Newsroom, "ThredUp's 13th annual Resale Report" (2025-03-19). https://newsroom.thredup.com/news/thredup-13th-resale-report
14. NOAA NCEI, "Meteorological Versus Astronomical Seasons". https://www.ncei.noaa.gov/news/meteorological-versus-astronomical-seasons
15. China Meteorological Administration, "各地入冬标准如何确定，是否存在不同" (2023-12-25). https://www.cma.gov.cn/wmhd/gzly/cjwt/202312/t20231225_5972654.html
16. China Meteorological Administration, "北京'官宣'开启冬天！如何判定'入冬'？" (2024-11-12). https://www.cma.gov.cn/2011xwzx/2011xmtjj/202411/t20241112_6688112.html
17. StatCounter, "Search Engine Market Share China" (Aug 2026). https://gs.statcounter.com/search-engine-market-share/all/china
18. Kellogg Insight, "Buying and Selling Seasonal Goods" (Soysal & Krishnamurthi, Marketing Science 2012). https://insight.kellogg.northwestern.edu/article/buying_and_selling_seasonal_goods
19. VintiePlus, "How to Spot Seasonal Trends on Vinted" (example of an unsourced statistic). https://vintieplus.com/blog/spot-seasonal-trends-vinted/
20. FTC, "Policy Statement Regarding Advertising Substantiation" (1984-11-23). https://www.ftc.gov/legal-library/browse/ftc-policy-statement-regarding-advertising-substantiation