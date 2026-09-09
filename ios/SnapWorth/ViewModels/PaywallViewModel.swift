import SwiftUI

@MainActor
@Observable
final class PaywallViewModel {
    var selectedProductID: String = Config.yearlyProductID
    var isPurchasing: Bool = false
    var isRestoring: Bool = false
    var errorMessage: String?
    /// A purchase that is neither done nor failed — see `PurchaseOutcome.pending`.
    var pendingMessage: String?
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
        // Matches the guard in ScanViewModel, ResultViewModel and
        // ThriftFlipViewModel. The View disables the button, but that relies on
        // a render cycle — and this is the payment path, so it should not be
        // the only thing standing between a fast double-tap and two purchases.
        guard !isPurchasing, !isRestoring else { return }
        isPurchasing = true
        errorMessage = nil
        pendingMessage = nil
        defer { isPurchasing = false }
        Analytics.shared.track(.purchaseStarted(productID: selectedProductID))
        do {
            switch try await service.purchase(productID: selectedProductID) {
            case .completed:
                isPurchaseComplete = true
            case .pending:
                // Ask to Buy or an SCA challenge. Dismissing here — which is
                // what setting isPurchaseComplete used to do — left the user
                // with no subscription, no explanation, and the same paywall on
                // their next scan. Stay put and say what is happening.
                pendingMessage = "Waiting for approval. Your subscription starts as soon as it's approved — you don't need to buy again."
            }
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
