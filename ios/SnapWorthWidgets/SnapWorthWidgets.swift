import ActivityKit
import WidgetKit
import SwiftUI

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
    // From `SnapDarkHex` in the shared model, which `DesignSystem.swift` also
    // reads — see the comment there for what these were and why they moved.
    // `wAmber` and `wEspresso` were declared here and used by nothing; dead
    // palette entries invite the next person to reach for one.
    static let wBackground     = Color(hex: SnapDarkHex.cream)
    static let wCharcoal       = Color(hex: SnapDarkHex.charcoal)
    static let wTerracotta     = Color(hex: SnapDarkHex.terracotta)
    static let wSage           = Color(hex: SnapDarkHex.sage)
    static let wWarmGray       = Color(hex: SnapDarkHex.warmGray)

    /// For a filled accent surface with cream on top, never for a foreground.
    static let wTerracottaFill     = Color(hex: SnapDarkHex.terracottaFill)
    static let wTerracottaFillDeep = Color(hex: SnapDarkHex.terracottaFillDeep)

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
