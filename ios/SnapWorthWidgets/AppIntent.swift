import AppIntents
import WidgetKit

/// What the Control Centre button runs.
///
/// `openAppWhenRun` launches the app and performs this in the app's process —
/// but a cold launch has no view listening for a navigation notification yet,
/// so the request is left in the App Group for the app to drain when it is
/// ready. See `WidgetBridge.takePendingAction`.
@available(iOS 18.0, *)
struct OpenScanIntent: AppIntent {
    static let title: LocalizedStringResource = "Scan an item"
    static let description = IntentDescription("Opens SnapWorth with the camera ready.")
    static let openAppWhenRun = true

    func perform() async throws -> some IntentResult {
        WidgetBridge.request(.scan)
        return .result()
    }
}
