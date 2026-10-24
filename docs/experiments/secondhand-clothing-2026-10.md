# Second-hand clothing eval: first results, 2026-10-23 → 10-24

What the first runs of `python -m eval.run --split id_gold` found, and what
was done about it. The run folders are local and gitignored
(`backend/eval/data/secondhand/runs/`); this file is the copy that lasts.

## Setup

- **Items.** The 261-item dev half of `id_gold`, from the Clothing Dataset
  for Second-Hand Fashion v3 (CC-BY 4.0; `backend/eval/README.md`,
  Attribution). 260 scored in each run; one item failed (`not_resalable` at
  v2, `ai_no_price` at v2.3).
- **Server.** A local backend on gemini-2.5-flash. No Pro token, so only the
  front photo was sent and the brand photo never reached the model.
- **Photos.** As the dataset has them: studio table shots, the garment
  sideways and filling about half the frame, mostly Nordic brands. Absolute
  numbers will differ from the app's; comparing prompts on the same items is
  what this set is for.
- **Each arm is one run.** Two overlapping 20-item runs at v2 agreed on every
  brand but moved prices by $3–$5, so a difference of a few items between arms
  is not an effect.

## Baseline, prompt v2

| Measure | Result |
| --- | --- |
| Brand, exact | 11.2% |
| Brand, fuzzy | 14.6% |
| Answered "Unknown" | 53.5% |
| Category | 99.2% (nearly all are clothing) |
| Garment type | 74.6% |
| Condition, mean error | 1.07 grades |
| Condition, exact | 30.0% |
| Median scan time | 10.9 s (p90 12.9 s) |

**Brand.** A brand was named for 121 items: 30 right, 91 wrong. The badge did
not separate them. 116 of the 121 were High, and 27 of those were right
(23%); 5 were Medium (3 right); none were Low. Typical wrong answers: L.L.Bean
for Lindex, Tommy Hilfiger for Vero Moda, Old Navy for Newbody, Brooks
Brothers for Gap.

**Condition.** The grade barely follows the sorter's. Rows are the dataset's
1–5 (5 best), columns the scan's grade:

| | new | likeNew | good | used |
| --- | --- | --- | --- | --- |
| 5 | 7 | 2 | 44 | 16 |
| 4 | 16 | 2 | 29 | 13 |
| 3 | 1 | 1 | 46 | 34 |
| 2 | 2 | 0 | 19 | 23 |
| 1 | 0 | 0 | 2 | 3 |

Of the 129 items rated 4–5, 102 were graded good or used. The two scales do
not map exactly, so read this as a direction.

**Type.** Dresses were weakest at 38.5% (a short dress lying flat reads as a
long top), then T-shirts at 57.7%.

**The labels were checked.** Four mismatches were looked at by eye; the
dataset was right each time and every photo matched its item.

## Prompt v2.3 (#381, closed)

| Measure | v2 | v2.3 |
| --- | --- | --- |
| Brand, exact | 11.2% | 10.0% |
| Brand, fuzzy | 14.6% | 13.5% |
| Answered "Unknown" | 53.5% | 61.5% |
| Named a brand | 121: 30 right, 91 wrong | 100: 27 right, 73 wrong |
| Wrong brands marked High | 89 | 69 |
| Garment type | 74.6% | 74.6% |
| Condition, mean error | 1.07 | 0.96 |
| Condition, mean bias | −0.68 (too harsh) | +0.09 |
| Condition, exact | 30.0% | 28.1% |

**Brand.** 30 wrong brands became "Unknown", 12 "Unknown" items got a new
wrong brand, and no "Unknown" became right. 69 of the 73 wrong brands were
still High; most were US brands the model could not have read (Old Navy 5
times, Forever 21 4 times).

**Condition.** The better mean error hides a worse grade. The whole scale
moved up about three quarters of a grade, and the grade still barely follows
the sorter's (it rises 0.5 of a grade from sorter 2 to 5). Of the 125 items
rated 2 or 3, 58 became new or likeNew, against 4 at v2: that inflates the
price of worn items.

