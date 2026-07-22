import SwiftUI
import SwiftData

extension Notification.Name {
    static let snapSwitchToScan = Notification.Name("snapSwitchToScan")
}

struct MainTabView: View {
    let purchaseService: any PurchaseService
    @State private var selectedTab = 0
    @Query private var results: [ScanResult]

    /// Fallback surface for the ledger reminder: how many listed items are due
    /// for an update (listed ≥14 days ago, still unsold). Works regardless of
    /// notification permission.
    private var ledgerNeedsUpdateCount: Int {
        guard let cutoff = Calendar.current.date(byAdding: .day, value: -14, to: Date()) else { return 0 }
        return results.filter { $0.status == .listed && ($0.listedDate ?? $0.timestamp) <= cutoff }.count
    }

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

            FlipsView(purchaseService: purchaseService)
                .tabItem {
                    Label("My Flips", systemImage: "chart.line.uptrend.xyaxis")
                }
                .badge(ledgerNeedsUpdateCount > 0 ? Text("\(ledgerNeedsUpdateCount)") : nil)
                .tag(2)

            SettingsView(purchaseService: purchaseService)
                .tabItem {
                    Label("Settings", systemImage: selectedTab == 3 ? "gearshape.fill" : "gearshape")
                }
                .tag(3)
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
        // Notification deep links
        .onReceive(NotificationCenter.default.publisher(for: .snapOpenFlips)) { _ in
            selectedTab = 2
        }
        .onReceive(NotificationCenter.default.publisher(for: .snapOpenSettings)) { _ in
            selectedTab = 3
        }
    }
}
