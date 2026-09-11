import Foundation
import SwiftData
import SwiftUI
import WidgetKit

// ── Shared data model ─────────────────────────────────────────────────────────
// This struct is also duplicated inside SnapWorthWidgets (widget targets can't
// import the main app's module), so keep both in sync if you add fields.

struct WidgetHaulData: Codable {
    var totalLow:      Double
    var totalHigh:     Double
    var itemCount:     Int
    var lastItemName:  String
    var lastItemRange: String
    var updatedAt:     Date

    static let empty = WidgetHaulData(
        totalLow: 0, totalHigh: 0, itemCount: 0,
        lastItemName: "", lastItemRange: "",
        updatedAt: .distantPast
    )

    var formattedRange: String {
        guard itemCount > 0 else { return "$0" }
        let fmt = NumberFormatter.snapCurrency
        let lo = fmt.string(from: NSNumber(value: totalLow))  ?? "$\(Int(totalLow))"
        let hi = fmt.string(from: NSNumber(value: totalHigh)) ?? "$\(Int(totalHigh))"
        return "\(lo) – \(hi)"
    }
}

// ── Store ─────────────────────────────────────────────────────────────────────

enum WidgetDataStore {
    static let appGroupID = "group.eu.snapworth.app"
    static let haulKey    = "snapworth.widget.haul"

    /// Call this after any insert/delete of ScanResults in the main app.
    static func writeHaul(results: [ScanResult]) {
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

        let data = WidgetHaulData(
            totalLow:      lo,
            totalHigh:     hi,
            itemCount:     results.count,
            lastItemName:  last?.itemName      ?? "",
            lastItemRange: last?.formattedRange ?? "",
            updatedAt:     Date()
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
