import WidgetKit
import SwiftUI

// ── Shared data model (mirrors WidgetDataStore in the main app) ───────────────

struct WidgetHaulData: Codable {
    var totalLow: Double
    var totalHigh: Double
    var itemCount: Int
    var lastItemName: String
    var lastItemRange: String
    var updatedAt: Date

    var formattedRange: String {
        let lo = Self.currencyFormatter.string(from: NSNumber(value: totalLow))  ?? "$\(Int(totalLow))"
        let hi = Self.currencyFormatter.string(from: NSNumber(value: totalHigh)) ?? "$\(Int(totalHigh))"
        return "\(lo) – \(hi)"
    }

    private static let currencyFormatter: NumberFormatter = {
        let fmt = NumberFormatter()
        fmt.numberStyle = .currency
        fmt.currencyCode = "USD"
        fmt.maximumFractionDigits = 0
        return fmt
    }()

    static let empty = WidgetHaulData(
        totalLow: 0, totalHigh: 0, itemCount: 0,
        lastItemName: "", lastItemRange: "",
        updatedAt: .distantPast
    )
}

// ── App-Group reader ──────────────────────────────────────────────────────────

enum WidgetReader {
    private static let appGroupID = "group.eu.snapworth.app"
    private static let haulKey    = "snapworth.widget.haul"

    static func readHaul() -> WidgetHaulData {
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
