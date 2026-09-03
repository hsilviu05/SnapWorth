import Foundation

// ═══════════════════════════════════════════════════════════════════
// MARK: - Funnel definition (single source of truth for event names)
// ═══════════════════════════════════════════════════════════════════

/// Every analytics event the app can emit. This enum **is** the funnel
/// definition — wire names and parameters live here and nowhere else, so an
/// event name can never drift between call sites.
///
/// The launch funnel, in order:
///   app_opened → scan_started → free_scan_limit_hit → paywall_viewed →
///   purchase_completed
///
/// Rules: no PII ever. Categories come from the fixed `ItemCategory` enum;
/// amounts and item names are never included.
enum AnalyticsEvent {
    // ── Launch funnel ────────────────────────────────────────────────
    case appOpened
    case scanStarted
    case scanCompleted(success: Bool, category: ItemCategory?)
    case scanFailed(reason: ScanFailureReason)
    case freeScanLimitHit
    /// A scan advanced (or restarted) the day streak. Bucketed, never exact.
    case scanStreak(bucket: String)
    case paywallViewed(trigger: PaywallTrigger)
    case purchaseStarted(productID: String)
    /// Fires on the confirmed StoreKit transaction — never on a button tap.
    case purchaseCompleted(productID: String)
    case purchaseFailed(productID: String, reason: String)
    case restoreCompleted
    case shareCardOpened
    case shareCardShared(activityType: String?)

    // ── Snap → Sell ──────────────────────────────────────────────────
    case listingGenerated(marketplace: String)

    // ── Thrift Flip ──────────────────────────────────────────────────
    case thriftFlipCalculated(verdict: String)

    // ── My Flips ledger ──────────────────────────────────────────────
    case ledgerItemMarkedSold
    case ledgerDashboardViewed
    case ledgerExportTapped
    case ledgerPaywallHit(trigger: PaywallTrigger)
    case ledgerMonthShared

    // ── Local notifications ──────────────────────────────────────────
    case notificationScheduled(category: String)
    case notificationOpened(category: String)

    // ── Stability (MetricKit) ────────────────────────────────────────
    /// A crash reported by MetricKit on a later launch. Signal and termination
    /// are both bucketed — never raw call stacks or termination text.
    case crashReported(signal: String, termination: String)
    /// An applications-not-responding hang, bucketed by duration.
    case hangReported(bucket: String)
    /// Time-to-first-draw, bucketed. Detects a launch regression.
    case launchTimeReported(bucket: String)

    // ── Transport security ───────────────────────────────────────────
    /// The API's TLS chain validated but matched none of the pinned keys.
    ///
    /// Pinning runs in report-only mode until a full release cycle shows zero
    /// of these in the field. Before this event existed the mismatch went to
    /// `os_log` only — which nobody can read from the field — so the
    /// precondition for enforcing pins could never be observed and the flag
    /// would have stayed off forever. `enforced` says whether the request was
    /// refused (true) or allowed through in report-only mode (false). No host,
    /// no certificate details: the app has one API host.
    case certificatePinMismatch(enforced: Bool)

    // ── Persistence health ───────────────────────────────────────────
    /// The on-disk store failed to open and the app fell back to in-memory.
    ///
    /// The user's entire scan history appears empty and that session's work is
    /// lost on quit. Without this event a data-loss launch is indistinguishable
    /// from a healthy one — the failure only surfaces later, as a review.
    case persistentStoreFallback(reason: String)

    /// The wire name sent off-device.
    var name: String {
        switch self {
        case .appOpened:            return "app_opened"
        case .scanStarted:          return "scan_started"
        case .scanCompleted:        return "scan_completed"
        case .scanFailed:           return "scan_failed"
        case .freeScanLimitHit:     return "free_scan_limit_hit"
        case .scanStreak:           return "scan_streak"
        case .paywallViewed:        return "paywall_viewed"
        case .purchaseStarted:      return "purchase_started"
        case .purchaseCompleted:    return "purchase_completed"
        case .purchaseFailed:       return "purchase_failed"
        case .restoreCompleted:     return "restore_completed"
        case .shareCardOpened:      return "share_card_opened"
        case .shareCardShared:      return "share_card_shared"
        case .listingGenerated:     return "listing_generated"
        case .thriftFlipCalculated: return "thrift_flip_calculated"
        case .ledgerItemMarkedSold: return "ledger_item_marked_sold"
        case .ledgerDashboardViewed:return "ledger_dashboard_viewed"
        case .ledgerExportTapped:   return "ledger_export_tapped"
        case .ledgerPaywallHit:     return "ledger_paywall_hit"
        case .ledgerMonthShared:    return "ledger_month_shared"
        case .notificationScheduled:return "notification_scheduled"
        case .notificationOpened:   return "notification_opened"
        case .persistentStoreFallback: return "persistent_store_fallback"
        case .certificatePinMismatch: return "certificate_pin_mismatch"
        case .crashReported:        return "crash_reported"
        case .hangReported:         return "hang_reported"
        case .launchTimeReported:   return "launch_time_reported"
        }
    }

