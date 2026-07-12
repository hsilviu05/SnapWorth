import SwiftUI

extension Notification.Name {
    static let snapSwitchToScan = Notification.Name("snapSwitchToScan")
}

struct MainTabView: View {
    let purchaseService: any PurchaseService
    @State private var selectedTab = 0

    var body: some View {
        TabView(selection: $selectedTab) {
            ScanView(purchaseService: purchaseService)
                .tabItem {
                    Label("Scan", systemImage: selectedTab == 0 ? "camera.fill" : "camera")
                }
                .tag(0)

            HistoryView()
                .tabItem {
                    Label("My Finds", systemImage: selectedTab == 1 ? "bag.fill" : "bag")
                }
                .tag(1)

            SettingsView(purchaseService: purchaseService)
                .tabItem {
                    Label("Settings", systemImage: selectedTab == 2 ? "gearshape.fill" : "gearshape")
                }
                .tag(2)
        }
        .tint(Color.snapTerracotta)
        .onReceive(NotificationCenter.default.publisher(for: .snapSwitchToScan)) { _ in
            selectedTab = 0
        }
        // Widget deep links
        .onReceive(NotificationCenter.default.publisher(for: .snapWidgetOpenScan)) { _ in
            selectedTab = 0
        }
        .onReceive(NotificationCenter.default.publisher(for: .snapWidgetOpenHistory)) { _ in
            selectedTab = 1
        }
    }
}
