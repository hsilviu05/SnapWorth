import ActivityKit
import Foundation
import SwiftData
import SwiftUI
import WidgetKit

// ── BEGIN SHARED WIDGET MODEL ────────────────────────────────────────────────
//
// This block is duplicated verbatim in:
//   ios/SnapWorth/Services/WidgetDataStore.swift        (app target)
//   ios/SnapWorthWidgets/SnapWorthWidgets.swift         (widget target)
//
// A widget extension cannot import the app's module, and the app target uses
// explicit file references while SnapWorthWidgets is a synchronised folder, so
// one file cannot cheaply belong to both. The copies are therefore checked
// byte-for-byte by the "Widget model is in sync" step in .github/workflows/ios.yml.
//
// They had already drifted before that check existed: the widget's
// `formattedRange` hardcoded its own formatter and lacked the empty-haul guard,
// so a library with no scans read "$0 – $0" on the Home Screen and "$0" in the
// app. Everything the model needs is self-contained below for that reason —
// nothing here may reference a symbol that exists in only one of the targets.

/// One recent find, for the list widgets.
struct WidgetFind: Codable, Identifiable, Equatable {
    var id: String
    var name: String
    var range: String
}

/// What the app last told the widgets about the user's library.
///
/// **Every field added after v1 must be optional or defaulted in `init(from:)`.**
/// Swift's synthesised `Codable` initialiser throws `keyNotFound` for a missing
/// key — it does *not* fall back to a property's default value. The widget
/// process routinely reads a blob written by an older build of the app (an
/// update installs the new extension before the user next opens the app), so a
/// strict decode would empty every Home Screen until the next scan. Hence the
/// hand-written decoder.
struct WidgetHaulData: Codable, Equatable {

    // v1 — shipped 1.3.x
    var totalLow: Double
    var totalHigh: Double
    var itemCount: Int
    var lastItemName: String
    var lastItemRange: String
    var updatedAt: Date

    // v2 — added 1.4.0
    /// Free scans left today. `nil` means Pro, or never established.
    var freeScansRemaining: Int?
    /// What the app last knew. The widget cannot ask StoreKit, so this can lag
    /// a lapsed subscription until the app next runs — it gates presentation
    /// only, never access.
    var isPro: Bool
    var streak: Int
    /// Newest first, capped by the writer.
    var recentFinds: [WidgetFind]
    /// Month-to-date profit. Written only while Pro, so a lapse clears it on
    /// the next write rather than leaving a paid number on the Home Screen.
    var monthProfit: Double?
    var monthFlips: Int

    // v3 — added 1.4.0, before release
    //
    // Three things above are scoped to a period and were stored as bare
    // numbers: the free-scan count (a UTC day), the streak (a local day), and
    // the month's profit (a local month). The extension cannot recompute any
    // of them — `FreeScanCounter` and `ScanStreak` live in
    // `UserDefaults.standard`, not the App Group, and the ledger is in
    // SwiftData — and the providers' hourly refresh just re-read the same
    // frozen number. So each one outlived the period it described: a spent
    // allowance stayed spent past the reset, a lapsed streak kept showing, and
    // September's profit carried into October under a header reading "This
    // month". These two fields plus `updatedAt` are what let the widget tell.
    /// The day the streak was last extended. `ScanStreak.current()` returns 0
    /// once that is older than yesterday; without the date the widget has no
    /// way to apply the same test.
    var streakLastScan: Date?
    /// The full daily allowance, so a count that has aged out of its UTC day
    /// can render the number actually available rather than nothing.
    var freeScanAllowance: Int?

    static let empty = WidgetHaulData(
        totalLow: 0, totalHigh: 0, itemCount: 0,
        lastItemName: "", lastItemRange: "", updatedAt: .distantPast,
        freeScansRemaining: nil, isPro: false, streak: 0,
        recentFinds: [], monthProfit: nil, monthFlips: 0
    )

    var hasScans: Bool { itemCount > 0 }

