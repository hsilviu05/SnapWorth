import AppIntents
import SwiftUI
import WidgetKit

// ── Control Centre / Action Button ───────────────────────────────────────────
//
// The one place this app's primary action belongs. Scanning happens standing in
// a shop with something in the other hand — a press of the Action Button beats
// unlocking, finding the app, and waiting for a tab to settle.
//
// iOS 18 only, and the deployment target is 17, so the whole type is gated and
// the bundle adds it behind `if #available`. A `ControlWidget` that shipped
// unguarded would fail to build against the older SDK, not degrade.

@available(iOS 18.0, *)
struct ScanControlWidget: ControlWidget {
    var body: some ControlWidgetConfiguration {
        StaticControlConfiguration(kind: "eu.snapworth.control.scan") {
            ControlWidgetButton(action: OpenScanIntent()) {
                Label("Scan", systemImage: "camera.viewfinder")
            }
        }
        .displayName("Scan an item")
        .description("Open SnapWorth with the camera ready.")
    }
}
