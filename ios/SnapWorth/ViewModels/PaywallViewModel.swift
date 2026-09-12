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

    /// Point the selection at a plan StoreKit actually returned.
    ///
    /// The default is yearly. A fetch that came back with only the monthly
    /// product therefore left the CTA disabled — `isPurchasable` reads the
    /// *selected* plan — under a "Loading plans…" subheadline, with a
    /// perfectly purchasable monthly card sitting unselected beside it. The
    /// only way out was to guess that the other card worked.
    ///
    /// Does nothing while pricing is empty (still loading, or a total
    /// failure): there is nothing better to move to, and moving the selection
    /// would change what the user sees for no gain.
    func reconcileSelection(with pricing: [String: PlanPricing]) {
        guard !pricing.isEmpty, pricing[selectedProductID] == nil else { return }
        // Same order the cards appear in, so the fallback is the one the user
        // would have reached for.
        let preferred = [Config.yearlyProductID, Config.monthlyProductID]
        guard let fallback = preferred.first(where: { pricing[$0] != nil }) else { return }
        selectedProductID = fallback
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
        // Cleared alongside `errorMessage`, so a stale "waiting for approval"
        // from an Ask-to-Buy attempt is not mistaken for this restore's result.
        pendingMessage = nil
        defer { isRestoring = false }
        do {
            try await service.restorePurchases()
            if service.isSubscribed {
                isPurchaseComplete = true
            } else {
                // The branch that was missing. `AppStore.sync()` succeeding
                // with nothing to restore *is* a success — `restorePurchases`
                // throws only on a real sync error — so the catch below never
                // ran, `errorMessage` stayed nil, and the sheet did not
                // dismiss. The spinner ran for a second, stopped, and nothing
                // else on screen changed: no message, no alert, no state. It
                // was indistinguishable from a button that does nothing, which
                // is what an App Review tester on a fresh sandbox account sees
                // when they tap Restore.
                //
                // `pendingMessage`, which renders in neutral grey, rather than
                // the red `errorMessage`: nothing failed. `SettingsViewModel`
                // has said "No active subscription found." for this case all
                // along; the paywall's copy of the flow dropped it.
                pendingMessage = "No active subscription found on this Apple ID."
            }
        } catch {
            errorMessage = AppError.from(error).errorDescription
        }
    }
}
