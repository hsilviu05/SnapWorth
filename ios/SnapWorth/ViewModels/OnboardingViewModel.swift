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
            body: "SnapWorth writes the title and description for eBay, Vinted, Facebook and more. You paste and post.",
            accent: .snapTerracotta
        ),
        OnboardingSlide(
            hero: .thriftFlip,
            headline: "Buy smart.\nFlip for profit.",
            body: "See your exact profit after marketplace fees, right there in the aisle.",
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
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
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
    let headline: String
    let body: String
    let accent: Color
}
