import Foundation

// ═══════════════════════════════════════════════════════════════════
// MARK: - Funnel definition (single source of truth for event names)
// ═══════════════════════════════════════════════════════════════════

/// Every analytics event the app can emit. This enum **is** the funnel
/// definition — wire names and parameters live here and nowhere else, so an
/// event name can never drift between call sites.
///
/// The launch funnel, in order:
///   app_opened → onboarding_started → onboarding_completed →
///   scan_started → scan_result_shown → free_scan_limit_hit →
///   paywall_viewed → purchase_completed / paywall_dismissed
///
/// **Day-0 is a parameter, not a second set of events.** Six events carry
/// `is_first`, so a first-run funnel is the same query with one filter rather
/// than a parallel family of `first_*` names that a future call site could
/// forget to emit. `ScanTally` decides what "first" means, in one place:
/// `isFirstScan()` for an event read before the scan is recorded,
/// `isFirstRun()` for one that can only fire after it.
///
/// Rules: no PII ever. Categories come from the fixed `ScanCategory` enum;
/// amounts and item names are never included.
enum AnalyticsEvent {
    // ── Launch funnel ────────────────────────────────────────────────
    case appOpened
    /// The first onboarding slide appeared. Fires once per install, before
    /// anything else the user could do — onboarding had no instrumentation at
    /// all until now, so a user who never reached the camera was invisible.
    case onboardingStarted
    /// Onboarding ended, and how: `finished` walked to the last slide,
    /// `skipped` used the Skip control.
    case onboardingCompleted(via: OnboardingExit)
    case scanStarted(isFirst: Bool)
    case scanCompleted(success: Bool, category: ScanCategory?)
    /// A valuation was actually put in front of the user. Distinct from
    /// `scan_completed`, which fires when the response arrives: between the two
    /// sit persistence, encoding and sheet presentation.
    case scanResultShown(isFirst: Bool)
    case scanFailed(reason: ScanFailureReason, isFirst: Bool)
    /// The user's Nth successful scan, at the rungs in `ScanTally.milestones`.
    case scanCountMilestone(count: Int)
    case freeScanLimitHit
    /// A scan advanced (or restarted) the day streak. Bucketed, never exact.
    case scanStreak(bucket: String)
    case paywallViewed(trigger: PaywallTrigger, isFirst: Bool)
    /// The paywall closed without a purchase. Pairs with `paywall_viewed` to
    /// give a look-to-buy rate; a purchase closes it through
    /// `purchase_completed` instead and does not emit this.
    case paywallDismissed(trigger: PaywallTrigger)
    case purchaseStarted(productID: String, isFirst: Bool)
    /// Fires on the confirmed StoreKit transaction — never on a button tap.
    case purchaseCompleted(productID: String, isFirst: Bool)
    case purchaseFailed(productID: String, reason: String)
    case restoreCompleted
    /// StoreKit shows a subscription the server would not honour, or the
    /// routine sync that tells the server failed. `reason` is a fixed bucket
    /// (`EntitlementSyncFailure`). Until this existed a subscriber sent to the
    /// paywall by a failed sync left no trace anywhere the operator looks.
    case entitlementSyncFailed(reason: String)
    case shareCardOpened
    case shareCardShared(activityType: String?)
    /// "Guess the price": the estimate was revealed in the game, with or
    /// without the user having typed a guess first.
    case guessRevealed(withGuess: Bool)
    /// A guess card left through the share sheet: "pair" (question + reveal),
    /// "guess" (question only) or "reveal".
    case guessCardShared(style: String)
    /// A label close-up was sent with a re-read (#88).
    case tagPhotoAdded(succeeded: Bool)

    // ── Snap → Sell ──────────────────────────────────────────────────
    case listingGenerated(marketplace: String)
    case listingPhotoCleaned(marketplace: String)
    /// A listing reached the clipboard. `marketplace` is the generated
    /// listing's, or "draft" for the plain draft on every result.
    case listingCopied(marketplace: String)
    /// A generated listing left through the share sheet (completed, not
    /// merely opened).
    case listingShared(marketplace: String)
    /// "Open <marketplace>" under a generated listing.
    case marketplaceOpened(marketplace: String)

