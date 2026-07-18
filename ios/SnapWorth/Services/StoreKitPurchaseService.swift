import Foundation
import StoreKit

/// Native StoreKit 2 purchase service. Fetches products directly from the App
/// Store by product identifier — no third-party dashboard or offerings config.
@MainActor
final class StoreKitPurchaseService: PurchaseService, ObservableObject {
    @Published private(set) var isSubscribed: Bool

    private static let cacheKey = "snapworth_is_subscribed"
    private let productIDs = [Config.monthlyProductID, Config.yearlyProductID]

    private var products: [Product] = []
    private var updatesTask: Task<Void, Never>?

    init() {
        // Restore last known status instantly so subscribed users never see
        // the free-tier UI while the async entitlement check is in flight.
        self.isSubscribed = UserDefaults.standard.bool(forKey: Self.cacheKey)

        // Listen for transactions that happen outside an explicit purchase call
        // (renewals, Ask-to-Buy approvals, purchases made on another device).
        updatesTask = listenForTransactions()

        Task {
            await loadProducts()
            await refreshSubscriptionStatus()
        }
    }

    deinit {
        updatesTask?.cancel()
    }

    // MARK: - PurchaseService

    func purchase(productID: String) async throws {
        let product = try await product(for: productID)

        let result: Product.PurchaseResult
        do {
            result = try await product.purchase()
        } catch {
            throw PurchaseError.failed(error.localizedDescription)
        }

        switch result {
        case .success(let verification):
            let transaction = try checkVerified(verification)
            await transaction.finish()
            await refreshSubscriptionStatus()
        case .userCancelled:
            throw PurchaseError.cancelled
        case .pending:
            // Deferred (e.g. Ask to Buy / SCA). Not a failure — leave state as-is.
            // The transaction listener finalizes it once approved.
            break
        @unknown default:
            throw PurchaseError.failed("This purchase could not be completed.")
        }
    }

    func restorePurchases() async throws {
        do {
            try await AppStore.sync()
        } catch {
            throw PurchaseError.failed(error.localizedDescription)
        }
        await refreshSubscriptionStatus()
    }

    // MARK: - Private

    /// Returns the cached `Product`, fetching on demand if the initial load
    /// hasn't finished (or previously failed) so a tap is never a dead end.
    private func product(for productID: String) async throws -> Product {
        if let cached = products.first(where: { $0.id == productID }) {
            return cached
        }
        await loadProducts()
        guard let product = products.first(where: { $0.id == productID }) else {
            throw PurchaseError.failed("This subscription is currently unavailable. Please try again.")
        }
        return product
    }

    private func loadProducts() async {
        if let fetched = try? await Product.products(for: productIDs), !fetched.isEmpty {
            products = fetched
        }
    }

    private func refreshSubscriptionStatus() async {
        var active = false
        for await result in Transaction.currentEntitlements {
            guard let transaction = try? checkVerified(result) else { continue }
            if productIDs.contains(transaction.productID), transaction.revocationDate == nil {
                active = true
            }
        }
        setSubscribed(active)
    }

    private func listenForTransactions() -> Task<Void, Never> {
        Task { [weak self] in
            for await result in Transaction.updates {
                guard let self else { continue }
                guard let transaction = try? self.checkVerified(result) else { continue }
                await transaction.finish()
                await self.refreshSubscriptionStatus()
            }
        }
    }

    private nonisolated func checkVerified<T>(_ result: VerificationResult<T>) throws -> T {
        switch result {
        case .unverified:
            throw PurchaseError.failed("Your purchase could not be verified.")
        case .verified(let safe):
            return safe
        }
    }

    private func setSubscribed(_ value: Bool) {
        isSubscribed = value
        UserDefaults.standard.set(value, forKey: Self.cacheKey)
    }
}
