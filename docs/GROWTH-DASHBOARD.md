# Growth dashboard

What to read every Monday, where, with which filter, and from which build the
number exists (#205). Four sources, and nothing joins them but this page and
its log:

- **App Store Connect** App Analytics: the store and installs.
- **Vercel Web Analytics**: the site, on every page since #195.
- **TelemetryDeck**: what people do in the app. Signal names and parameters
  are `AnalyticsEvent` in `ios/SnapWorth/Services/Analytics.swift`, the only
  place they are defined; `TelemetryDeckAnalytics.swift` sends each as
  `TelemetryDeck.signal(name, parameters:)`.
- **The bot's `/week`**: the server's own counters.

Every event and parameter named here was checked against `Analytics.swift` on
`main` at `02c9029`. The exceptions are marked where they appear and listed
under [Named in #205, not in the code](#named-in-205-not-in-the-code).

**Counts are floors.** TelemetryDeck respects the in-app opt-out, and ASC
counts only users who share analytics with developers. Weekly numbers are in
the tens: compare ratios, and read four-week trends rather than one week.

## First build: how it is dated

A row's *first build* is the earliest build that can send it. The commit that
introduced a name or parameter is found with
`git log -S'"<name>"' origin/main -- ios/`, and then placed against the build
bumps. A `chore: <version>, build <n>` commit is when the number changed, not
when the archive was cut, which is later and from whatever `main` was then
(CLAUDE.md). So:

- **≤ N** means the commit is an ancestor of build N's bump, so build N has
  it. An earlier build may have it too, if that build's archive was cut after
  the commit merged. Organizer's archive date is the only record of that, and
  no `RELEASE-NOTES-*.md` records an archive commit (they state lower bounds:
  "archive from `main` at … or later").
- **1.5.1 (21)** means the commit reached `main` after 1.5.0's bump
  (`435cba6`, 2026-09-25 19:37 +03:00). None of these is in 1.5.0 (20): 1.5.0
  was already live on 2026-09-26 (`RELEASE-NOTES-1.5.0.md`, written in
  `b0859d4`), and every one of them reached `main` on 2026-09-27. They ship in
  the next build, 1.5.1. Its bump is not on `main` yet at `02c9029`; 21 is
  the number the sequence gives.

| Build | Bump commit | Bump date |
|---|---|---|
| 1.1.0 (1) | `438c411` | 2026-07-21 |
| 1.1.1 (3) | `bd4f50e` | 2026-07-22 |
| 1.2.0 (5) | `4823a66` | 2026-07-28 |
| 1.3.5 (12) | `0a286ec` | 2026-09-04 |
| 1.4.1 (17) | `a84d1d9` | 2026-09-14 |
| 1.4.2 (18) | `e81d0ec` | 2026-09-20 |
| 1.5.0 (20) | `435cba6` | 2026-09-25 |
| 1.5.1 (21) | not yet bumped | |

One case is open. The first-run funnel (#157, `af263a7`) merged on 2026-09-16
at 17:56 +03:00, after 1.4.1's bump, and 1.4.1 (17) was archived that same day.
It is in 1.4.2 (18); whether it is in 1.4.1 (17) depends on the archive's hour
(`RELEASE-NOTES-1.4.2.md`, *Scope*). The baseline week runs on 1.5.0, which has
it either way.

**The baseline week (2026-09-28 → 10-04) is on 1.5.0 (20).** Any row whose
first build is 1.5.1 is empty for it by construction, and two steps of the
day-0 funnel need a different filter on it (below).

## Store — App Store Connect

Read in ASC → Apps → SnapWorth → App Analytics, with the date range set to the
week. The owner saves three views there (#205 step 2): *Source*, *Territory*
and *Campaign*. These are store metrics, so no app build gates them; what
1.5.1 changes is the listing they measure (#202).

| Metric | Source and filter | First available | Read it in |
|---|---|---|---|
| Impressions | App Analytics *Impressions*, all sources, all territories. Use the same measure every week | Always | View *Source* |
| Product page views | *Product page views*, all sources | Always | View *Source* |
| Tap-through | Page views ÷ impressions. This is `app_store_listing.md`'s "2.1% tapped through" (six weeks to the subtitle change) | Always | Computed |
| Conversion | First-time downloads ÷ page views. This is the listing file's "18.6% of those installed". ASC's own *Conversion rate* uses another denominator, so log this one to compare with the baseline sentence | Always | Computed |
| First-time downloads by source type | *First-time downloads*, split by *Source type*: App Store Search, App Store Browse, Web Referrer, App Referrer, and whatever else ASC lists. snapworth.eu is a web referrer until its links carry a campaign | Always | View *Source* |
| First-time downloads by territory | Split by territory: **US**; **DE + AT + CH** (one `de` listing, `app_store_listing.de.md`); **ES + the Latin American storefronts**, summed (one `es` listing covers about twenty, `app_store_listing.es.md`); **RO**; **CN** (mainland, already distributed, `app_store_listing.zh-Hans.md`) | Always. Localized listings arrive with 1.5.1 (#202); on 1.5.0 every storefront showed the English page (checked 2026-09-26, `RELEASE-NOTES-1.5.0.md`) | View *Territory* |
| First-time downloads by campaign | Sources → campaigns, one row per `ct` in the [campaign table](#campaigns) | **Pending `pt`:** from the day PR #242 (#204) deploys. Before that no link on the site carries a campaign | View *Campaign* |
| Trials and paid conversions | ASC's subscription figures for the yearly plan, the only one with the 3-day free trial: trials started, and trials converted to paid | Always | ASC subscription reports; cross-check with `/week`'s *New subscriptions* |

## Web — Vercel Web Analytics

Read in Vercel → the `website` project → Analytics, range set to the week.
Every page loads `/_vercel/insights/script.js` since #195 (`69ee283`, merged
2026-09-27); before that only `/` did. No app build is involved.

| Metric | Source and filter | First available | Read it in |
|---|---|---|---|
| Visitors, `/` | *Pages*, path `/` | Before #195 | Pages panel |
| Visitors, `/worth/*` | Paths `/worth` (the hub) and `/worth/<slug>` (16 guides), summed | 2026-09-27 (#195) | Pages panel |
| Visitors, `/guess` | Path `/guess` | 2026-09-27 (#195) | Pages panel |
| Visitors, `/i/[code]` | Path `/i/[code]`. `invite.html` rewrites every invite path to this before it is sent (`invite.html:119-127`), so no code reaches Vercel | 2026-09-27 (#195) | Pages panel |
| Visitors, `/support` | Path `/support` | 2026-09-27 (#195) | Pages panel |
| Referrers | *Referrers* panel, overall and filtered to each page group above | 2026-09-27 (#195) | Referrers panel |
| Countries | *Countries* panel | 2026-09-27 (#195) | Countries panel |

The site sends no click events, so a tap through to the App Store is not a
Vercel number. The install side of a page is its campaign in ASC.

## Day-0 funnel — TelemetryDeck

This and the three sections after it are read in TelemetryDeck, on the
*Weekly growth* dashboard the owner builds from these rows (#205 step 2).

A TelemetryDeck funnel, in order, over the week, restricted to one app version
with the SDK's default payload key `TelemetryDeck.AppInfo.version` (`1.5.0` for
the baseline). Count users, not signals. A funnel step counts only users who
passed the steps before it, so step 1 already limits it to new installs.

"Day 0 is a parameter, not a second set of events" (`Analytics.swift:16-21`):
`is_first`. Only some of the six steps carry it, and 1.5.0 sends it wrong on one
of them and not at all on another. The filter therefore differs by build:

| Step | Event and filter, 1.5.0 (baseline) | Event and filter, 1.5.1 on | First build |
|---|---|---|---|
| 1 | `onboarding_started`, no filter. It has no `is_first`, and needs none: onboarding shows only until it is completed (`hasCompletedOnboarding`), so only a new install sees it | same | ≤ 1.4.2 (18) |
| 2 | `onboarding_completed`, no filter; `via` = `finished` or `skipped` splits it | same | ≤ 1.4.2 (18) |
| 3 | `scan_started` with `is_first` = `true` | same | Event ≤ 1.1.0 (1); `is_first` ≤ 1.4.2 (18) |
| 4 | `scan_result_shown` with `is_first` = `true` | same | ≤ 1.4.2 (18) |
| 5 | `paywall_viewed` with `trigger` = `onboarding`. **Not `is_first`:** until 1.5.1 it is `false` for nearly every first-run paywall, because both paywalls a new user meets open after the first scan is recorded (`3a389c8`, which names `trigger=onboarding` as the way to recover it) | `paywall_viewed` with `is_first` = `true` | Event and `trigger` ≤ 1.1.0 (1); a correct `is_first` from 1.5.1 (21) |
| 6 | `purchase_completed`, no filter: 1.5.0's purchase events carry only `product_id`. The funnel's earlier steps keep it to new users | `purchase_completed` with `is_first` = `true` | Event ≤ 1.1.0 (1); `is_first` from 1.5.1 (21) (`3a389c8`, #194) |

The launch funnel the enum documents (`Analytics.swift:11-14`) also has
`app_opened` first and `paywall_dismissed` last; neither is a step here.

## Return and paywall

| Metric | Source and filter | First build | Read it in |
|---|---|---|---|
| Free limit reached | `free_scan_limit_hit`, users per week (no parameters). Sent from the Scan tab and Thrift Flip, and from Haul in 1.5.1 | ≤ 1.1.0 (1) | TelemetryDeck |
| Day-1 return | **Not an app event.** TelemetryDeck's retention over the SDK's own `TelemetryDeck.Session.started`, which it sends at launch and on every return to the foreground (`TelemetryDeckAnalytics.swift`). Users whose `TelemetryDeck.Acquisition.firstSessionDate` falls on day D, and who start a session on D+1. `app_opened` would undercount: it fires on a cold launch only (`AnalyticsBootstrap.start`) | The SDK, pinned at 2.14.1 since `4c483cc`: every build ≤ 1.1.0 (1) | TelemetryDeck retention |
| Paywall to purchase, by trigger | `paywall_viewed` against `purchase_completed`, both split by `trigger`: `onboarding`, `scan_limit`, `upgrade_button`, `settings`, `ledger_history`, `ledger_export`, `snap_sell`, `portfolio_trend`, `valuation_detail`, `trends`, `add_tag`, and `haul` from 1.5.1 | `paywall_viewed{trigger}` ≤ 1.1.0 (1). `purchase_completed{trigger}` from 1.5.1 (21) (`f1d6b7f`, #239, "S"). On 1.5.0, `paywall_viewed{trigger}` minus `paywall_dismissed{trigger}` (≤ 1.4.2 (18)) approximates purchases per trigger, because a purchase closes the paywall without `paywall_dismissed`. It is an upper bound: a paywall closed by killing the app counts too | TelemetryDeck |
| Return loop (S) | `reminder_opt_in` by `source`: `scan_spent` (the "Remind me" beside the next free scan) or `settings`. Then `notification_scheduled` and `notification_opened` with `category` = `freeScan` | `reminder_opt_in` from 1.5.1 (21) (`65e04c7`, #239). The two notification events with `category=freeScan` ≤ 1.3.5 (12) | TelemetryDeck |

## Sharing loops

The leading half of each loop. The install half is in the
[channel scorecard](#channel-scorecard).

| Metric | Source and filter | First build | Read it in |
|---|---|---|---|
| Result card shared | `share_card_shared` by `activity_type`, iOS's raw activity type (such as `com.apple.UIKit.activity.Message`), which is absent when iOS gives none. It counts completed shares only, from one share sheet that carries both the result card and the guess story. `share_card_opened` is the tap before it | ≤ 1.1.0 (1) | TelemetryDeck |
| Guess story shared | `guess_card_shared` by `style`. It fires when the guess story is chosen, **before** the share sheet: a choice, not a completed share. The only value sent is `pair` (`ResultView.swift:236`); the `guess` and `reveal` in the enum's comment have no call site | ≤ 1.3.5 (12) | TelemetryDeck |
| Haul shared | `haul_shared`, completed shares | 1.5.1 (21) (`431c07c`, #187) | TelemetryDeck |
| Month shared | `ledger_month_shared`, completed shares | ≤ 1.1.0 (1) | TelemetryDeck |
| Invite shared | **`referral_shared` is not in the code.** Read `referral_share_opened`: the Share invite tap, when the sheet opens, not a completed share. 1.5.0 sends the same tap under the old name `referral_shared` (RUNBOOK §18: count both while 1.5.0 is installed). Both are silent until referrals are switched on (`REFERRALS_ENABLED` is unset in production) | `referral_share_opened` from 1.5.1 (21) (`0074d2a`, #236) | TelemetryDeck |

## Feature pull

| Metric | Source and filter | First build | Read it in |
|---|---|---|---|
| Thrift Flip verdicts | `thrift_flip_calculated` by `verdict`: `profit` or `loss` | ≤ 1.2.0 (5) | TelemetryDeck |
| Hauls by size | `haul_completed` by `items`: `1`, `2-4`, `5-9`, `10-14`, `15+`. **Only events without a `revised_from` key.** A haul that grows after "Keep scanning" reports again with `revised_from`, and counting those would count one haul twice | `haul_completed` from 1.5.1 (21) (`431c07c`, #187); `revised_from` too (`a301b41`) | TelemetryDeck |
| Listings generated | `listing_generated` by `marketplace`. It is the one Snap → Sell signal 1.5.0 sends | ≤ 1.2.0 (5) | TelemetryDeck |
| Listings copied | `listing_copied` by `marketplace`: `ebay`, `poshmark`, `mercari`, `depop`, `facebook`, `vinted`, `olx`, `xianyu`, `kleinanzeigen`, or `draft` for the plain draft on every result | 1.5.1 (21) (`b4f7820`, #193) | TelemetryDeck |
| Listings shared | `listing_shared` by `marketplace`, completed shares | 1.5.1 (21) (`b4f7820`) | TelemetryDeck |
| Marketplace opened | `marketplace_opened` by `marketplace` ("Open <marketplace>" under a listing) | 1.5.1 (21) (`b4f7820`) | TelemetryDeck |
| Widget opens | `widget_opened` by `source`: `quick_scan`, `haul`, `haul_scan`, `lock_haul`, `recent_finds`, `scans_left`, `month_profit`, `live_activity`, `dynamic_island`, `control` | 1.5.1 (21) (`b4f7820`) | TelemetryDeck |
| Widgets placed | `widgets_installed`, once a day per device: `count` (`0`, `1`, `2-3`, `4+`) and `kinds` (the placed widget kinds, comma-joined) | 1.5.1 (21) (`b4f7820`) | TelemetryDeck |
| Listed, then sold | `ledger_item_marked_listed` against `ledger_item_marked_sold` | Listed from 1.5.1 (21) (`b4f7820`); sold ≤ 1.1.0 (1) | TelemetryDeck |

The other names #205 gives as new in 1.5.1 are health signals rather than
growth: `review_prompt_requested` (`1947a79`, #193) and
`entitlement_sync_failed` by `reason` (`ea2c8f2`, #194). Both are 1.5.1 (21).

## Server — `/week`

| Metric | Source and filter | First available | Read it in |
|---|---|---|---|
| `/week` as it is | Scans (free · Pro), Failed, Active user-days, New subscriptions and Gemini spend, each against the week before. It covers the seven UTC days ending yesterday (`_weekly_text`, `backend/notify.py:2926`). *New subscriptions* counts each Apple `originalTransactionId` once, whichever of the client sync or Apple's notification sees it first | Server, deployed | Telegram: the report sent with Monday's digest (`WEEKLY_REPORT_WEEKDAY = 0`), or `/week` |
| Gemini spend per paid subscription | **No counter yet.** Spend is not split by tier; that is #219. Leave the column empty until it lands | Not available | — |
| Referral conversions | The daily digest's *Referrals:* line: claimed, redeemed at Apple, rewarded, paid after the free week (`REFERRAL_STEPS`, `notify.py:1006`). It is not in `/week`, so sum the seven digests | Server; silent until referrals are switched on | Telegram, daily digest |

**The counters behind `/week` expire after 35 days** (`STATS_TTL`,
`notify.py:104`). A week that nobody writes into the log below is gone five
weeks later.

## Channel scorecard

For each channel: the leading indicator (interest), the install signal
(credit), and where each is read.

| Channel | Leading indicator | Install signal | Read in |
|---|---|---|---|
| ASO, per locale (US; DE/AT/CH; ES + Latin America; RO; CN) | Impressions and tap-through in that territory | First-time downloads in that territory, source type App Store Search and App Store Browse | ASC, views *Territory* and *Source*. The localized listings are 1.5.1's (#202) |
| Homepage `/` | Visitors to `/`, and its referrers | Campaigns `site_nav`, `site_hero`, `site_tag`, `site_plan_free`, `site_plan_yearly`, `site_plan_monthly`, `site_cta`, `site_mobile_cta`. Pending `pt`: until then only ASC's Web Referrer (snapworth.eu), which cannot tell pages apart | Vercel; ASC view *Campaign* |
| /worth | Visitors to `/worth/*`, and search referrers | Campaigns `worth_<slug>` and `worth_hub`, pending `pt` | Vercel; ASC view *Campaign* |
| /guess | Visitors to `/guess` | Campaign `guess_web`, pending `pt` | Vercel; ASC view *Campaign* |
| Share cards | `share_card_opened` → `share_card_shared`; `guess_card_shared`; `haul_shared` (1.5.1); `ledger_month_shared` | **None yet.** Every card's QR encodes the bare `Config.appStoreURL`, so an install from a card looks like any other. Campaign values per card are #221 (1.5.2) | TelemetryDeck; ASC view *Campaign* after #221 |
| Referrals | `referral_share_opened` (1.5.1; `referral_shared` on 1.5.0); visitors to `/i/[code]`; `referral_code_accepted` (1.5.1) | Server counters: claimed, redeemed at Apple, then paid after the free week. Campaign `invite_page`, pending `pt` | TelemetryDeck; Vercel; the daily digest; ASC view *Campaign*. All silent until the referral launch (RUNBOOK §18) |
| Social bios | Profile visits, in each network's own insights | Campaigns `ig_bio`, `tiktok_bio`, `x_bio`: links the owner generates in ASC with the same `pt` | ASC view *Campaign* |

## Campaigns

The `ct` values, and the page and button each is on, are the **campaign
table in `website/README.md`**, which PR #242 (#204) adds. **Pending `pt`:**
that PR is a draft until the owner posts App Store Connect's provider token on
#204, and until it merges and deploys, `website/README.md` does not exist on
`main` and no link on the site carries a campaign. The dashboard reads that
table by name and does not copy it, so a new `/worth` guide's `worth_<slug>`
appears there first.

## Named in #205, not in the code

Checked against `Analytics.swift` at `02c9029`. No iOS code was added for
these; each is a decision for its own issue.

- **`referral_shared`.** No such event. It was renamed `referral_share_opened`
  in `0074d2a` (#236, 1.5.1), and it is a tap that opens the share sheet. No
  event records a completed invite share. 1.5.0 still sends `referral_shared`
  for the same tap, once referrals are on.
- **`is_first` on `onboarding_started` and `onboarding_completed`.** #205
  filters the whole day-0 funnel on `is_first=true`, but these two never carry
  it. Applied to them, the filter empties the first two steps. They need no
  filter: onboarding shows only until it is completed, so only on a new
  install. The events that carry `is_first` are `scan_started`,
  `scan_result_shown`, `scan_failed`, `paywall_viewed`, and from 1.5.1
  `purchase_started` and `purchase_completed`.
- **Day-1 return.** No app event measures it. It is TelemetryDeck's retention
  over the SDK's own `TelemetryDeck.Session.started`, a signal that
  `Analytics.swift` does not define.
- **Gemini spend per paid subscription.** No server counter splits spend by
  tier (#219).

Present in the code but **not in the 1.5.0 baseline build**: `widget_opened`,
`widgets_installed`, `listing_copied`, `listing_shared`, `marketplace_opened`,
`ledger_item_marked_listed`, `haul_completed` (with `items` and
`revised_from`), `haul_shared`, `review_prompt_requested`,
`entitlement_sync_failed`, `reminder_opt_in`, `referral_share_opened`,
`referral_code_accepted`, `referral_reward_opened`, `trigger=haul`, and
`is_first` and `trigger` on `purchase_started` and `purchase_completed`.
`paywall_viewed`'s `is_first` is in 1.5.0 but reads false there for nearly
every first-run paywall.

To check this page against a later `main`:

```sh
for e in onboarding_started onboarding_completed scan_started scan_result_shown \
         paywall_viewed paywall_dismissed purchase_completed free_scan_limit_hit \
         reminder_opt_in notification_scheduled notification_opened \
         share_card_opened share_card_shared guess_card_shared haul_shared \
         ledger_month_shared referral_share_opened referral_code_accepted \
         thrift_flip_calculated haul_completed listing_generated listing_copied \
         listing_shared marketplace_opened widget_opened widgets_installed \
         ledger_item_marked_listed ledger_item_marked_sold \
         review_prompt_requested entitlement_sync_failed; do
  grep -q "return \"$e\"" ios/SnapWorth/Services/Analytics.swift || echo "missing: $e"
done
```

## Weekly log

**Owner, every Monday:** one dated row for the seven days that ended the day
before, beside that morning's `/week`. The first is the **baseline**:
2026-09-28 → 10-04, on 1.5.0, logged on **Monday 2026-10-05**, before 1.5.1's
phased release reaches most users. Set every tool's range to those seven days.
ASC's figures can arrive a day or two late. If 10-04 is not in yet on Monday,
fill the ASC cells when it is and say so in *Notes*.

Leave a cell empty when its row's first build is later than the week's build.
Write `0` only for a real zero. Campaign cells stay empty until PR #242
deploys.

Column key: **Impr.**, **Views**, **TT** (tap-through), **Conv.** (downloads ÷
views) and **DL** (first-time downloads) are ASC. **DL by source** is Search /
Browse / Web referrer / App referrer. **DL by territory** is US / DACH /
ES+LatAm / RO / CN. **Web** is visitors to `/` · `/worth/*` · `/guess` · `/i/[code]`
· `/support`. **Day-0** is the six funnel steps, users. **Limit** is
`free_scan_limit_hit` users. **D1** is day-1 return. **`/week`** is scans
(free · Pro) · failed · user-days · new subs · Gemini $.

| Week | Logged | Build live | Impr. | Views | TT | Conv. | DL | DL by source | DL by territory | Trials / paid | Campaigns (top 3) | Web | Day-0 | Limit | D1 | `/week` | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-28 → 10-04 (baseline) | 2026-10-05 | 1.5.0 (20) | | | | | | | | | | | | | | | |