    // ── Widgets ──────────────────────────────────────────────────────
    /// The app was opened from a widget, Live Activity or control. `source`
    /// is a `WidgetSource` — a closed set, never the raw query.
    case widgetOpened(source: String)
    /// Once a day: how many of this app's widgets are placed, bucketed, and
    /// which kinds.
    case widgetsInstalled(count: String, kinds: String)

    // ── Referrals (#97) ──────────────────────────────────────────────
    //
    // Taps, named as taps. These were `referral_shared`, `referral_redeemed`
    // and `referral_rewarded`, and none of them was what it said: the first
    // fired on the Share button before the sheet opened, the second when the
    // server accepted a code and before Apple's sheet, the third on the
    // Redeem button. An offer-code redemption never passes through the app's
    // purchase flow, so the app cannot see one at all — the server counts
    // redemptions, rewards and paid periods (RUNBOOK §18). Renamed before
    // referrals were ever switched on, so no dashboard held the old names.

    /// The Share invite button was tapped; the share sheet opened.
    case referralShareOpened
    /// The server accepted a friend's invite code and the app opened Apple's
    /// redemption page. Not a redemption: the friend can still cancel there.
    case referralCodeAccepted
    /// Apple's redemption page was opened for a week the user earned.
    /// `source` is `alert` (the "You earned a week" alert) or `invite_screen`.
    case referralRewardOpened(source: String)

    // ── Thrift Flip ──────────────────────────────────────────────────
    case thriftFlipCalculated(verdict: String)

    // ── Haul mode (#93) ──────────────────────────────────────────────
    /// The haul summary was reached. `items` is a bucket from
    /// `haulSizeBucket`, never the count — see there.
    ///
    /// Counts valued *and* still-pending photos: a haul whose last few are
    /// waiting out a rate limit is still a haul of that size, and the pending
    /// ones are valued, saved and counted as scans when they land. Counting
    /// only the valued ones would shrink exactly the hauls big enough to hit
    /// the limit.
    ///
    /// Once per haul at its first Finish, so a kill from the switcher does
    /// not lose it. A haul that grows into a larger bucket after "Keep
    /// scanning" reports again with `revised_from`, the bucket it replaces:
    /// hauls are the events without it, and each revision moves one haul
    /// from `revised_from` to `items`.
    case haulCompleted(itemsBucket: String, revisedFrom: String? = nil)
    /// The haul card left through the share sheet. Only a completed share.
    case haulShared

    // ── My Flips ledger ──────────────────────────────────────────────
    case ledgerItemMarkedSold
    /// The step before a sale, which the funnel could not see: only `sold`
    /// was recorded. Named to pair with `ledger_item_marked_sold`.
    case ledgerItemMarkedListed
    case ledgerDashboardViewed
    case ledgerExportTapped
    case ledgerPaywallHit(trigger: PaywallTrigger)
    case ledgerMonthShared

    // ── Local notifications ──────────────────────────────────────────
    case notificationScheduled(category: String)
    case notificationOpened(category: String)

