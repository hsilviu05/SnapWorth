import Foundation

/// Mock purchase service for previews and tests — no StoreKit involved.
/// Set `forcedSubscribed` to test both the free and subscribed UX.
@MainActor
final class MockPurchaseService: PurchaseService, ObservableObject {
    @Published private(set) var isSubscribed: Bool

    /// Flip to `false` to test the free-tier paywall flow.
    init(forcedSubscribed: Bool = false) {
        self.isSubscribed = forcedSubscribed
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
