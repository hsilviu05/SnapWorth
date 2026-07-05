import RevenueCat
import Foundation

@MainActor
final class RevenueCatPurchaseService: PurchaseService, ObservableObject {
    @Published private(set) var isSubscribed: Bool = false

    init() {
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
        isSubscribed = result.customerInfo.entitlements["premium"]?.isActive == true
    }

    func restorePurchases() async throws {
        let info = try await Purchases.shared.restorePurchases()
        isSubscribed = info.entitlements["premium"]?.isActive == true
    }

    private func refreshSubscriptionStatus() {
        Task {
            if let info = try? await Purchases.shared.customerInfo() {
                isSubscribed = info.entitlements["premium"]?.isActive == true
            }
        }
    }
}