    // ── Ratings ──────────────────────────────────────────────────────
    /// `ReviewPrompt` asked iOS for a rating prompt. Whether iOS showed one is
    /// not knowable — it answers nothing — so this counts requests, which is
    /// what the 60-day gap and the three-a-year budget are spent in.
    case reviewPromptRequested

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
        case .onboardingStarted:    return "onboarding_started"
        case .onboardingCompleted:  return "onboarding_completed"
        case .scanStarted:          return "scan_started"
        case .scanCompleted:        return "scan_completed"
        case .scanResultShown:      return "scan_result_shown"
        case .scanFailed:           return "scan_failed"
        case .scanCountMilestone:   return "scan_count_milestone"
        case .freeScanLimitHit:     return "free_scan_limit_hit"
        case .scanStreak:           return "scan_streak"
        case .paywallViewed:        return "paywall_viewed"
        case .paywallDismissed:     return "paywall_dismissed"
        case .purchaseStarted:      return "purchase_started"
        case .purchaseCompleted:    return "purchase_completed"
        case .purchaseFailed:       return "purchase_failed"
        case .restoreCompleted:     return "restore_completed"
        case .entitlementSyncFailed: return "entitlement_sync_failed"
        case .shareCardOpened:      return "share_card_opened"
        case .shareCardShared:      return "share_card_shared"
        case .guessRevealed:        return "guess_revealed"
        case .guessCardShared:      return "guess_card_shared"
        case .tagPhotoAdded:        return "tag_photo_added"
        case .listingGenerated:     return "listing_generated"
        case .listingPhotoCleaned:  return "listing_photo_cleaned"
        case .listingCopied:        return "listing_copied"
        case .listingShared:        return "listing_shared"
        case .marketplaceOpened:    return "marketplace_opened"
        case .widgetOpened:         return "widget_opened"
        case .widgetsInstalled:     return "widgets_installed"
        case .referralShareOpened:  return "referral_share_opened"
        case .referralCodeAccepted: return "referral_code_accepted"
        case .referralRewardOpened: return "referral_reward_opened"
        case .thriftFlipCalculated: return "thrift_flip_calculated"
        case .haulCompleted:        return "haul_completed"
        case .haulShared:           return "haul_shared"
        case .ledgerItemMarkedSold: return "ledger_item_marked_sold"
        case .ledgerItemMarkedListed: return "ledger_item_marked_listed"
        case .ledgerDashboardViewed:return "ledger_dashboard_viewed"
        case .ledgerExportTapped:   return "ledger_export_tapped"
        case .ledgerPaywallHit:     return "ledger_paywall_hit"
        case .ledgerMonthShared:    return "ledger_month_shared"
        case .notificationScheduled:return "notification_scheduled"
        case .notificationOpened:   return "notification_opened"
        case .reviewPromptRequested: return "review_prompt_requested"
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
        case let .scanFailed(reason, isFirst):
            return ["reason": reason.rawValue, "is_first": String(isFirst)]
        case let .scanStarted(isFirst), let .scanResultShown(isFirst):
            return ["is_first": String(isFirst)]
        case let .scanCountMilestone(count):
            return ["count": String(count)]
        case let .onboardingCompleted(via):
            return ["via": via.rawValue]
        case let .paywallViewed(trigger, isFirst):
            return ["trigger": trigger.rawValue, "is_first": String(isFirst)]
        case let .ledgerPaywallHit(trigger), let .paywallDismissed(trigger):
            return ["trigger": trigger.rawValue]
        case let .purchaseStarted(productID, isFirst), let .purchaseCompleted(productID, isFirst):
            return ["product_id": productID, "is_first": String(isFirst)]
        case let .purchaseFailed(productID, reason):
            return ["product_id": productID, "reason": reason]
        case let .entitlementSyncFailed(reason):
            return ["reason": reason]
        case let .shareCardShared(activityType):
            if let activityType { return ["activity_type": activityType] }
            return [:]
        case let .guessRevealed(withGuess):
            return ["with_guess": String(withGuess)]
        case let .tagPhotoAdded(succeeded):
            return ["succeeded": String(succeeded)]
        case let .guessCardShared(style):
            return ["style": style]
        case let .notificationScheduled(category), let .notificationOpened(category):
            return ["category": category]
        case let .listingGenerated(marketplace), let .listingPhotoCleaned(marketplace),
             let .listingCopied(marketplace), let .listingShared(marketplace),
             let .marketplaceOpened(marketplace):
            return ["marketplace": marketplace]
        case let .widgetOpened(source), let .referralRewardOpened(source):
            return ["source": source]
        case let .widgetsInstalled(count, kinds):
            return ["count": count, "kinds": kinds]
        case let .thriftFlipCalculated(verdict):
            return ["verdict": verdict]
        case let .haulCompleted(bucket, revisedFrom):
            guard let revisedFrom else { return ["items": bucket] }
            return ["items": bucket, "revised_from": revisedFrom]
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

