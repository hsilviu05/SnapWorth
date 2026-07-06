import RevenueCat
import Foundation

@MainActor
final class RevenueCatPurchaseService: PurchaseService, ObservableObject {
    @Published private(set) var isSubscribed: Bool

    private static let cacheKey = "snapworth_is_subscribed"

    init() {
        // Restore last known status instantly so subscribed users never see
        // the free-tier UI while the async RevenueCat check is in flight.
        self.isSubscribed = UserDefaults.standard.bool(forKey: Self.cacheKey)
        Purchases.configure(withAPIKey: Config.revenueCatAPIKey)
        refreshSubscriptionStatus()
    }

    func purchase(productID: String) async throws {
        let offerings = try await Purchases.shared.offerings()
        guard
            let offering = offerings.current,
            let package = offering.availablePackages.first(where: { $0.storeProduct.productIdentifier == productID })
        else {
            throw PurchaseError.failed("Product not found in RevenueCat offerings.")
        }

        let result = try await Purchases.shared.purchase(package: package)
        setSubscribed(result.customerInfo.entitlements["premium"]?.isActive == true)
    }

    func restorePurchases() async throws {
        let info = try await Purchases.shared.restorePurchases()
        setSubscribed(info.entitlements["premium"]?.isActive == true)
    }

    private func refreshSubscriptionStatus() {
        Task {
            if let info = try? await Purchases.shared.customerInfo() {
                setSubscribed(info.entitlements["premium"]?.isActive == true)
            }
        }
    }

    private func setSubscribed(_ value: Bool) {
        isSubscribed = value
        UserDefaults.standard.set(value, forKey: Self.cacheKey)
    }
}
