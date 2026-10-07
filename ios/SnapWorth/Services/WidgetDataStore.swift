import ActivityKit
import Foundation
import SwiftData
import SwiftUI
import WidgetKit

// ── Store ─────────────────────────────────────────────────────────────────────

enum WidgetDataStore {
    static let appGroupID = WidgetBridge.appGroupID
    static let haulKey = WidgetBridge.haulKey

    /// The month's ledger figures, from one pass over the library.
    ///
    /// Split out because this is the rule the widget's three captions turn on
    /// and `writeHaul` itself cannot be tested — it writes to the App Group
    /// and reloads timelines. The rule itself is `LedgerMath`'s — the one My
    /// Flips' header, its bars and its share card use — so the widget cannot
    /// count a month the app does not.
    ///
    /// `flips` counts only the sales that could be priced — `realizedProfit`
    /// is nil without a paid price — because "$214 from 6 flips" has to be
    /// true of the same six. `sold` counts every sale. They differ by exactly
    /// the sales nobody entered a cost basis for, and collapsing that
    /// difference into one nil is what let the widget print "No flips sold yet
    /// this month" beside a Flips screen reading "2 items sold".
    ///
    /// `parts` is the profit per currency, the phone's own first (#224): what
    /// the widget prints. `profit` is the first part, for a widget built
    /// before `parts`, and is the month's whole profit when the month was kept
    /// in one currency, as nearly every month is.
    static func monthLedger(results: [ScanResult], now: Date = Date(),
                            calendar: Calendar = .current, locale: Locale = .current)
    -> (profit: Double, parts: [WidgetMoney], flips: Int, sold: Int) {
        let sales = LedgerMath.sales(
            LedgerMath.soldInMonth(results, containing: now, calendar: calendar), locale: locale)
        let parts = SaleCurrency.ordered(sales.profit, locale: locale).map {
            WidgetMoney(code: $0,
                        amount: NSDecimalNumber(decimal: sales.profit.amount(in: $0)).doubleValue)
        }
        return (parts.first?.amount ?? 0, parts, sales.priced, sales.count)
    }

