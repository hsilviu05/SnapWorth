# Free-scan experiment: the server's record, 2026-09-02 → 10-05 (#212)

Copied on 2026-10-06 from the ops bot's `/experiment`, with Railway's
`EXPERIMENT_START_DAY=20260902` and `EXPERIMENT_END_DAY=20261005`, the day
before the 09-02 counters expired (`STATS_TTL`, 35 days). The server's
copy of these days is now deleting itself one day at a time; this file is
the copy that lasts. Values are the bot's, unedited.

```
🧪 Free-scan experiment — closed after 34 days
02 Sep → 05 Oct · lever armed — 3 first-day scans, from FREE_SCANS_FIRST_DAY=3
day      act  free   hit trial  paid
09-02      0     0     0     0     0
09-03      0     0     0     0     0
09-04      4     2     0     0     0
09-05      2     1     0     0     0
09-06      3     0     0     0     0
09-07      4     3     0     0     0 †
09-08      4     6     0     0     0
09-09      6     4     0     0     0
09-10      8     9     0     0     0 *
09-11      5     2     0     0     0
09-12      9     7     0     0     0 †
09-13      9     3     0     0     0
09-14      3     5     0     0     0
09-15      5     6     0     0     0
09-16      3     1     0     0     0
09-17      1     0     0     0     0
09-18      1     0     0     0     0
09-19      2     3     0     0     0
09-20      4     3     0     0     0
09-21      2     1     0     0     0
09-22      2     3     0     0     0
09-23      2     1     0     0     0
09-24      2     3     0     0     0
09-25      6     0     0     0     0
09-26     12    11     0     0     0 †
09-27      3     1     0     0     0
09-28      4     1     0     0     0
09-29      5     2     0     0     0
09-30      1     0     0     0     0
10-01      4     3     0     0     0
10-02      0     0     0     0     0
10-03      9     5     0     1     0
10-04     18     8     0     0     0
10-05     13     6     0     1     0
No limit hits recorded — 100 free scans in the window.
* limit hits counted from 18:29 UTC that day only — the counter shipped mid-day. Every other column is a whole day.
† 4 trial starts or purchases from before the server split them (#218): in neither column, and in the %.
```

Columns: `act` active devices, `free` free scans served, `hit` free users
refused by the server, `trial` trial starts, `paid` paid conversions and
direct purchases (split by #218 from 09-28).

## What the table can and can't say

**The `hit` column does not measure limit hits.** The app checks the
allowance itself (`ScanViewModel.swift:109`, `FreeScanCounter.hasRemaining`).
A user who has used up the day's scans is shown the paywall without
`/scan` ever being called. The server counts a hit (`auth.reserve_quota`
→ `notify.count_limit_hit`) only when the phone thought a scan was left and
the server disagreed. That's rare, so the column is zero by construction,
not because nobody ran out. The instrument for limit hits is TelemetryDeck's
`free_scan_limit_hit`, sent at that same check. #212's step 3 named it; it
is the only one that can answer the question.

**Volume is too small to separate the arms.** The whole window has 100
free scans and at most 6 trial starts or purchases: 2 split ones, plus 4
from before #218, which fall in neither column. The days with the lever off
are 09-02 to 09-06 (armed on 09-07, `docs/AUDIT-2026-09.md` A-6). Two of
them have no activity at all, which is more likely the counters' first days
than a real zero. The other three have 9 device-days. No conversion rate can
be compared across 9 device-days.

**What the server does show.** Activity rose in the last three days: 9, 18
and 13 active devices on 10-03, 10-04 and 10-05, after 1.5.1 went live on
10-02, against 1 to 6 most days before. The first two split trial starts
came on 10-03 and 10-05. Those three days sit outside the experiment window
and mix the release with everything else (the growth log's baseline note).

## What decides #212

The decision is on cost and product grounds, as #212 said it would be,
checked against TelemetryDeck. The server's numbers add nothing here.

- **TelemetryDeck, 09-10 → 09-24** (the armed window) and **09-02 → 09-06**
  (lever off): `free_scan_limit_hit` users, then `paywall_viewed` with
  `trigger=scan_limit`, then `purchase_started`. Read the counts and record
  them here with their dates.
- **Cost:** `/costs`, "given away" line. At about 100 free scans in 34 days,
  the welcome's extra scans are a few cents.
- **Then record the decision** in `quota.py`'s docstring and RUNBOOK, as
  #212 step 4 asks. Either keep `FREE_SCANS_FIRST_DAY=3`, or set it to 1
  (no welcome).
