import SwiftUI
import SwiftData

enum HistorySortOrder: String, CaseIterable {
    case newest = "Newest"
    case mostValuable = "Most Valuable"

    /// What the sort menu shows; the raw value is the stored identity.
    var label: String {
        switch self {
        case .newest:       return String(localized: "Newest", comment: "Finds sort order")
        case .mostValuable: return String(localized: "Most Valuable", comment: "Finds sort order")
        }
    }
}

@MainActor
@Observable
final class HistoryViewModel {
    var searchText: String = ""
    var sortOrder: HistorySortOrder = .newest
    var deleteError: String?

    /// The library just became empty. Drops anything scoped to the rows that
    /// were in it.
    ///
    /// `searchText` outlived a wipe: the search field is only *hidden* when the
    /// library empties, while this view model is `@State` for the whole
    /// lifetime of the tab. So the first find after a wipe was filtered against
    /// the old query — a banner reading "1 item scanned" above a grid reading
    /// "No results for \"nike\"", with the find they had just made nowhere on
    /// screen.
    func libraryEmptied() {
        searchText = ""
    }

    func sorted(_ results: [ScanResult]) -> [ScanResult] {
        switch sortOrder {
        case .newest:
            return results.sorted { $0.timestamp > $1.timestamp }
        case .mostValuable:
            // Each key once, then sort. A comparator reading `likelyValue`
            // re-prices both sides of every comparison — n log n price reads
            // where n will do.
            return results
                .map { (result: $0, value: $0.likelyValue) }
                .sorted { $0.value > $1.value }
                .map(\.result)
        }
    }

    /// Filtered first, then sorted: a search narrows the list, and there is
    /// no reason to order the rows it is about to throw away.
    func filtered(_ results: [ScanResult]) -> [ScanResult] {
        guard !searchText.isEmpty else { return sorted(results) }
        return sorted(results.filter {
            $0.itemName.localizedCaseInsensitiveContains(searchText) ||
            $0.brand.localizedCaseInsensitiveContains(searchText)
        })
    }

    /// Sum of the condition-adjusted "likely" value across the passed scans.
    ///
    /// Decimal, not Double. `priceRange(for:)` already returns Decimal — the
    /// previous version converted each item to Double via `midpointValue`,
    /// summed in binary floating point and formatted through `NSNumber`. Every
    /// other money path in the app (ThriftFlipViewModel, FlipsViewModel,
    /// realizedProfit) is Decimal, and a portfolio total is precisely where
    /// accumulated drift shows: the error compounds once per item, so it grows
    /// with the size of the library this feature is meant to celebrate.
    ///
    /// Free of SwiftData and of the view, so the arithmetic is directly
    /// testable without a ModelContainer.
    nonisolated static func total(of values: [Decimal]) -> Decimal {
        values.reduce(Decimal.zero, +)
    }

    /// What the user still holds — the number under "Your finds are worth".
    ///
    /// `insights().unrealized`, not a second sum of its own. It used to total
    /// *every* row with no status filter, while `insights` routes `.sold` into
    /// `realized` and deliberately leaves it out of `unrealized` — so the
    /// headline counted a sold item's estimate while the line three rows below
    /// it said that same item was not held, and the estimate is not the sale
    /// price either, so the figure it contributed corresponded to no money
    /// anywhere.
    ///
    /// One item bought for $20, estimated $40–$60, sold for $100 rendered, on
    /// one card: "Your finds are worth $50.00" / "1 item scanned" / "$70.00
    /// realised · $0.00 still held". $50 reconciles with neither $70 nor $0.
    ///
    /// Deriving it from `insights` rather than adding a matching filter here is
    /// the point: two sums that must agree will not stay agreed.
    nonisolated static func portfolioTotal(of results: [ScanResult]) -> Decimal {
        insights(for: results).unrealized
    }

    func portfolioTotal(from results: [ScanResult]) -> Decimal {
        Self.portfolioTotal(of: results)
    }

    func totalValue(from results: [ScanResult]) -> String {
        Self.money(portfolioTotal(from: results))
    }

    /// Matches the formatting used by the ledger and Thrift Flip so the same
    /// amount never renders two ways in one app.
    nonisolated static func money(_ value: Decimal) -> String {
        NumberFormatter.snapCurrency.string(from: NSDecimalNumber(decimal: value)) ?? "$0"
    }

    // ── Portfolio insights ─────────────────────────────────────────────────────

    /// What the portfolio header can say that is both true and actionable.
    ///
    /// The total alone is passive — it only moves when you scan, so between
    /// visits there is nothing new to see. These three are derived from data
    /// already on the device and change as items move through the ledger, which
    /// is what makes the screen worth reopening.
    struct Insights: Equatable {
        /// Scanned but never marked owned/listed/sold — the pile you meant to
        /// do something with.
        let unlisted: Int
        /// Profit actually banked on sold items.
        let realized: Decimal
        /// Estimated value still sitting in things you hold.
        let unrealized: Decimal
        /// Longest hold among items not yet sold, in days.
        let oldestHoldDays: Int?
    }

