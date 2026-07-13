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

    private var closeButtonTask: Task<Void, Never>?

    func startCloseButtonTimer() {
        closeButtonTask = Task {
            try? await Task.sleep(for: .seconds(2))
            guard !Task.isCancelled else { return }
            withAnimation { showCloseButton = true }
        }
    }

    func cancelTimer() {
        closeButtonTask?.cancel()
        closeButtonTask = nil
    }

    func purchase(service: any PurchaseService) async {
        isPurchasing = true
        errorMessage = nil
        defer { isPurchasing = false }
        do {
            try await service.purchase(productID: selectedProductID)
            isPurchaseComplete = true
        } catch {
            let appError = AppError.from(error)
            if appError != .purchaseCancelled {
                errorMessage = appError.errorDescription
            }
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
            errorMessage = AppError.from(error).errorDescription
        }
    }
}