| | new | likeNew | good | used |
| --- | --- | --- | --- | --- |
| 5 | 10 | 34 | 24 | 1 |
| 4 | 21 | 25 | 12 | 3 |
| 3 | 6 | 35 | 30 | 10 |
| 2 | 4 | 13 | 19 | 8 |
| 1 | 0 | 1 | 4 | 0 |

The likely cause is the condition rule v2.3 inherits from v2.2 ("If you can
see no wear, grade `new` … and `likeNew` otherwise, never `good` or `used`"):
a table shot from a distance rarely shows wear. That is inferred from the
wording; a v2.2-only run would confirm it. v2.3 was not served.

## A brand check against the scan's own evidence (#382, closed)

#382 kept a brand only when the scan's `visual_evidence` named it. At v2 on the
same items: brand exact 10.8%, 105 named (29 right, 76 wrong), 74 wrong brands
still High. It dropped 16 wrong brands and 2 right ones (Nike and Lacoste,
known from a logo and a slogan, not a name).

It barely helps because the model invents the evidence. 22 photos were sent to
the model directly so its raw `visual_evidence` could be read (a free scan
blanks it). In 19 of 20 wrong answers the evidence claims a tag reading of the
wrong brand, as "certain" or "probable": "Neck label clearly shows
'Timberland'" on a blazer labelled "Classics by Bondelid"; "OLD NAVY … visible
on neck tag" where the neck print is a few unreadable pixels. Checking the
answer against the model's own evidence only checks that it agrees with
itself.

## Shipped: a clothing brand alone cannot earn High (#383)

A clothing scan that names a brand without a label photo now tops out at
Medium (`confidence.UNLABELLED_CLOTHING_BRAND_CEILING`), with the reason "the
brand was not confirmed from a label photo" (`brand_not_from_label`). A Pro
scan with its label photo can still be High; other categories are unchanged.

Partial credit for the brand signal could not do this: right and wrong brands
scored alike (medians 90 and 87), and at 0.4 credit 72 of the 89 wrong-brand
Highs stayed High. Applied to the v2 run, all 116 High answers become Medium,
the 27 right ones included. That is the trade: the badge stops promising a
brand it cannot back.

## Thinking budget, first arm (#217)

v2.1 uncapped against v2.1 with `thinking_budget` 1024, on 50 of these photos
with three repeats each, against the rule posted on #217 before the run.

| | uncapped | @1024 |
| --- | --- | --- |
| Billed output tokens, median | 1996 | 1512 (−24%) |
| Latency p50 / p95 | 10.9 / 14.0 s | 8.4 / 9.4 s |
| Scored | 48/50 | 49/50 |
| Consistency CV | 0.084 | 0.049 |
| Price shift vs uncapped | — | median 20%; 65% of items moved >10% |

**Fail.** The price shift is far over the ≤ 5% limit, and billed output fell
24% against the ≥ 30% required. Faster and steadier, but it moves prices a
lot, and whether closer to the truth needs labelled sales. 512 and 0 were not
run, to keep the Gemini credit for production.

## Open

- **Brand:** an independent reading the model cannot invent, such as the
  label photo or a separate text-only pass over the photo. Brands known from a
  logo need their own allowance.
- **Condition:** "no visible wear" should mean likeNew only when the photo is
  close enough to show wear. Needs another 261 scans.
- **Test half:** once a version beats v2 on dev.

## Reading these numbers from a run

Since this write-up, `report.md` prints the brand-per-badge table, the brands
the scan named with how often each was right, and the condition table, so a
future run shows them without hand analysis. `python -m eval.run --rescore
<run folder>` prints them for a run already on disk, with no scans sent. It
re-scores the saved answers; it does not re-run the server, so a run made
before #383 keeps the badges it was served with.
