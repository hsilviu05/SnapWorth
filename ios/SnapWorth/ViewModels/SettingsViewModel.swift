import SwiftUI

@MainActor
@Observable
final class SettingsViewModel {
    var isRestoring: Bool = false

    // ── One alert, and it says what it is about ──────────────────────────────
    //
    // This was `restoreMessage` + `showRestoreAlert`, and the Clear History
    // failure path reached for the same two — so a storage error that had
    // nothing to do with purchases was presented under the title "Restore
    // purchases". The title travels with the message now.
    var noticeTitle: String = ""
    var noticeMessage: String = ""
    var showNotice: Bool = false

    func report(_ title: String, _ message: String) {
        noticeTitle = title
        noticeMessage = message
        showNotice = true
    }

    func restorePurchases(service: any PurchaseService) async {
        // `isRestoring` was set and cleared here and read nowhere, so the row
        // gave no feedback at all: `AppStore.sync()` can take seconds and can
        // present a system sign-in sheet, and the tap looked ignored until it
        // returned. The View consumes the flag now, and this guard stops the
        // second tap that a silent first one invites.
        guard !isRestoring else { return }
        isRestoring = true
        defer { isRestoring = false }
        do {
            try await service.restorePurchases()
            report(String(localized: "Restore purchases"), service.isSubscribed
                ? String(localized: "Your subscription has been restored.")
                : String(localized: "No active subscription found."))
        } catch {
            // Dismissing the sign-in sheet is not a failure to report. Its
            // `errorDescription` is nil, so alerting anyway would have shown an
            // empty alert once cancellation stopped being an error.
            let appError = AppError.from(error)
            guard appError != .purchaseCancelled else { return }
            report(String(localized: "Restore purchases"), appError.errorDescription ?? "")
        }
    }

    /// Copy for the Clear History confirmation.
    ///
    /// It was `"…all \(count) saved scans."` — the raw count with a hardcoded
    /// plural and no singular case — so a user with one find read "This will
    /// permanently delete all 1 saved scans." The same file pluralises
    /// correctly 150 lines further down, so the standard was already set here.
    static func clearHistoryMessage(count: Int) -> String {
        count == 1
            ? String(localized: "This will permanently delete your saved scan.")
            : String(localized: "This will permanently delete all \(count) saved scans.")
    }

    func openURL(_ urlString: String) {
        guard let url = URL(string: urlString) else { return }
        UIApplication.shared.open(url)
    }
}
