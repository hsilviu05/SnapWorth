import ActivityKit
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

    var scansLeft: ScansLeft {
        if isPro { return .pro(streak: streak) }
        guard let left = freeScansRemaining else { return .unknown }
        return .remaining(left)
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
            return "\(WidgetHaulData.money(totalLow)) – \(WidgetHaulData.money(totalHigh))"
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
