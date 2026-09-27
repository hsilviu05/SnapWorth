import Foundation

/// The ledger's money rules, once: which sales fall in a month, what they
/// made, and what is still held.
///
/// Each of these was written separately by every surface that needed it —
/// "sold this month" by My Flips' header, its bars, its share card and the
/// widget writer, "still held" by the My Finds banner and the weekly digest —
/// and the screens-disagree class of bug has been fixed four times already
/// (56cd7c4, 380445b, c39c8f2, 027413d), each time by bringing one copy back
/// in line with another. A rule that exists once cannot drift from itself.
///
/// Pure and free of SwiftData and the view, so every rule is testable with
/// plain rows. What an individual find is worth is not here: that is
/// `ScanResult.portfolioValue`, and this only adds those figures up.
enum LedgerMath {

    // ── Periods ──────────────────────────────────────────────────────────────

    /// The calendar month containing `now`, in `calendar`'s zone — the user's
    /// local month, as every date rule in the ledger is. See `FlipsViewModel.csv`
    /// for what a UTC day did to a sale logged in the evening.
    static func month(containing now: Date, calendar: Calendar = .current) -> DateInterval? {
        calendar.dateInterval(of: .month, for: now)
    }

    /// Whether `date` falls in `interval`: the start included, the end not.
    ///
    /// `DateInterval.contains` includes its end, and a month's end is the
    /// first instant of the next one. So a sale logged at exactly midnight on
    /// the 1st was counted in both months by the two surfaces that used it —
    /// the widget and the six-month bars — while My Flips' header, which asked
    /// `Calendar.isDate(_:equalTo:toGranularity:)`, put it in the new month
    /// only. One instant a month, but one sale in two months' totals is the
    /// disagreement this type exists to end.
    static func contains(_ interval: DateInterval, _ date: Date) -> Bool {
        interval.start <= date && date < interval.end
    }

    // ── Sales ────────────────────────────────────────────────────────────────

    /// What a set of finds sold for, in the terms every sales total uses.
    struct Sales: Equatable {
        /// Realised profit, summed over the sales with a paid price.
        ///
        /// `realizedProfit` is nil without one, and a sale with no cost basis
        /// has no knowable profit: counting it as zero would understate the
        /// figure, and guessing a basis would invent it.
        var profit: Decimal = 0
        /// How many sales `profit` is the profit *of*.
        var priced: Int = 0
        /// Every sale, priced or not. Larger than `priced` by exactly the
        /// sales nobody entered a paid price for — the gap "$214 from 6
        /// flips" and "2 items sold" must each be honest about.
        var count: Int = 0
    }

    /// Every find marked sold.
    static func sold(_ results: [ScanResult]) -> [ScanResult] {
        results.filter { $0.status == .sold }
    }

    /// The finds sold inside `interval`. A sold find with no sale date is in
    /// no period — it still counts all-time, through `sold(_:)`.
    static func sold(_ results: [ScanResult], in interval: DateInterval) -> [ScanResult] {
        results.filter { result in
            guard result.status == .sold, let soldDate = result.soldDate else { return false }
            return contains(interval, soldDate)
        }
    }

    /// The finds sold in the calendar month containing `now`.
    static func soldInMonth(_ results: [ScanResult], containing now: Date = Date(),
                            calendar: Calendar = .current) -> [ScanResult] {
        guard let month = month(containing: now, calendar: calendar) else { return [] }
        return sold(results, in: month)
    }

    /// Profit and counts over the sold finds among `results`. Anything not
    /// sold is ignored, so a whole library can be passed for all-time totals.
    static func sales(_ results: [ScanResult]) -> Sales {
        let sold = sold(results)
        let profits = sold.compactMap(\.realizedProfit)
        return Sales(profit: total(profits), priced: profits.count, count: sold.count)
    }

    // ── Holdings ─────────────────────────────────────────────────────────────

    /// Everything not sold: scanned, owned and listed alike. What "Your finds
    /// are worth" and the weekly digest describe.
    static func held(_ results: [ScanResult]) -> [ScanResult] {
        results.filter { $0.status != .sold }
    }

    /// The condition-adjusted likely value of what is still held.
    ///
    /// A sold find is out: its estimate is not the sale price, and the sale is
    /// already in `sales(_:).profit` — counting both put one item on a card
    /// twice, as two numbers reconciling with nothing.
    static func heldValue(_ results: [ScanResult]) -> Decimal {
        total(held(results).map(\.portfolioValue))
    }

    /// A sum of money, in `Decimal`: binary floating point drifts once per
    /// item, and a portfolio total is where that shows.
    static func total(_ values: [Decimal]) -> Decimal {
        values.reduce(Decimal.zero, +)
    }
}
