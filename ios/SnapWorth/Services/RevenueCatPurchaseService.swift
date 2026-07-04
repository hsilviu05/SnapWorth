// RevenueCatPurchaseService.swift
// This file compiles only when the RevenueCat Swift package is present.
// To enable:
//   1. Add RevenueCat via SPM: https://github.com/RevenueCat/purchases-ios
//   2. In Config.swift set revenueCatAPIKey to your iOS public key
//   3. In SnapWorthApp.swift replace MockPurchaseService with RevenueCatPurchaseService
//   4. Delete or archive MockPurchaseService once tested

#if canImport(RevenueCat)
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
#endif
