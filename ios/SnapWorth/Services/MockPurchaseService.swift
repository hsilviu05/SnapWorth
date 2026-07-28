import Foundation

/// Mock purchase service for previews and tests — no StoreKit involved.
/// Set `forcedSubscribed` to test both the free and subscribed UX.
@MainActor
final class MockPurchaseService: PurchaseService, ObservableObject {
    @Published private(set) var isSubscribed: Bool
    @Published private(set) var pricing: [String: PlanPricing]
    @Published private(set) var isPricingLoaded: Bool

    /// Flip to `false` to test the free-tier paywall flow.
    /// - Parameter pricingLoaded: set `false` to exercise the paywall's
    ///   loading/redacted state, which real users see on a cold cellular start.
    init(forcedSubscribed: Bool = false, pricingLoaded: Bool = true) {
        self.isSubscribed = forcedSubscribed
        self.isPricingLoaded = pricingLoaded
        self.pricing = pricingLoaded ? Self.samplePricing : [:]
    }

    /// Mirrors the *shape* StoreKit returns, not a hardcoded product truth —
    /// these values never reach a real storefront.
    static let samplePricing: [String: PlanPricing] = [
        Config.yearlyProductID: PlanPricing(
            productID: Config.yearlyProductID,
            displayPrice: "$39.99",
            displayPricePerWeek: "$0.77",
            introductoryOffer: "3-day free trial",
            savingsPercent: 33
        ),
        Config.monthlyProductID: PlanPricing(
            productID: Config.monthlyProductID,
            displayPrice: "$4.99",
            displayPricePerWeek: nil,
            introductoryOffer: nil,
            savingsPercent: nil
        ),
    ]

    func reloadProducts() async {
        pricing = Self.samplePricing
        isPricingLoaded = true
    }

    func purchase(productID: String) async throws {
        // Simulate network latency
        try await Task.sleep(for: .seconds(1.5))
        isSubscribed = true
    }

    func restorePurchases() async throws {
        try await Task.sleep(for: .seconds(1))
        // No-op in mock — user remains in current state
    }
}
