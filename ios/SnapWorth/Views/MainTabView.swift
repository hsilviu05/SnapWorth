import SwiftUI
import SwiftData

extension Notification.Name {
    static let snapSwitchToScan = Notification.Name("snapSwitchToScan")
}

struct MainTabView: View {
    let purchaseService: any PurchaseService
    /// Threaded from `SnapWorthApp` so an entitlement change actually makes
    /// this view value differ — see the comment at that call site.
    ///
    /// Load-bearing for more than the one view it is handed to: the other three
    /// tabs read `purchaseService.isSubscribed` directly, which registers no
    /// dependency, and they re-render only because a change to this property
    /// re-runs *this* body and rebuilds them. Removing it because "only
    /// Settings uses it" would put the stale-Pro-chrome bug back on all four.
    let isPro: Bool
    @State private var selectedTab = 0
    /// A week earned from a referral (#97) that this device has not been told
    /// about yet. Shown once per code; the code stays under Invite a friend.
    @State private var earnedReward: ReferralStatus.Reward?
    @Environment(\.openURL) private var openURL
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.modelContext) private var modelContext

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

            SettingsView(purchaseService: purchaseService, isPro: isPro)
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
        // Every schedule in NotificationManager is idempotent, so rebuilding
        // them on each foreground is the simplest way to keep the daily
        // free-scan reminder honest: it moves to tomorrow once today's scan
        // happens, and disappears the moment the user goes Pro. Previously
        // schedules were only rebuilt when notifications were first granted.
        .onChange(of: scenePhase) { _, phase in
            guard phase == .active else { return }
            Task {
                // An expiry produces no `Transaction.updates` event, so a
                // long-resident session kept painting Pro chrome after the
                // subscription lapsed. Re-read entitlements first: the
                // notification sync below branches on `isSubscribed`.
                await purchaseService.refreshEntitlements()
                await NotificationManager.shared.syncEligible(
                    context: modelContext, purchaseService: purchaseService)
                await checkForEarnedReward()
            }
        }
        .alert("You earned a week of Pro",
               isPresented: Binding(get: { earnedReward != nil }, set: { if !$0 { earnedReward = nil } }),
               presenting: earnedReward) { reward in
            Button("Redeem") {
                Analytics.shared.track(.referralRewarded)
                openURL(reward.redeemURL)
            }
            Button("Later", role: .cancel) {}
        } message: { _ in
            Text("A friend used your invite. Redeem your free week with Apple.")
        }
    }

    /// Announces the newest unannounced referral reward, once. Only the newest,
    /// so three friends at once is one alert, not three; the rest are listed
    /// under Invite a friend.
    private func checkForEarnedReward() async {
        let status = await ReferralAPIClient.shared.status()
        guard status.enabled else { return }
        let fresh = ReferralRewardNotice.unannounced(status.rewards)
        guard let newest = fresh.max(by: { $0.earnedAt < $1.earnedAt }) else { return }
        ReferralRewardNotice.markAnnounced(fresh)
        earnedReward = newest
    }
}
