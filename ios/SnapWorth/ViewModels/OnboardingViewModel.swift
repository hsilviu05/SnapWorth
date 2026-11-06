import SwiftUI
import UIKit

@MainActor
@Observable
final class OnboardingViewModel {
    var currentPage: Int = 0

    let slides: [OnboardingSlide] = [
        OnboardingSlide(
            hero: .valueEstimate,
            headline: "That $4 jacket?\nMight be $90.",
            body: "Point your camera at any thrift find and see its resale value in seconds — before you buy.",
            accent: .snapSage
        ),
        OnboardingSlide(
            hero: .snapSell,
            headline: "One snap.\nA ready listing.",
            body: "SnapWorth writes the title and description for eBay, Poshmark, Mercari and more. You paste and post.",
            accent: .snapTerracotta
        ),
        OnboardingSlide(
            hero: .thriftFlip,
            headline: "Buy smart.\nFlip for profit.",
            // "Estimated", not "exact": the resale side is an AI estimate
            // and the fee table is approximate — Thrift Flip says both, one
            // screen after this promise.
            body: "See your estimated profit after marketplace fees, right there in the aisle.",
            accent: .snapAmber
        ),
        OnboardingSlide(
            hero: .trackFinds,
            headline: "Every find,\nin one place.",
            body: "Your scans are saved to your closet so you can watch the hidden value add up over time.",
            accent: .snapSage
        ),
    ]

    var isLastPage: Bool { currentPage == slides.count - 1 }

    func advance() {
        guard currentPage < slides.count - 1 else { return }
        Haptics.light()
        currentPage += 1
    }
}

/// Which product moment a slide previews — selects the hero illustration.
enum OnboardingHero {
    case valueEstimate
    case snapSell
    case thriftFlip
    case trackFinds
}

struct OnboardingSlide: Identifiable {
    let id = UUID()
    let hero: OnboardingHero
    /// Keys, not strings: the four slides are the only copy a first-time user
    /// reads before deciding whether to keep the app, and `Text(String)` is
    /// the initialiser that does not translate.
    let headline: LocalizedStringKey
    let body: LocalizedStringKey
    let accent: Color
}

/// What the user said brings them to the app, asked once on the last
/// onboarding page and optional. It only reorders what a paywall that nobody
/// reached for leads with (`PaywallCopy.benefits(pitch:goal:)`); nothing is
/// gated or hidden on it, and it never leaves the phone except as the
/// `onboarding_goal_chosen` event.
enum UserGoal: String, CaseIterable, Identifiable {
    case resell
    case declutter
    case curious

    var id: String { rawValue }

    static let key = "snapworth_user_goal"

    static func current(defaults: UserDefaults = .standard) -> UserGoal? {
        defaults.string(forKey: key).flatMap(UserGoal.init(rawValue:))
    }

    func save(defaults: UserDefaults = .standard) {
        defaults.set(rawValue, forKey: Self.key)
    }

    var label: LocalizedStringKey {
        switch self {
        case .resell:    return "I resell"
        case .declutter: return "I'm clearing out"
        case .curious:   return "Just curious"
        }
    }

    /// The paywall rows this goal leads with, by icon (`PaywallCopy.Benefit`).
    var leadIcons: [String] {
        switch self {
        case .resell:
            return ["square.stack.3d.up.fill", "rectangle.split.3x1.fill", "cart.fill"]
        case .declutter:
            return ["chart.line.uptrend.xyaxis", "bubble.left.and.bubble.right.fill", "cart.fill"]
        case .curious:
            return []
        }
    }
}
