import AppIntents
import WidgetKit

/// What the Control Centre button runs.
///
/// `openAppWhenRun` launches the app and performs this in the app's process —
/// but a cold launch has no view listening for a navigation notification yet,
/// so the request is left in the App Group for the app to drain when it is
/// ready. See `WidgetBridge.takePendingAction`.
///
/// The App Group handoff alone was not enough. The app drains it from two
/// places: a `.task` that runs once at scene creation, and the
/// `scenePhase == .active` edge. `onChange` fires only on a *transition*, and
/// `openAppWhenRun` is a no-op when the app is already frontmost — no
/// relaunch, no phase change, no `.task` re-run (RootView's identity is
/// stable). So pressing the Action Button, which iOS 18 lets a user assign
/// straight to this control and which is the primary use case this widget was
/// written for, did nothing whatsoever while SnapWorth was on screen.
///
/// So it also opens `snapworth://scan`. `onOpenURL` *is* delivered to an
/// already-frontmost app, and it posts the same notification the drain does,
/// so the two paths converge on one observer. The App Group write stays: it is
/// what covers the cold launch this class was designed around, and the drain
/// is idempotent — `takePendingAction` clears as it reads, so whichever
/// channel arrives first consumes the request and the other finds nothing.
@available(iOS 18.0, *)
struct OpenScanIntent: AppIntent {
    static let title: LocalizedStringResource = "Scan an item"
    static let description = IntentDescription("Opens SnapWorth with the camera ready.")
    static let openAppWhenRun = true

    func perform() async throws -> some IntentResult & OpensIntent {
        WidgetBridge.request(.scan)
        return .result(opensIntent: OpenURLIntent(URL(string: "snapworth://scan")!))
    }
}
