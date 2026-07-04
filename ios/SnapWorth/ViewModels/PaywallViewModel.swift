import SwiftUI

@MainActor
@Observable
final class PaywallViewModel {
    var selectedProductID: String = Config.yearlyProductID
    var isPurchasing: Bool = false
    var isRestoring: Bool = false
    var errorMessage: String?
    var showCloseButton: Bool = false
    var isPurchaseComplete: Bool = false

    // Delay before showing the X close button
    func startCloseButtonTimer() {
        Task {
            try? await Task.sleep(for: .seconds(2))
            withAnimation { showCloseButton = true }
        }
    }

    func purchase(service: any PurchaseService) async {
        isPurchasing = true
        errorMessage = nil
        defer { isPurchasing = false }
        do {
            try await service.purchase(productID: selectedProductID)
            isPurchaseComplete = true
        } catch PurchaseError.cancelled {
            // no-op — user dismissed sheet
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func restore(service: any PurchaseService) async {
        isRestoring = true
        errorMessage = nil
        defer { isRestoring = false }
        do {
            try await service.restorePurchases()
            if service.isSubscribed { isPurchaseComplete = true }
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
