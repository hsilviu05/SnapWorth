import Foundation

enum Config {
    // ── API ──────────────────────────────────────────────────────────────────
    /// Set to your deployed backend URL before submitting to the App Store.
    static let baseURL = URL(string: "https://api.snapworth.eu")!

    /// When true, ScanAPIClient returns canned JSON — no network required.
    /// Flip to false once your backend is deployed and the URL above is set.
    static let mockMode = false

    // ── Subscription ─────────────────────────────────────────────────────────
    static let monthlyProductID = "eu.snapworth.monthly"
    static let yearlyProductID  = "eu.snapworth.yearly"

    // ── Free tier ────────────────────────────────────────────────────────────
    static let freeScansAllowed = 3

    // ── RevenueCat ───────────────────────────────────────────────────────────
    /// Paste your RevenueCat iOS public SDK key here.
    static let revenueCatAPIKey = "appl_REPLACE_WITH_YOUR_IOS_KEY"
}
