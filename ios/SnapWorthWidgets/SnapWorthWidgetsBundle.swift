import WidgetKit
import SwiftUI

@main
struct SnapWorthWidgetBundle: WidgetBundle {
    var body: some Widget {
        HaulWidget()
        QuickScanWidget()
    }
}