    /// Call this after any insert/delete of ScanResults in the main app.
    ///
    /// `isPro` defaults to nil meaning *read the persisted entitlement*. Most
    /// callers are repository writes that have no idea about entitlement, and
    /// clobbering it to `false` on every scan would blank a paying
    /// subscriber's Pro widgets until they next opened the paywall.
    ///
    /// This used to resolve nil as "carry forward whatever is already
    /// stored", which sounded conservative and was in fact a dead end: no
    /// production caller ever passed `isPro`, `WidgetHaulData.empty.isPro` is
    /// `false`, and a v1 blob has no `isPro` key to seed from — so the flag
    /// could never become true and two of the six widgets were permanently
    /// wrong for subscribers. Reading the cache the purchase service already
    /// keeps also handles the other direction: a lapse restores the free-scan
    /// count on the next write instead of leaving a subscriber's presentation
    /// on the Home Screen.
    ///
    /// The month ledger is written for every tier and is not affected by a
    /// lapse — the app gives free users that figure too, so withholding it
    /// here upsold a feature they already had.
    static func writeHaul(results: [ScanResult], isPro: Bool? = nil) {
        // Never publish a fallback launch's library.
        //
        // When SwiftData cannot open the on-disk store, `SnapWorthApp` falls
        // back to an in-memory container so the app still runs. A fetch against
        // it does not throw — it succeeds and returns `[]` — so every caller
        // here looked like a library that had simply been emptied, and the
        // launch-path seed reached this function before the app had any idea
        // whether the store would ever open again. This blob is the only copy
        // of the haul outside the store, which makes it the only representation
        // still standing while the store is unreadable; rewriting it to zeros
        // destroys the last good figures for a condition that, on a transient
        // failure like a full disk at open time, clears itself next launch.
        //
        // The guard lives here rather than at the call site because there is
        // more than one caller: `seedWidgetData` on launch and on an
        // entitlement change, and `ScanRepository`'s debounced sync for every
        // scan written during the fallback session — work that is discarded at
        // quit and has no business reaching the Home Screen either.
        //
        // Stale beats zeroed. The first healthy launch overwrites it with the
        // truth.
        guard !AppLaunchState.isRunningOnFallbackStore else { return }

        // Condition-adjusted, like every other surface. This summed the raw AI
        // baseline while `lastItemRange` below — rendered inches away inside
        // the same medium widget — is adjusted, so a one-item library showed
        // two different values for the same item with no way to tell which was
        // the product's answer.
        //
        // Summed as `Decimal` and converted once, matching the portfolio
        // path's precision rule rather than accumulating `Double` error across
        // a long history.
        //
        // One price read per row for all three sums. This runs on the main
        // actor at launch and after every save, and read each row's range
        // three times over.
        let ranges = results.map(\.currentPriceRange)
        let lo = NSDecimalNumber(decimal: LedgerMath.total(ranges.map { $0.low })).doubleValue
        let hi = NSDecimalNumber(decimal: LedgerMath.total(ranges.map { $0.high })).doubleValue
        // The likely value of the very items above — same items, same
        // condition adjustment, `likely` instead of `low` and `high`: the
        // model's expected price where a find has one, the midpoint where it
        // does not (`ScanResult.baselineLikely`). So the circular complication
        // reads the figure the app sorts and totals by, rather than the top of
        // what the rectangular one shows. See `WidgetHaulData.totalLikely`.
        //
        // Summed over every result, like `lo` and `hi`, and *not* over
        // `HistoryViewModel.portfolioTotal`'s population, which is the unsold
        // portfolio. The two agree for a library with nothing sold, which is
        // the case the finding describes; where they differ they are answering
        // different questions, and so are `totalLow`/`totalHigh` already. A
        // widget whose own three figures disagree with each other would be the
        // worse trade.
        let likely = NSDecimalNumber(decimal: LedgerMath.total(ranges.map { $0.likely })).doubleValue
        let last = results.max(by: { $0.timestamp < $1.timestamp })

        let pro = isPro ?? StoreKitPurchaseService.cachedIsSubscribed

        let recent = results
            .sorted { $0.timestamp > $1.timestamp }
            .prefix(WidgetBridge.maxRecentFinds)
            .map { WidgetFind(id: $0.id.uuidString,
                              name: $0.itemName,
                              range: $0.formattedRange) }

        let month = Self.monthLedger(results: results)

        // Pro-only figures are written only while Pro, so a lapse clears them
        // on the next write instead of leaving a paid number on the Home
        // Screen indefinitely. The month ledger is *not* one of them — see
        // below.

        let data = WidgetHaulData(
            totalLow:      lo,
            totalHigh:     hi,
            itemCount:     results.count,
            lastItemName:  last?.itemName      ?? "",
            lastItemRange: last?.formattedRange ?? "",
            updatedAt:     Date(),
            freeScansRemaining: pro ? nil : FreeScanCounter.remaining,
            isPro:         pro,
            streak:        ScanStreak.current(),
            recentFinds:   Array(recent),
            // Written for every tier. The app is the authority on what is
            // paid, and it hands free users this exact figure: `FlipsView`
            // puts them on the month scope on purpose, under a header reading
            // "Profit this month", computed by the same month-scoped sum this
            // writer takes. The real ledger gates are all-time scope, sold
            // rows past `ledgerFreeSoldCap`, and CSV export.
            //
            // Withholding it here made the widget upsell a feature the user
            // already had, with a deep link that opens the very screen showing
            // the number — and produced the same blob state for a lapsed
            // subscriber, telling them profit tracking had been taken away
            // when it had not.
            monthProfit:   month.flips > 0 ? month.profit : nil,
            monthFlips:    month.flips,
            // The day the streak was last extended and the full allowance, so
            // the widget can tell a live streak from a lapsed one and a spent
            // allowance from a reset one without the app running.
            streakLastScan: ScanStreak.lastScan,
            freeScanAllowance: Config.freeScansAllowed,
            // Ungated with the other two: it captions the same figure, and a
            // caption that says "0 sold" beside a real profit would be its own
            // contradiction.
            monthSold: month.sold,
            totalLikely: likely,
            monthProfitParts: month.flips > 0 ? month.parts : nil
        )

        guard
            let suite   = UserDefaults(suiteName: appGroupID),
            let encoded = try? JSONEncoder().encode(data)
        else { return }

        suite.set(encoded, forKey: haulKey)
        WidgetCenter.shared.reloadAllTimelines()
    }

    /// Also called from main app (e.g. to pre-populate on launch).
    static func readHaul() -> WidgetHaulData {
        guard
            let suite = UserDefaults(suiteName: appGroupID),
            let raw   = suite.data(forKey: haulKey),
            let data  = try? JSONDecoder().decode(WidgetHaulData.self, from: raw)
        else { return .empty }
        return data
    }
}

// ── Which widgets are placed ─────────────────────────────────────────────────

/// Reports, once a day, how many of this app's widgets are on the device and
/// of which kinds.
///
/// `widget_opened` counts taps, and a widget that is glanced at and never
/// tapped — which is most of what a Home Screen widget is for — would read as
/// unused. This is the other half: whether the widgets are there at all.
/// The count is bucketed like every other count in the payload; the kinds are
/// this extension's own identifiers, a closed set.
@MainActor
enum WidgetInstallReport {
    private static let lastDayKey = "snapworth_widget_report_day"

    static func sendIfDue(now: Date = Date(), defaults: UserDefaults = .standard) async {
        let parts = Calendar.current.dateComponents([.year, .month, .day], from: now)
        let day = String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
        guard defaults.string(forKey: lastDayKey) != day else { return }
        // Stamped before the await, so a launch and a foreground arriving
        // together cannot both get past the check and report twice.
        defaults.set(day, forKey: lastDayKey)
        // The completion form: the async one is iOS 18, and this app runs on 17.
        let placed: [String]? = await withCheckedContinuation { done in
            WidgetCenter.shared.getCurrentConfigurations { result in
                done.resume(returning: (try? result.get())?.map(\.kind))
            }
        }
        guard let placed else { return }
        let kinds = Set(placed).sorted().joined(separator: ",")
        Analytics.shared.track(.widgetsInstalled(count: bucket(placed.count), kinds: kinds))
    }

    nonisolated static func bucket(_ count: Int) -> String {
        switch count {
        case ..<1:  return "0"
        case 1:     return "1"
        case 2...3: return "2-3"
        default:    return "4+"
        }
    }
}