    /// Unspaced, matching `ScanResult.formattedRange` — which is what the app
    /// shows everywhere, and what lands in `lastItemRange` and every
    /// `WidgetFind.range` in this very blob. This was spaced, so the medium
    /// widget printed "$348 – $620" for the haul and "$60–$95" for the last
    /// item a few points to its right: the same kind of quantity, in one card,
    /// punctuated two ways.
    var formattedRange: String {
        guard hasScans else { return "$0" }
        return "\(Self.money(totalLow))–\(Self.money(totalHigh))"
    }

    var formattedMonthProfit: String? {
        guard let monthProfit else { return nil }
        return Self.money(monthProfit)
    }

    static func money(_ value: Double) -> String {
        Self.currencyFormatter.string(from: NSNumber(value: value))
            ?? "$\(Int(value))"
    }

    /// Matches `NumberFormatter.snapCurrency` in the app's design system. Held
    /// separately because the widget target cannot see that file.
    private static let currencyFormatter: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .currency
        f.currencyCode = "USD"
        f.locale = Locale(identifier: "en_US")
        f.maximumFractionDigits = 0
        return f
    }()

    private enum CodingKeys: String, CodingKey {
        case totalLow, totalHigh, itemCount, lastItemName, lastItemRange, updatedAt
        case freeScansRemaining, isPro, streak, recentFinds, monthProfit, monthFlips
        case streakLastScan, freeScanAllowance
    }

    init(totalLow: Double, totalHigh: Double, itemCount: Int,
         lastItemName: String, lastItemRange: String, updatedAt: Date,
         freeScansRemaining: Int?, isPro: Bool, streak: Int,
         recentFinds: [WidgetFind], monthProfit: Double?, monthFlips: Int,
         streakLastScan: Date? = nil, freeScanAllowance: Int? = nil) {
        self.totalLow = totalLow
        self.totalHigh = totalHigh
        self.itemCount = itemCount
        self.lastItemName = lastItemName
        self.lastItemRange = lastItemRange
        self.updatedAt = updatedAt
        self.freeScansRemaining = freeScansRemaining
        self.isPro = isPro
        self.streak = streak
        self.recentFinds = recentFinds
        self.monthProfit = monthProfit
        self.monthFlips = monthFlips
        self.streakLastScan = streakLastScan
        self.freeScanAllowance = freeScanAllowance
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // v1 fields are defaulted too: a blob truncated or half-written by a
        // crash should render an empty widget, not no widget at all.
        totalLow = try c.decodeIfPresent(Double.self, forKey: .totalLow) ?? 0
        totalHigh = try c.decodeIfPresent(Double.self, forKey: .totalHigh) ?? 0
        itemCount = try c.decodeIfPresent(Int.self, forKey: .itemCount) ?? 0
        lastItemName = try c.decodeIfPresent(String.self, forKey: .lastItemName) ?? ""
        lastItemRange = try c.decodeIfPresent(String.self, forKey: .lastItemRange) ?? ""
        updatedAt = try c.decodeIfPresent(Date.self, forKey: .updatedAt) ?? .distantPast
        freeScansRemaining = try c.decodeIfPresent(Int.self, forKey: .freeScansRemaining)
        isPro = try c.decodeIfPresent(Bool.self, forKey: .isPro) ?? false
        streak = try c.decodeIfPresent(Int.self, forKey: .streak) ?? 0
        recentFinds = try c.decodeIfPresent([WidgetFind].self, forKey: .recentFinds) ?? []
        monthProfit = try c.decodeIfPresent(Double.self, forKey: .monthProfit)
        monthFlips = try c.decodeIfPresent(Int.self, forKey: .monthFlips) ?? 0
        streakLastScan = try c.decodeIfPresent(Date.self, forKey: .streakLastScan)
        freeScanAllowance = try c.decodeIfPresent(Int.self, forKey: .freeScanAllowance)
    }
}

/// Where the two targets meet.
enum WidgetBridge {
    static let appGroupID = "group.eu.snapworth.app"
    static let haulKey = "snapworth.widget.haul"
    /// How many finds the list widgets can show at their largest.
    static let maxRecentFinds = 4
}

