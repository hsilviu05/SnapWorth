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
            restoreMessage = error.localizedDescription
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

    func sendFeedback() {
        let subject = "SnapWorth Feedback"
        let body = "Hi SnapWorth team,"
        let encoded = "mailto:hello@snapworth.com?subject=\(subject)&body=\(body)"
            .addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? ""
        guard let url = URL(string: "mailto:hello@snapworth.com") else { return }
        UIApplication.shared.open(url)
        _ = encoded
    }
}
