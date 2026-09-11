import SwiftUI

@MainActor
@Observable
final class SettingsViewModel {
    var isRestoring: Bool = false
    var restoreMessage: String?
    var showRestoreAlert: Bool = false

    func restorePurchases(service: any PurchaseService) async {
        isRestoring = true
        defer { isRestoring = false }
        do {
            try await service.restorePurchases()
            restoreMessage = service.isSubscribed
                ? "Your subscription has been restored."
                : "No active subscription found."
        } catch {
            restoreMessage = AppError.from(error).errorDescription
        }
        showRestoreAlert = true
    }

    func openSettings() {
        guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
        UIApplication.shared.open(url)
    }

    func openURL(_ urlString: String) {
        guard let url = URL(string: urlString) else { return }
        UIApplication.shared.open(url)
    }
}