    nonisolated static func insights(for results: [ScanResult],
                                     now: Date = Date()) -> Insights {
        var unlisted = 0
        var realized = Decimal.zero
        var unrealized = Decimal.zero
        var oldest: Int?

        for item in results {
            switch item.status {
            case .sold:
                // Nil when the buy price was never recorded — a sale with no
                // cost basis has no knowable profit, and counting it as zero
                // would quietly understate the real figure.
                realized += item.realizedProfit ?? 0
            case .scanned:
                unlisted += 1
                unrealized += item.portfolioValue
            case .owned, .listed:
                unrealized += item.portfolioValue
            }

            if item.status != .sold {
                let days = Calendar.current.dateComponents(
                    [.day], from: item.timestamp, to: now).day ?? 0
                if days > (oldest ?? -1) { oldest = days }
            }
        }
        return Insights(unlisted: unlisted, realized: realized,
                        unrealized: unrealized, oldestHoldDays: oldest)
    }

    func insights(from results: [ScanResult]) -> Insights {
        Self.insights(for: results)
    }

    /// One short line for the portfolio header, or nil when there is nothing
    /// worth saying. Deliberately at most one: a header that lists four
    /// statistics is a report, not a prompt.
    nonisolated static func insightLine(_ i: Insights) -> String? {
        if i.unlisted > 0 {
            return String(localized: "\(i.unlisted) finds you haven't listed yet")
        }
        if i.realized > 0 {
            return String(localized: "\(money(i.realized)) realised · \(money(i.unrealized)) still held")
        }
        if let days = i.oldestHoldDays, days >= 30 {
            return String(localized: "Held for \(days) days")
        }
        return nil
    }

    // ── Portfolio trend (Pro) ──────────────────────────────────────────────────

    /// One point on the portfolio trend line.
    struct TrendPoint: Equatable {
        let date: Date
        let total: Decimal
    }

    /// Portfolio total as of each scan date, oldest first.
    ///
    /// The series is *cumulative by acquisition*: walking scans in timestamp
    /// order and accumulating their current value answers "how has what I own
    /// grown", which is the question a portfolio view is asked. It deliberately
    /// does not try to reconstruct what the portfolio was historically worth —
    /// that would need a value snapshot for every item at every past date, and
    /// inventing one would produce a confident-looking line built on data we
    /// never recorded. A sale is a dated event the ledger did record, so a
    /// sold item leaves the line on its sold date — see `trendPairs`.
    ///
    /// Downsampled so the sparkline stays cheap and legible: a 500-item library
    /// renders the same number of points as a 20-item one.
    nonisolated static func trend(from pairs: [(date: Date, value: Decimal)],
                                  maxPoints: Int = 40) -> [TrendPoint] {
        guard !pairs.isEmpty else { return [] }
        let ordered = pairs.sorted { $0.date < $1.date }

        var running = Decimal.zero
        var points: [TrendPoint] = []
        points.reserveCapacity(ordered.count)
        for pair in ordered {
            running += pair.value
            points.append(TrendPoint(date: pair.date, total: running))
        }

        guard points.count > maxPoints else { return points }
        // Keep the last point: the current total must be the one on screen.
        let stride = Double(points.count - 1) / Double(maxPoints - 1)
        return (0..<maxPoints).map { points[Int((Double($0) * stride).rounded())] }
    }

    /// The sparkline in words, for VoiceOver: its first and last point. The
    /// line has no axes, so those two are all it says. Nil below two points,
    /// where no line is drawn.
    nonisolated static func trendSummary(_ points: [TrendPoint]) -> String? {
        guard points.count >= 2, let first = points.first, let last = points.last else { return nil }
        return String(localized: "\(money(first.total)) to \(money(last.total))")
    }

    func trendPoints(from results: [ScanResult]) -> [TrendPoint] {
        Self.trend(from: Self.trendPairs(for: results))
    }

    /// What the trend line is built from: each find entering at its scan
    /// date, and a sold one leaving again at its sale.
    ///
    /// Sold rows used to stay in for good. The headline became the held value
    /// alone (`portfolioTotal`), while the line summed every row ever scanned,
    /// so after a sale it ended above "Your finds are worth" — against the rule
    /// the line was built to, that its last point is the headline figure — and
    /// it could never fall. Leaving on the sold date keeps the history (it did
    /// rise when the item came in) and lands the line on the headline.
    ///
    /// A sold row with no sold date, or one dated before its own scan, is left
    /// out rather than entering and leaving at the same instant, which would
    /// draw a spike that is not there.
    nonisolated static func trendPairs(for results: [ScanResult]) -> [(date: Date, value: Decimal)] {
        var pairs: [(date: Date, value: Decimal)] = []
        pairs.reserveCapacity(results.count)
        for item in results {
            let value = item.portfolioValue
            guard item.status == .sold else {
                pairs.append((date: item.timestamp, value: value))
                continue
            }
            guard let sold = item.soldDate, sold > item.timestamp else { continue }
            pairs.append((date: item.timestamp, value: value))
            pairs.append((date: sold, value: -value))
        }
        return pairs
    }

    /// Signed change for a single item since it entered the portfolio, or nil
    /// when it has never been re-priced.
    func changeLabel(for result: ScanResult) -> String? {
        guard let change = result.valueChangeSinceAdded, change != 0 else { return nil }
        let base = Self.money(abs(change))
        return change > 0 ? "+\(base)" : "−\(base)"
    }

    func delete(_ result: ScanResult, repository: ScanRepository) {
        do {
            try repository.delete(result)
        } catch {
            deleteError = AppError.from(error).errorDescription
        }
    }
}