// ── Compact money, for the accessory families ────────────────────────────────

extension WidgetHaulData {
    /// `$1.2K` rather than `$1,240`.
    ///
    /// A circular accessory is about 72 points across; a four-figure total with
    /// a thousands separator does not fit there at a legible size.
    ///
    /// Both boundaries are decided *after* rounding, which is where this went
    /// wrong twice. Choosing the branch from the raw value put 999.6 in the
    /// sub-thousand case, printing the "$1000" the abbreviation exists to
    /// avoid; and it made 9,999 read "$10.0K" while 10,000 read "$10K" — the
    /// same number, spelled two ways, one dollar apart.
    ///
    /// The sign is prefixed to the whole thing, not left inside the amount.
    /// Interpolating the signed number straight after the "$" printed a loss
    /// as "$-1.2K", and the month's profit is the one consumer that expects
    /// negatives. The app spells the same figure "−$420"
    /// (`FlipsViewModel.signedMoney`), and one number must not read two ways
    /// on two surfaces — so this uses the same U+2212 minus.
    static func compactMoney(_ value: Double) -> String {
        let dollars = value.rounded()
        let sign = dollars < 0 ? "−" : ""
        let magnitude = abs(dollars)
        guard magnitude >= 1_000 else { return "\(sign)$\(Int(magnitude))" }

        let thousands = (magnitude / 100).rounded() / 10
        guard thousands >= 10 else {
            return "\(sign)$\(String(format: "%.1f", thousands))K"
        }
        return "\(sign)$\(Int(thousands.rounded()))K"
    }

    var compactTotal: String { Self.compactMoney(totalHigh) }

    var compactRange: String {
        "\(Self.compactMoney(totalLow))–\(Self.compactMoney(totalHigh))"
    }

    /// "8 finds" / "1 find". Spelled out because the accessory families have no
    /// room for a label beside the number.
    var findsLabel: String {
        "\(itemCount) find\(itemCount == 1 ? "" : "s")"
    }

    /// "8 items" / "1 item".
    ///
    /// Static because the Quick Scan widget's entry carries a bare count
    /// rather than a whole haul — which is how it came to hardcode the plural
    /// and greet a brand-new user with "1 items in your haul" on the very
    /// first impression the widget ever makes. Three widgets were spelling
    /// this out inline; one of them got it wrong.
    static func itemsLabel(_ count: Int) -> String {
        "\(count) item\(count == 1 ? "" : "s")"
    }
}

// ── Recent finds, and Scans left ─────────────────────────────────────────────
//
// Both live here rather than in the widget views because the app test target
// cannot import the widget extension. The two defects below compiled fine and
// were invisible to every test: a widget's strings are only testable if the
// strings are in the shared model.

extension WidgetHaulData {
    /// The rows the "Recent finds" widget should draw, newest first.
    ///
    /// `recentFinds` is a v2 key, so a 1.3.x blob decodes it to `[]` — the
    /// hand-written `init(from:)` defaults every v2 field — while `itemCount`
    /// and the totals decode from v1 perfectly. That is exactly the case this
    /// model documents above: an update installs the new extension before the
    /// user next opens the app. The widget's header branched on `hasScans` and
    /// its body on `recentFinds.isEmpty`, so it rendered "$348 – $620" and
    /// "Nothing scanned yet" at the same time. A v1 blob does carry one find,
    /// in `lastItemName`/`lastItemRange` — draw that instead of claiming there
    /// are none.
    func recentRows(limit: Int) -> [WidgetFind] {
        if !recentFinds.isEmpty { return Array(recentFinds.prefix(limit)) }
        guard hasScans, !lastItemName.isEmpty else { return [] }
        return [WidgetFind(id: "last", name: lastItemName, range: lastItemRange)]
    }

