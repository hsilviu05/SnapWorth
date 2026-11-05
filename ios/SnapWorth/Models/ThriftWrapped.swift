import Foundation
import StoreKit

/// A thrifter's year in finds, for the Year in Finds share card.
///
/// Built from the library on the phone and nothing else: no request, no new
/// data leaving the device. Pure and free of SwiftData and the view, like
/// `LedgerMath`, so every rule is testable with plain rows.
///
/// The card only ever carries what the app already shows elsewhere, by the
/// same rules: estimates are `portfolioValue` (dollars, like every estimate),
/// and the year's flips are `LedgerMath.sales`, per currency and never added
/// across them.
struct ThriftWrapped: Equatable, Identifiable {
    let year: Int
    /// Finds scanned in `year`, sold or not.
    let scanCount: Int
    /// The summed likely value of those finds, in dollars.
    let estimatedTotal: Decimal
    let topFindName: String
    let topFindValue: Decimal
    /// The brand seen most often, when it was seen at least twice. One
    /// sighting is not a favourite, and "Unknown" never is.
    let topBrand: String?
    /// The first day of the month with the most scans; the earliest on a tie.
    let busiestMonth: Date?
    /// The year's sales, by `soldDate`. Pro shows the profit on the card.
    let sales: LedgerMath.Sales

    var id: Int { year }

    /// Fewer than this and the card would be thin; nothing is offered.
    static let minimumScans = 3

    /// The year being wrapped on `now`, or nil outside the season: December
    /// wraps its own year, and January the one just ended.
    static func seasonYear(now: Date = Date(), calendar: Calendar = .current) -> Int? {
        let parts = calendar.dateComponents([.year, .month], from: now)
        guard let year = parts.year, let month = parts.month else { return nil }
        switch month {
        case 12: return year
        case 1: return year - 1
        default: return nil
        }
    }

    /// The year My Finds offers, if any. A TestFlight or Xcode build offers
    /// the current year all year round, so the card can be tested before
    /// December; an App Store build only in season.
    static func offeredYear(now: Date = Date(), calendar: Calendar = .current,
                            isTestBuild: Bool) -> Int? {
        if let year = seasonYear(now: now, calendar: calendar) { return year }
        return isTestBuild ? calendar.component(.year, from: now) : nil
    }

    /// Whether this install came from TestFlight or Xcode, from the App Store's
    /// own signed record. Unverifiable or unknown reads as the App Store.
    static func isTestBuild() async -> Bool {
        guard case .verified(let transaction) = try? await AppTransaction.shared else { return false }
        return transaction.environment == .sandbox || transaction.environment == .xcode
    }

    init?(results: [ScanResult], year: Int, calendar: Calendar = .current,
          locale: Locale = .current) {
        guard let start = calendar.date(from: DateComponents(year: year, month: 1, day: 1)),
              let interval = calendar.dateInterval(of: .year, for: start) else { return nil }

        let found = results.filter { LedgerMath.contains(interval, $0.timestamp) }
        guard found.count >= Self.minimumScans,
              let top = found.max(by: { $0.portfolioValue < $1.portfolioValue }) else { return nil }

        self.year = year
        scanCount = found.count
        estimatedTotal = LedgerMath.total(found.map(\.portfolioValue))
        topFindName = top.itemName
        topFindValue = top.portfolioValue
        topBrand = Self.topBrand(found)
        busiestMonth = Self.busiestMonth(found, calendar: calendar)
        sales = LedgerMath.sales(LedgerMath.sold(results, in: interval), locale: locale)
    }

    /// Whether the year has a profit to print: a sale with a paid and a sold
    /// price. The same rule as the month card (`canShareMonth`).
    var hasProfit: Bool { sales.priced > 0 }

    static func topBrand(_ found: [ScanResult]) -> String? {
        var counts: [String: (name: String, count: Int)] = [:]
        for result in found where SaleOutcomePayload.identifiesBrand(result) {
            let name = result.brand.trimmingCharacters(in: .whitespaces)
            let key = name.lowercased()
            counts[key] = (counts[key]?.name ?? name, (counts[key]?.count ?? 0) + 1)
        }
        // Ties go to the alphabetically first, so the card is the same on
        // every render.
        let best = counts.values
            .filter { $0.count >= 2 }
            .max { $0.count != $1.count ? $0.count < $1.count : $0.name > $1.name }
        return best?.name
    }

    static func busiestMonth(_ found: [ScanResult], calendar: Calendar) -> Date? {
        var counts: [Date: Int] = [:]
        for result in found {
            if let month = LedgerMath.month(containing: result.timestamp, calendar: calendar)?.start {
                counts[month, default: 0] += 1
            }
        }
        return counts.max { $0.value != $1.value ? $0.value < $1.value : $0.key > $1.key }?.key
    }
}
