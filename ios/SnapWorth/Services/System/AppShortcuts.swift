import AppIntents
import UIKit

/// "What's this worth?" from Siri, Spotlight and the Shortcuts app, with no
/// setup by the user (#412).
///
/// It reaches the camera the way the Control Centre button does
/// (`OpenScanIntent` in the widget extension): a request left in the App
/// Group, drained by the scene's `.task` on a cold launch and on the edge to
/// `.active` on a resume. Neither of those runs when SnapWorth is already
/// frontmost, so in that case the request is taken back here and the route
/// posted directly. `OpenURLIntent` would do that part, but it is iOS 18 only
/// and the app supports 17; an App Shortcut gated by availability is what
/// Apple's forums report crashing on older systems.
struct ValueItemIntent: AppIntent {
    static let title: LocalizedStringResource = "Value an item"
    static let description = IntentDescription("Opens SnapWorth with the camera ready.")
    static let openAppWhenRun = true

    @MainActor
    func perform() async throws -> some IntentResult {
        WidgetBridge.request(.scan)
        Analytics.shared.track(.widgetOpened(source: WidgetSource.siri.rawValue))
        // Before onboarding nothing is listening, and the drain holds the
        // request until it is finished. Taking it here would destroy it.
        let onboarded = UserDefaults.standard.bool(forKey: "hasCompletedOnboarding")
        if onboarded, UIApplication.shared.applicationState == .active,
           WidgetBridge.takePendingAction() != nil {
            SnapWorthApp.open(.snapWidgetOpenScan)
        }
        return .result()
    }
}

struct SnapWorthShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        // Every phrase must name the app. These are English only until an
        // `AppShortcuts.xcstrings` exists; the title above is translated.
        AppShortcut(
            intent: ValueItemIntent(),
            phrases: [
                "What's this worth in \(.applicationName)",
                "Value an item with \(.applicationName)",
                "Scan an item with \(.applicationName)",
                "Price this with \(.applicationName)",
            ],
            shortTitle: "Value an item",
            systemImageName: "camera.viewfinder"
        )
    }
}