    /// PII-free parameters. Only enums, product SKUs and booleans — never
    /// prices, item names, images or user identifiers.
    var parameters: [String: String] {
        switch self {
        case let .scanCompleted(success, category):
            var p = ["success": String(success)]
            if let category { p["item_category"] = category.rawValue }
            return p
        case let .scanFailed(reason):
            return ["reason": reason.rawValue]
        case let .paywallViewed(trigger), let .ledgerPaywallHit(trigger):
            return ["trigger": trigger.rawValue]
        case let .purchaseStarted(productID), let .purchaseCompleted(productID):
            return ["product_id": productID]
        case let .purchaseFailed(productID, reason):
            return ["product_id": productID, "reason": reason]
        case let .shareCardShared(activityType):
            if let activityType { return ["activity_type": activityType] }
            return [:]
        case let .notificationScheduled(category), let .notificationOpened(category):
            return ["category": category]
        case let .listingGenerated(marketplace):
            return ["marketplace": marketplace]
        case let .thriftFlipCalculated(verdict):
            return ["verdict": verdict]
        case let .crashReported(signal, termination):
            return ["signal": signal, "termination": termination]
        case let .hangReported(bucket), let .launchTimeReported(bucket), let .scanStreak(bucket):
            return ["bucket": bucket]
        case let .persistentStoreFallback(reason):
            // A coarse error classification only — never the raw error string,
            // which can embed a filesystem path containing the device owner's
            // name.
            return ["reason": reason]
        case let .certificatePinMismatch(enforced):
            return ["enforced": String(enforced)]
        default:
            return [:]
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - Fixed enums (keep payloads bounded & PII-free)
// ═══════════════════════════════════════════════════════════════════

/// Fixed set of item categories. The backend returns a free-form string; we
/// normalize to this closed set so analytics never leaks an unexpected value.
enum ItemCategory: String, CaseIterable {
    case clothing, shoes, accessories, bags, electronics
    case home, collectibles, media, toys, beauty, other

    /// Buckets a raw backend category into the fixed set; unknown ⇒ `.other`.
    init(normalizing raw: String) {
        let key = raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        switch key {
        case "clothing", "clothes", "apparel", "menswear", "womenswear":
            self = .clothing
        case "shoes", "sneakers", "footwear":
            self = .shoes
        case "accessories", "accessory", "jewelry", "watches", "watch":
            self = .accessories
        case "bags", "bag", "handbags", "handbag", "purse", "purses":
            self = .bags
        case "electronics", "electronic", "tech", "gadgets":
            self = .electronics
        case "home", "furniture", "homeware", "home goods", "kitchen", "decor":
            self = .home
        case "collectibles", "collectible", "antiques", "art", "vintage":
            self = .collectibles
        case "media", "books", "book", "music", "vinyl", "games", "video games":
            self = .media
        case "toys", "toy", "figures", "figure":
            self = .toys
        case "beauty", "cosmetics", "fragrance", "makeup":
            self = .beauty
        default:
            self = .other
        }
    }
}

/// The three failure buckets the funnel cares about.
enum ScanFailureReason: String {
    case network
    case noResult = "no_result"
    case permission

    /// Maps an `AppError` onto a coarse funnel reason.
    init(_ error: AppError) {
        switch error {
        case .network, .timeout:
            self = .network
        default:
            // rateLimit / serverUnavailable / imageEncodingFailed /
            // persistence / unknown all mean "we couldn't return a result".
            self = .noResult
        }
    }
}

/// Every place a paywall can be shown. Single source so triggers can't drift.
enum PaywallTrigger: String {
    case onboarding
    case scanLimit     = "scan_limit"
    case upgradeButton = "upgrade_button"
    case settings
    case ledgerHistory = "ledger_history"
    case ledgerExport  = "ledger_export"
    case snapSell      = "snap_sell"
    case thriftFlip    = "thrift_flip"
    case portfolioTrend = "portfolio_trend"
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - Analytics facade (gated, no-op until configured)
// ═══════════════════════════════════════════════════════════════════

/// Anything that can receive events. Kept SDK-free so the app never imports a
/// vendor SDK outside `TelemetryDeckAnalytics`, and so tests can inject a spy.
protocol AnalyticsService: AnyObject {
    func track(_ event: AnalyticsEvent)
}

/// The single entry point for analytics. Every call site uses
/// `Analytics.shared.track(.someEvent)`.
///
/// Two gates, both respected everywhere:
///  1. No backend configured (e.g. no TelemetryDeck app ID) ⇒ no-op.
///  2. User toggle off (`isEnabled == false`) ⇒ no-op.
final class Analytics {
    static let shared = Analytics()
    private init() {}

    /// Persisted opt-out. Defaults to on; flipping it off silences everything.
    static let enabledKey = "snapworth_analytics_enabled"

    var isEnabled: Bool {
        get { UserDefaults.standard.object(forKey: Self.enabledKey) as? Bool ?? true }
        set { UserDefaults.standard.set(newValue, forKey: Self.enabledKey) }
    }

    private var backend: AnalyticsService?

    /// Installs the concrete backend. Called once at launch.
    func configure(_ service: AnalyticsService) {
        backend = service
    }

    func track(_ event: AnalyticsEvent) {
        guard isEnabled else { return }
        backend?.track(event)
    }
}
