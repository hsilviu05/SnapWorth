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

    static let empty = WidgetHaulData(
        totalLow: 0, totalHigh: 0, itemCount: 0,
        lastItemName: "", lastItemRange: "", updatedAt: .distantPast,
        freeScansRemaining: nil, isPro: false, streak: 0,
        recentFinds: [], monthProfit: nil, monthFlips: 0
    )

    var hasScans: Bool { itemCount > 0 }

    var formattedRange: String {
        guard hasScans else { return "$0" }
        return "\(Self.money(totalLow)) – \(Self.money(totalHigh))"
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
    }

    init(totalLow: Double, totalHigh: Double, itemCount: Int,
         lastItemName: String, lastItemRange: String, updatedAt: Date,
         freeScansRemaining: Int?, isPro: Bool, streak: Int,
         recentFinds: [WidgetFind], monthProfit: Double?, monthFlips: Int) {
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
    static func compactMoney(_ value: Double) -> String {
        let dollars = value.rounded()
        guard abs(dollars) >= 1_000 else { return "$\(Int(dollars))" }

        let thousands = (dollars / 100).rounded() / 10
        guard abs(thousands) >= 10 else {
            return "$\(String(format: "%.1f", thousands))K"
        }
        return "$\(Int(thousands.rounded()))K"
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
// ── END SHARED WIDGET MODEL ──────────────────────────────────────────────────

// ── Store ─────────────────────────────────────────────────────────────────────

enum WidgetDataStore {
    static let appGroupID = WidgetBridge.appGroupID
    static let haulKey = WidgetBridge.haulKey

    /// Call this after any insert/delete of ScanResults in the main app.
    ///
    /// `isPro` defaults to nil meaning *carry forward whatever is already
    /// stored*. Most callers are repository writes that have no idea about
    /// entitlement, and clobbering it to `false` on every scan would blank a
    /// paying subscriber's Pro widgets until they next opened the paywall.
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

        let previous = readHaul()
        let pro = isPro ?? previous.isPro

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
            monthFlips:    pro ? monthProfits.count : 0
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
