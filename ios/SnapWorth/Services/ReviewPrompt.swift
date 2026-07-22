import StoreKit
import UIKit

/// Asks for an App Store rating after a few *successful* scans — a positive
/// moment when the user has just seen the app's value. More ratings lift both
/// search ranking and conversion, so this is a deliberate growth lever.
///
/// We only *request*; iOS throttles the actual prompt (≈3×/year) and decides
/// whether to show it. On top of that we self-limit to once per app version so
/// we never nag. Fully no-op safe if there's no active scene.
@MainActor
enum ReviewPrompt {
    private static let scanCountKey = "snapworth_successful_scans"
    private static let promptedVersionKey = "snapworth_review_prompted_version"

    /// Ask on the Nth successful scan — late enough to have proven value,
    /// early enough that most engaged users still hit it.
    private static let promptAtScan = 3

    /// Call after every successful scan. Increments the lifetime counter and,
    /// once the threshold is crossed, requests a review at most once per version.
    static func recordSuccessfulScan() {
        let defaults = UserDefaults.standard
        let count = defaults.integer(forKey: scanCountKey) + 1
        defaults.set(count, forKey: scanCountKey)

        guard count >= promptAtScan else { return }

        // Only ever ask once per app version.
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
        guard defaults.string(forKey: promptedVersionKey) != version else { return }

        guard let scene = UIApplication.shared.connectedScenes
            .first(where: { $0.activationState == .foregroundActive }) as? UIWindowScene
        else { return }

        AppStore.requestReview(in: scene)
        defaults.set(version, forKey: promptedVersionKey)
    }
}
