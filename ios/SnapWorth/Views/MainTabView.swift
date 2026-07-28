import SwiftUI
import SwiftData

extension Notification.Name {
    static let snapSwitchToScan = Notification.Name("snapSwitchToScan")
}

struct MainTabView: View {
    let purchaseService: any PurchaseService
    @State private var selectedTab = 0

    /// Only *listed* items, filtered in the fetch rather than in Swift.
    ///
    /// This previously fetched every `ScanResult` the user had ever created —
    /// on the root view that hosts all four tabs — purely to compute a badge.
    /// Two costs: every scan mutation invalidated the whole `TabView` body, and
    /// a power user with thousands of records faulted them all in on each pass.
    ///
    /// The predicate must reference `statusRaw` (the stored property), not
    /// `status` (a computed accessor) — SwiftData predicates cannot call
    /// computed properties and would silently fail to compile the fetch.
    @Query(filter: #Predicate<ScanResult> { $0.statusRaw == "listed" })
    private var listedItems: [ScanResult]

    /// Fallback surface for the ledger reminder: how many listed items are due
    /// for an update (listed ≥14 days ago, still unsold). Works regardless of
    /// notification permission.
    private var ledgerNeedsUpdateCount: Int {
        guard let cutoff = Calendar.current.date(byAdding: .day, value: -14, to: Date()) else { return 0 }
        return listedItems.reduce(into: 0) { count, item in
            if (item.listedDate ?? item.timestamp) <= cutoff { count += 1 }
        }
    }

    var body: some View {
        TabView(selection: $selectedTab) {
            ScanView(purchaseService: purchaseService)
                .tabItem {
                    Label("Scan", systemImage: selectedTab == 0 ? "camera.fill" : "camera")
                }
                .tag(0)

            HistoryView(purchaseService: purchaseService)
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