    /// What the "Scans left" widget is looking at.
    ///
    /// Three states, not a number with a fallback. Every read used
    /// `freeScansRemaining ?? 0`, and nil on a non-Pro blob does not mean
    /// zero — it means the app has never written a count: no blob in the App
    /// Group yet, a decode failure, or a v1 blob from an install that has not
    /// been reopened since the update. Zero is the alarming branch, a large
    /// terracotta "0" captioned "Back tomorrow, or go Pro", and it was being
    /// shown to people whose whole daily allowance was untouched.
    enum ScansLeft: Equatable {
        case pro(streak: Int)
        case remaining(Int)
        case unknown
    }

}

extension WidgetHaulData.ScansLeft {
    /// Pro carries the streak rather than the word "unlimited": a number that
    /// never changes is not worth a slot on someone's Home Screen.
    var headline: String {
        switch self {
        case .pro(let streak):     return streak > 0 ? "\(streak)-day streak" : "Pro"
        case .remaining(let left): return "\(left)"
        case .unknown:             return "—"
        }
    }

    var subtitle: String {
        switch self {
        case .pro(let streak):     return streak > 1 ? "Keep it going" : "Unlimited scans"
        case .remaining(0):        return "Back tomorrow, or go Pro"
        case .remaining(let left): return "free scan\(left == 1 ? "" : "s") left today"
        case .unknown:             return "Open SnapWorth"
        }
    }

    var circularValue: String {
        switch self {
        case .pro(let streak):     return streak > 0 ? "\(streak)" : "∞"
        case .remaining(let left): return "\(left)"
        case .unknown:             return "—"
        }
    }

    var spoken: String {
        switch self {
        case .pro(let streak):
            return streak > 0 ? "\(streak) day scanning streak" : "SnapWorth Pro"
        case .remaining(0):
            return "No free scans left today"
        case .remaining(let left):
            return "\(left) free scan\(left == 1 ? "" : "s") left today"
        case .unknown:
            return "Scan count not available yet. Open SnapWorth."
        }
    }

    /// True only when the count is known and spent — the view paints terracotta
    /// here, which reads as "you are out" and must not fire on `.unknown`.
    var isSpent: Bool { self == .remaining(0) }
}

// ── Freshness ────────────────────────────────────────────────────────────────
//
// A widget renders an entry, and an entry has a date. Everything below asks
// whether a stored number still describes the period it was written for, using
// that date rather than `Date.now` — so the answer is the same whether it is
// computed for a timeline entry scheduled at a boundary or for a test.

extension WidgetHaulData {
    /// A UTC calendar, matching `FreeScanCounter.isServerToday`.
    ///
    /// The allowance resets on the server's day, not the phone's — `quota.py`
    /// counts UTC days — so the widget has to ask the same question the app
    /// asks, not a local-midnight approximation of it.
    static var serverCalendar: Calendar {
        var utc = Calendar(identifier: .gregorian)
        utc.timeZone = TimeZone(secondsFromGMT: 0) ?? .gmt
        return utc
    }

    /// Whether `freeScansRemaining` still describes the day `now` falls in.
    func quotaIsCurrent(at now: Date) -> Bool {
        Self.serverCalendar.isDate(updatedAt, inSameDayAs: now)
    }

    /// The streak as the app would compute it: the stored count if the last
    /// scan was today or yesterday, else zero. Mirrors `ScanStreak.current()`,
    /// including its use of the *local* calendar — the streak is a habit, and a
    /// habit is kept in the timezone you live in, unlike the allowance.
    ///
    /// A blob written before `streakLastScan` existed has no date to test
    /// against, so its count is taken at face value: showing a possibly-stale
    /// streak for one launch is better than blanking a real one.
    func liveStreak(at now: Date) -> Int {
        guard streak > 0 else { return 0 }
        guard let last = streakLastScan else { return streak }
        let calendar = Calendar.current
        if calendar.isDate(last, inSameDayAs: now) { return streak }
        guard let yesterday = calendar.date(byAdding: .day, value: -1, to: now) else {
            return streak
        }
        return calendar.isDate(last, inSameDayAs: yesterday) ? streak : 0
    }

    /// Whether `monthProfit` still describes the month `now` falls in. The
    /// figure is labelled "This month" in the widget, so once this is false the
    /// label is a false claim and the number has to go.
    func monthIsCurrent(at now: Date) -> Bool {
        Calendar.current.isDate(updatedAt, equalTo: now, toGranularity: .month)
    }

