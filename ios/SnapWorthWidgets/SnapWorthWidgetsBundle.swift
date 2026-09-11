import WidgetKit
import SwiftUI

@main
struct SnapWorthWidgetBundle: WidgetBundle {
    var body: some Widget {
        HaulWidget()
        QuickScanWidget()
        LockScreenHaulWidget()
        // Controls arrived in iOS 18; the deployment target is 17.
        if #available(iOS 18.0, *) {
            ScanControlWidget()
        }
    }
}
