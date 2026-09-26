import StoreKit
import UIKit

/// Asks for an App Store rating after a few *successful* scans — a positive
/// moment when the user has just seen the app's value. More ratings lift both
/// search ranking and conversion, so this is a deliberate growth lever.
///
/// We only *request*; iOS throttles the actual prompt (≈3×/year) and decides
/// whether to show it. On top of that we self-limit to one request per
/// `minimumGap` so we never nag. Fully no-op safe if there's no active scene.
///
/// The request is made from the result sheet once the price is on screen, not
/// from the scan. It used to fire 1.2 seconds after the result opened — which,
/// since the guess-first cover became the default, is while the price is still
/// covered: the user was asked to rate the app before seeing the number they
/// scanned for. And "once per version" re-armed it on every update, four times
/// in eleven days in September, so the three prompts iOS allows a year went on
/// the one moment with nothing to show for it.
@MainActor
enum ReviewPrompt {
    private static let scanCountKey = "snapworth_successful_scans"
    /// Written by builds that limited to once per version. Read only to seed
    /// `lastRequestKey` on upgrade; see `requestIfDue`.
    private static let promptedVersionKey = "snapworth_review_prompted_version"
    private static let lastRequestKey = "snapworth_review_last_requested"

    /// Ask from the Nth successful scan — late enough to have proven value,
    /// early enough that most engaged users still hit it.
    nonisolated private static var promptAtScan: Int { 3 }

    /// The shortest time between two requests. iOS shows at most three prompts
    /// a year and drops the rest silently, so asking more often than this only
    /// spends the budget on moments that were not chosen.
    nonisolated static var minimumGap: TimeInterval { 60 * 86_400 }

    /// Call after every successful scan. Only counts it: the request waits for
    /// the price to be on screen.
    static func recordSuccessfulScan(defaults: UserDefaults = .standard) {
        defaults.set(defaults.integer(forKey: scanCountKey) + 1, forKey: scanCountKey)
    }

    /// Only an estimate the app stands behind is a moment of value. A Low
    /// read is the app saying it is unsure — no time to ask for five stars.
    nonisolated static func isWorthAskingAbout(confidence: String) -> Bool {
        switch confidence.lowercased() {
        case "high", "medium": return true
        default:               return false
        }
    }

    /// The decision, apart from UserDefaults and the scene. Pure, for the tests.
    ///
    /// A clock that moved backwards makes the gap meaningless rather than
    /// small, so it counts as not yet due.
    nonisolated static func isDue(scanCount: Int, lastRequest: Date?, now: Date) -> Bool {
        guard scanCount >= promptAtScan else { return false }
        guard let lastRequest else { return true }
        return now.timeIntervalSince(lastRequest) >= minimumGap
    }

    /// Request a review if one is due. Call at a moment of value, with the
    /// payoff already on screen — today that is a fresh estimate, revealed,
    /// that the app is confident in (`ResultView`).
    ///
    /// "Mark as sold" was considered and left out: it opens the sold-price
    /// and fees fields, and a system prompt there interrupts the user in the
    /// middle of typing the one number the ledger needs.
    static func requestIfDue(defaults: UserDefaults = .standard, now: Date = Date()) {
        var lastRequest = defaults.object(forKey: lastRequestKey) as? Date
        if lastRequest == nil, defaults.string(forKey: promptedVersionKey) != nil {
            // An earlier build asked in some version, at some unknown time.
            // Start the gap now rather than treating it as long ago: the
            // likeliest case is an ask during the last few days of updates.
            defaults.set(now, forKey: lastRequestKey)
            lastRequest = now
        }
        guard isDue(scanCount: defaults.integer(forKey: scanCountKey),
                    lastRequest: lastRequest, now: now) else { return }

        guard let scene = UIApplication.shared.connectedScenes
            .first(where: { $0.activationState == .foregroundActive }) as? UIWindowScene
        else { return }

        AppStore.requestReview(in: scene)
        defaults.set(now, forKey: lastRequestKey)
        Analytics.shared.track(.reviewPromptRequested)
    }
}