    /// The month's profit, or nil once the month it belongs to has ended.
    func monthProfit(at now: Date) -> Double? {
        guard monthIsCurrent(at: now) else { return nil }
        return monthProfit
    }

    /// The flip count behind `monthProfit(at:)`, zeroed on the same boundary so
    /// the two cannot disagree — "$0 from 6 flips" is worse than either alone.
    func monthFlips(at now: Date) -> Int {
        monthIsCurrent(at: now) ? monthFlips : 0
    }

    /// What the "Scans left" widget is looking at, as of `now`.
    func scansLeft(at now: Date) -> ScansLeft {
        if isPro { return .pro(streak: liveStreak(at: now)) }
        guard let left = freeScansRemaining else { return .unknown }
        guard quotaIsCurrent(at: now) else {
            // The allowance has reset since this was written, and the app has
            // not run to record a scan against the new day — so the whole
            // allowance is available. Saying so beats both the stale zero and
            // an em dash.
            guard let allowance = freeScanAllowance, allowance > 0 else { return .unknown }
            return .remaining(allowance)
        }
        return .remaining(left)
    }

    /// The instants at which one of the snapshots above stops being true.
    ///
    /// The providers emitted a single entry dated `.now` with a blind hourly
    /// `.after` policy, so every refresh re-read the same frozen numbers and
    /// nothing was scheduled where they actually expire. A timeline entry at
    /// each boundary makes the correction happen without the app running.
    ///
    /// Three boundaries, because the three values are scoped differently: the
    /// next UTC midnight (the allowance), the next local midnight (the streak),
    /// and the start of the next local month (the profit). Deduplicated,
    /// because for anyone at UTC+0 the first two are the same instant.
    static func refreshBoundaries(after now: Date) -> [Date] {
        var dates: [Date] = []

        let utc = serverCalendar
        if let nextServerDay = utc.date(byAdding: .day, value: 1,
                                        to: utc.startOfDay(for: now)) {
            dates.append(nextServerDay)
        }

        let local = Calendar.current
        if let nextLocalDay = local.date(byAdding: .day, value: 1,
                                         to: local.startOfDay(for: now)) {
            dates.append(nextLocalDay)
        }
        if let nextMonth = local.dateInterval(of: .month, for: now)?.end {
            dates.append(nextMonth)
        }

        return Set(dates.filter { $0 > now }).sorted()
    }
}

// ── Pending action ───────────────────────────────────────────────────────────

extension WidgetBridge {
    /// A Control Centre tap waiting for the app to notice it.
    ///
    /// A Control Widget cannot carry a `widgetURL` the way a Home Screen widget
    /// can — it runs an App Intent instead. That intent opens the app, which
    /// means posting a navigation notification from `perform()` races the
    /// app's own launch: on a cold start there is no view listening yet, and
    /// the tap is silently swallowed. Leaving the request here instead lets the
    /// app drain it when it is ready, cold start or resume alike.
    static let pendingActionKey = "snapworth.widget.pendingAction"

    enum PendingAction: String {
        case scan
    }

    static func request(_ action: PendingAction) {
        UserDefaults(suiteName: appGroupID)?
            .set(action.rawValue, forKey: pendingActionKey)
    }

    /// The waiting action, if any. Clears it, so a tap is acted on once —
    /// re-reading on every foreground would otherwise reopen the camera every
    /// time the user came back to the app.
    static func takePendingAction() -> PendingAction? {
        guard let suite = UserDefaults(suiteName: appGroupID),
              let raw = suite.string(forKey: pendingActionKey) else { return nil }
        suite.removeObject(forKey: pendingActionKey)
        return PendingAction(rawValue: raw)
    }
}

// ── Live Activity ────────────────────────────────────────────────────────────

