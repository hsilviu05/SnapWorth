import SwiftUI

@Observable
final class OnboardingViewModel {
    var currentPage: Int = 0

    let slides: [OnboardingSlide] = [
        OnboardingSlide(
            headline: "That $4 jacket?\nMight be $90.",
            body: "SnapWorth reveals the hidden resale value in thrift store finds before you buy.",
            symbolName: "tag.fill",
            rotation: -4.0
        ),
        OnboardingSlide(
            headline: "Snap any item.\nWe do the rest.",
            body: "Our AI identifies it and prices it from thousands of real sold listings on Poshmark, eBay & more.",
            symbolName: "camera.viewfinder",
            rotation: 3.5
        ),
        OnboardingSlide(
            headline: "Join 12,000+\nthrifters flipping smarter.",
            body: "Save every find to your closet and track exactly how much value you've uncovered.",
            symbolName: "person.3.fill",
            rotation: -2.5
        ),
    ]

    var isLastPage: Bool { currentPage == slides.count - 1 }

    func advance() {
        if currentPage < slides.count - 1 {
            currentPage += 1
        }
    }
}

struct OnboardingSlide: Identifiable {
    let id = UUID()
    let headline: String
    let body: String
    let symbolName: String
    let rotation: Double
}
