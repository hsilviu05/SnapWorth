import SwiftUI
import SwiftData

@main
struct SnapWorthApp: App {
    // ── Purchase service ──────────────────────────────────────────────────────
    @StateObject private var purchaseService = StoreKitPurchaseService()

    // ── Onboarding state ──────────────────────────────────────────────────────
    @AppStorage("hasCompletedOnboarding") private var hasCompletedOnboarding = false

    init() {
        // Wires the analytics backend (no-op until a TelemetryDeck ID is set)
        // and fires app_opened — the top of the launch funnel.
        AnalyticsBootstrap.start()
    }

    // ── SwiftData container ───────────────────────────────────────────────────
    var sharedModelContainer: ModelContainer = {
        let schema = Schema([ScanResult.self])
        let config = ModelConfiguration(schema: schema, isStoredInMemoryOnly: false)
        do {
            return try ModelContainer(for: schema, configurations: [config])
        } catch {
            // Persistent store is corrupt or unreadable; fall back to in-memory
            // so the app stays functional rather than crash-looping on every launch.
            let fallback = ModelConfiguration(schema: schema, isStoredInMemoryOnly: true)
            do {
                return try ModelContainer(for: schema, configurations: [fallback])
            } catch let fallbackError {
                fatalError("SwiftData failed to create even an in-memory container: \(fallbackError). This is a schema programming error.")
            }
        }
    }()

    var body: some Scene {
        WindowGroup {
            RootView(purchaseService: purchaseService)
                .preferredColorScheme(.light)
                .onOpenURL(perform: handleWidgetURL)
                .task { seedWidgetData() }
        }
        .modelContainer(sharedModelContainer)
    }

    // ── Widget URL handling ───────────────────────────────────────────────────
    // snapworth://scan    → navigates to the camera tab
    // snapworth://history → navigates to the history tab

    private func handleWidgetURL(_ url: URL) {
        guard url.scheme == "snapworth" else { return }
        switch url.host {
        case "scan":
            NotificationCenter.default.post(name: .snapWidgetOpenScan, object: nil)
        case "history":
            NotificationCenter.default.post(name: .snapWidgetOpenHistory, object: nil)
        default:
            break
        }
    }

    /// Seed widget data on every launch so the widget is never stale after reinstall.
    private func seedWidgetData() {
        let ctx = sharedModelContainer.mainContext
        guard let results = try? ctx.fetch(FetchDescriptor<ScanResult>()) else { return }
        WidgetDataStore.writeHaul(results: results)
    }
}

// ── Notification names for widget deep links ──────────────────────────────────

extension Notification.Name {
    static let snapWidgetOpenScan    = Notification.Name("snapWidgetOpenScan")
    static let snapWidgetOpenHistory = Notification.Name("snapWidgetOpenHistory")
}

// ── Root navigator ────────────────────────────────────────────────────────────

struct RootView: View {
    let purchaseService: any PurchaseService
    @AppStorage("hasCompletedOnboarding") private var hasCompletedOnboarding = false
    @State private var showPaywall = false

    var body: some View {
        Group {
            if !hasCompletedOnboarding {
                OnboardingView {
                    hasCompletedOnboarding = true
                    showPaywall = true
                }
                .transition(.opacity)
            } else {
                MainTabView(purchaseService: purchaseService)
                    .transition(.opacity)
            }
        }
        .animation(.easeInOut(duration: 0.35), value: hasCompletedOnboarding)
        .sheet(isPresented: $showPaywall) {
            PaywallView(purchaseService: purchaseService, trigger: .onboarding)
        }
    }
}
