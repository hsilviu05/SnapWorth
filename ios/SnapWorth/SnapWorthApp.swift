import SwiftUI
import SwiftData

@main
struct SnapWorthApp: App {
    // ── Purchase service ──────────────────────────────────────────────────────
    // TODO: Switch to RevenueCatPurchaseService() once Apple Developer account
    // is active and Config.revenueCatAPIKey is set to the appl_ iOS key.
    @StateObject private var purchaseService = MockPurchaseService(forcedSubscribed: false)

    // ── Onboarding state ──────────────────────────────────────────────────────
    @AppStorage("hasCompletedOnboarding") private var hasCompletedOnboarding = false

    // ── SwiftData container ───────────────────────────────────────────────────
    var sharedModelContainer: ModelContainer = {
        let schema = Schema([ScanResult.self])
        let config = ModelConfiguration(schema: schema, isStoredInMemoryOnly: false)
        do {
            return try ModelContainer(for: schema, configurations: [config])
        } catch {
            fatalError("Could not create ModelContainer: \(error)")
        }
    }()

    var body: some Scene {
        WindowGroup {
            RootView(purchaseService: purchaseService)
                .preferredColorScheme(.light)
        }
        .modelContainer(sharedModelContainer)
    }
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
            PaywallView(purchaseService: purchaseService)
        }
    }
}