/// A thrift run: one trip, one running total.
///
/// Deliberately not the whole library. The haul widgets already answer "what is
/// everything worth"; this answers "what have I found *since I walked in*",
/// which is the number you want while deciding whether the next thing is worth
/// picking up. `startedAt` is what makes that possible — the app counts only
/// scans at or after it.
struct ThriftRunAttributes: ActivityAttributes {
    struct ContentState: Codable, Hashable {
        var itemCount: Int
        var totalLow: Double
        var totalHigh: Double
        var lastItemName: String

        var formattedRange: String {
            guard itemCount > 0 else { return "$0" }
            return "\(WidgetHaulData.money(totalLow))–\(WidgetHaulData.money(totalHigh))"
        }

        var compactTotal: String { WidgetHaulData.compactMoney(totalHigh) }

        var findsLabel: String {
            "\(itemCount) find\(itemCount == 1 ? "" : "s")"
        }

        static let empty = ContentState(itemCount: 0, totalLow: 0, totalHigh: 0,
                                        lastItemName: "")
    }

    var startedAt: Date
}
// ── END SHARED WIDGET MODEL ──────────────────────────────────────────────────

// ── Store ─────────────────────────────────────────────────────────────────────

enum WidgetDataStore {
    static let appGroupID = WidgetBridge.appGroupID
    static let haulKey = WidgetBridge.haulKey

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
    /// keeps also handles the other direction: a lapse clears the Pro figures
    /// on the next write instead of leaving a paid number on the Home Screen.
    static func writeHaul(results: [ScanResult], isPro: Bool? = nil) {
        // Condition-adjusted, like every other surface. This summed the raw AI
        // baseline while `lastItemRange` below — rendered inches away inside
        // the same medium widget — is adjusted, so a one-item library showed
        // two different values for the same item with no way to tell which was
        // the product's answer.
        //
        // Summed as `Decimal` and converted once, matching the portfolio
        // path's precision rule rather than accumulating `Double` error across
        // a long history.
        let lo = NSDecimalNumber(decimal: results.reduce(Decimal.zero) {
            $0 + $1.priceRange(for: $1.condition).low
        }).doubleValue
        let hi = NSDecimalNumber(decimal: results.reduce(Decimal.zero) {
            $0 + $1.priceRange(for: $1.condition).high
        }).doubleValue
        let last = results.max(by: { $0.timestamp < $1.timestamp })

        let pro = isPro ?? StoreKitPurchaseService.cachedIsSubscribed

        let recent = results
            .sorted { $0.timestamp > $1.timestamp }
            .prefix(WidgetBridge.maxRecentFinds)
            .map { WidgetFind(id: $0.id.uuidString,
                              name: $0.itemName,
                              range: $0.formattedRange) }

        // Month-to-date ledger, by the same rule `FlipsViewModel.monthlyBuckets`
        // uses: sold, with a sold date inside the current month. Computed here
        // from the same array rather than passed in, so the widget and the
        // Flips screen cannot disagree about a month's profit the way four
        // surfaces once disagreed about an item's value.
        //
        // The count is of items that actually *contributed* profit, not of
        // everything sold: `realizedProfit` is nil without a cost basis, and
        // "$214 from 6 flips" has to be true of the same six.
        let monthInterval = Calendar.current.dateInterval(of: .month, for: Date())
        let monthProfits: [Decimal] = results.compactMap { result in
            guard result.status == .sold,
                  let soldDate = result.soldDate,
                  let monthInterval, monthInterval.contains(soldDate)
            else { return nil }
            return result.realizedProfit
        }
        let monthProfit = NSDecimalNumber(
            decimal: monthProfits.reduce(Decimal.zero, +)).doubleValue

        // Pro-only figures are written only while Pro, so a lapse clears them
        // on the next write instead of leaving a paid number on the Home
        // Screen indefinitely.

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
            monthProfit:   pro && !monthProfits.isEmpty ? monthProfit : nil,
            monthFlips:    pro ? monthProfits.count : 0,
            // The day the streak was last extended and the full allowance, so
            // the widget can tell a live streak from a lapsed one and a spent
            // allowance from a reset one without the app running.
            streakLastScan: ScanStreak.lastScan,
            freeScanAllowance: Config.freeScansAllowed
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
