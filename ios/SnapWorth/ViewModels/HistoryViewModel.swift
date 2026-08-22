import SwiftUI
import SwiftData

enum HistorySortOrder: String, CaseIterable {
    case newest = "Newest"
    case mostValuable = "Most Valuable"
}

@MainActor
@Observable
final class HistoryViewModel {
    var searchText: String = ""
    var sortOrder: HistorySortOrder = .newest
    var deleteError: String?

    func sorted(_ results: [ScanResult]) -> [ScanResult] {
        switch sortOrder {
        case .newest:
            return results.sorted { $0.timestamp > $1.timestamp }
        case .mostValuable:
            return results.sorted { $0.midpointValue > $1.midpointValue }
        }
    }

    func filtered(_ results: [ScanResult]) -> [ScanResult] {
        let sorted = sorted(results)
        guard !searchText.isEmpty else { return sorted }
        return sorted.filter {
            $0.itemName.localizedCaseInsensitiveContains(searchText) ||
            $0.brand.localizedCaseInsensitiveContains(searchText)
        }
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

    func portfolioTotal(from results: [ScanResult]) -> Decimal {
        Self.total(of: results.map { $0.priceRange(for: $0.condition).likely })
    }

    func totalValue(from results: [ScanResult]) -> String {
        Self.money(portfolioTotal(from: results))
    }

    /// Matches the formatting used by the ledger and Thrift Flip so the same
    /// amount never renders two ways in one app.
    nonisolated static func money(_ value: Decimal) -> String {
        NumberFormatter.snapCurrency.string(from: NSDecimalNumber(decimal: value)) ?? "$0"
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
    /// never recorded.
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

    func trendPoints(from results: [ScanResult]) -> [TrendPoint] {
        Self.trend(from: results.map { (date: $0.timestamp, value: $0.portfolioValue) })
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
