import WidgetKit
import SwiftUI

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
// ── END SHARED WIDGET MODEL ──────────────────────────────────────────────────

// ── App-Group reader ──────────────────────────────────────────────────────────

enum WidgetReader {
    static func readHaul() -> WidgetHaulData {
        let appGroupID = WidgetBridge.appGroupID
        let haulKey = WidgetBridge.haulKey
        guard
            let suite = UserDefaults(suiteName: appGroupID),
            let data  = suite.data(forKey: haulKey),
            let haul  = try? JSONDecoder().decode(WidgetHaulData.self, from: data)
        else { return .empty }
        return haul
    }
}

// ── Color palette ─────────────────────────────────────────────────────────────

extension Color {
    static let wBackground = Color(hex: "FAF9F7")
    static let wCharcoal   = Color(hex: "2C2C2C")
    static let wTerracotta = Color(hex: "C9583A")
    static let wSage       = Color(hex: "7D9E7E")
    static let wAmber      = Color(hex: "D4913A")
    static let wEspresso   = Color(hex: "3D1E10")
    static let wWarmGray   = Color(hex: "8A857E")

    init(hex: String) {
        let h = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var int: UInt64 = 0
        Scanner(string: h).scanHexInt64(&int)
        let a, r, g, b: UInt64
        switch h.count {
        case 3:  (a, r, g, b) = (255, (int >> 8) * 17, (int >> 4 & 0xF) * 17, (int & 0xF) * 17)
        case 6:  (a, r, g, b) = (255, int >> 16, int >> 8 & 0xFF, int & 0xFF)
        case 8:  (a, r, g, b) = (int >> 24, int >> 16 & 0xFF, int >> 8 & 0xFF, int & 0xFF)
        default: (a, r, g, b) = (255, 0, 0, 0)
        }
        self.init(.sRGB,
                  red:   Double(r) / 255,
                  green: Double(g) / 255,
                  blue:  Double(b) / 255,
                  opacity: Double(a) / 255)
    }
}