    /// Coarse buckets for `haul_completed` — never the exact count, for the
    /// same reason `ScanStreak.bucket` exists: a closed set keeps the payload
    /// bounded, and an exact number of items photographed in one sitting says
    /// more about one person than a funnel needs to know.
    ///
    /// The edges follow what a haul is: one item is barely a haul, a bag from
    /// one shop is a handful, and a sourcing trip is ten or more.
    nonisolated static func haulSizeBucket(_ count: Int) -> String {
        switch count {
        case ..<2:   return "1"
        case 2...4:  return "2-4"
        case 5...9:  return "5-9"
        case 10...14: return "10-14"
        default:     return "15+"
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - Fixed enums (keep payloads bounded & PII-free)
// ═══════════════════════════════════════════════════════════════════

// Item categories are `ScanCategory` (ScanAPIClient.swift): the closed set the
// scan prompt offers, normalised the way the server's tallies are, so a
// category never carries an unexpected value into analytics.

/// The three failure buckets the funnel cares about.
/// How onboarding ended. Two values, because "did they read it or bail?" is
/// the question the slides exist to answer.
enum OnboardingExit: String {
    case finished
    case skipped
}

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
    // `thrift_flip` retired with #128: nothing in Thrift Flip is gated any
    // more. Historic events carry it; nothing new will.
    case portfolioTrend = "portfolio_trend"
    case valuationDetail = "valuation_detail"
    case trends = "trends"
    case addTag = "add_tag"
    /// The Haul entry on the Scan tab, and Haul's own "not included" banner
    /// (#93). A quota 402 inside a haul is `.scanLimit`, and drafting from
    /// the haul summary is `.snapSell` — each the funnel it already belongs to.
    case haul = "haul"
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - Analytics facade (gated, no-op until configured)
// ═══════════════════════════════════════════════════════════════════

/// Anything that can receive events. Kept SDK-free so the app never imports a
/// vendor SDK outside `TelemetryDeckAnalytics`, and so tests can inject a spy.
protocol AnalyticsService: AnyObject {
    func track(_ event: AnalyticsEvent)

    /// Propagate the user's opt-out into the backend itself.
    ///
    /// Gating `track` is not enough for an SDK that emits its own launch and
    /// session signals — see `TelemetryDeckAnalytics.setEnabled`.
    func setEnabled(_ enabled: Bool)
}

extension AnalyticsService {
    /// Backends with nothing of their own to silence (test spies) need no work.
    func setEnabled(_ enabled: Bool) {}
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

    /// Persisted opt-out. Defaults to on; flipping it off silences everything —
    /// including the backend SDK's own session and install signals, which this
    /// gate used to miss entirely.
    static let enabledKey = "snapworth_analytics_enabled"

    var isEnabled: Bool {
        get { UserDefaults.standard.object(forKey: Self.enabledKey) as? Bool ?? true }
        set {
            UserDefaults.standard.set(newValue, forKey: Self.enabledKey)
            backend?.setEnabled(newValue)
        }
    }

    /// Push the persisted flag into the backend SDK.
    ///
    /// The Settings toggle is an `@AppStorage(Analytics.enabledKey)` binding,
    /// which writes `UserDefaults` **directly** — it never goes through the
    /// setter above, so `backend?.setEnabled` was never called and the SDK was
    /// never told. `track` guards on `isEnabled`, so custom events did stop;
    /// what did not stop is the SDK's own automatic session and install
    /// signals, which carry an identifier and are silenced only by
    /// `setEnabled`. The doc comment above claimed those were covered. Through
    /// the only surface a user can actually reach, they were not: someone who
    /// turned "Share anonymous analytics" off kept sending them.
    ///
    /// `@AppStorage` cannot be routed through a setter, so any writer of the
    /// key has to call this. It is idempotent and cheap.
    func syncBackendToPersistedFlag() {
        backend?.setEnabled(isEnabled)
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
