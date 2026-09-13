import SwiftUI
import SwiftData

@main
struct SnapWorthApp: App {
    // ── Purchase service ──────────────────────────────────────────────────────
    @StateObject private var purchaseService = StoreKitPurchaseService()

    // ── Onboarding state ──────────────────────────────────────────────────────
    @AppStorage("hasCompletedOnboarding") private var hasCompletedOnboarding = false

    // ── Lifecycle ─────────────────────────────────────────────────────────────
    // Read here rather than in MainTabView because the Control Centre button's
    // App Intent has to be drained above the tab bar: it may arrive before any
    // tab exists.
    @Environment(\.scenePhase) private var scenePhase

    init() {
        // Wires the analytics backend (no-op until a TelemetryDeck ID is set)
        // and fires app_opened — the top of the launch funnel.
        AnalyticsBootstrap.start()

        // Reported here, not from inside the container closure.
        //
        // Stored-property initialisers run before this body, and
        // `Analytics.track` is a no-op while no backend is configured — so
        // firing the event at the point of failure would be silently dropped,
        // leaving the code looking instrumented while reporting nothing.
        if let reason = AppLaunchState.persistentStoreFallbackReason {
            Analytics.shared.track(.persistentStoreFallback(reason: reason))
        }

        // After bootstrap, for the same reason: MetricKit delivers
        // asynchronously, and a payload arriving while the analytics backend
        // is still unconfigured would be dropped by the no-op `track`.
        CrashReporter.shared.start()
    }

    // ── SwiftData container ───────────────────────────────────────────────────
    var sharedModelContainer: ModelContainer = {
        let schema = Schema([ScanResult.self])
        let config = ModelConfiguration(schema: schema, isStoredInMemoryOnly: false)
        do {
            let container = try ModelContainer(for: schema, configurations: [config])
            // After creation, not before: SwiftData materialises the store file
            // (and its -wal/-shm siblings) as part of opening the container, so
            // there is nothing to set attributes on until this point.
            //
            // `configurations` is a Set, so the URL is read from the config we
            // passed in rather than by indexing into an unordered collection.
            StoreProtection.apply(to: config.url)
            return container
        } catch {
            // Persistent store is corrupt or unreadable; fall back to in-memory
            // so the app stays functional rather than crash-looping on every launch.
            //
            // Behaviour is unchanged — this only records that it happened. The
            // user's history appears empty and this session's work is lost on
            // quit, so a fallback launch must be distinguishable from a healthy
            // one in the data.
            AppLaunchState.recordPersistentStoreFallback(error)
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
            // No `preferredColorScheme` — the app follows the system theme.
            // Every palette token resolves per trait collection (DesignSystem).
            RootView(purchaseService: purchaseService)
                .onOpenURL(perform: handleWidgetURL)
                .task { seedWidgetData() }
                .task { drainPendingWidgetAction() }
                .onChange(of: scenePhase) { _, phase in
                    // Also on resume: a Control Centre press while the app is
                    // already running never triggers `.task`, and the App
                    // Intent that wrote the request cannot reach a view.
                    if phase == .active { drainPendingWidgetAction() }
                }
                .onChange(of: purchaseService.isSubscribed) { _, isPro in
                    // A purchase, a restore, or a lapse moves every Pro-gated
                    // widget. Nothing else writes the blob until the next scan,
                    // so without this a new subscriber's "Profit this month"
                    // widget keeps showing the upsell for as long as they don't
                    // scan — and an expired one keeps showing a paid figure.
                    // Passed explicitly rather than left to the persisted cache
                    // so the write cannot race the store.
                    seedWidgetData(isPro: isPro)
                }
                .task { NotificationManager.shared.registerAsDelegate() }
        }
        .modelContainer(sharedModelContainer)
    }

    // ── Widget URL handling ───────────────────────────────────────────────────
    // snapworth://scan    → navigates to the camera tab
    // snapworth://history → navigates to the history tab
    // snapworth://flips   → navigates to the profit ledger

    /// Act on a Control Centre press.
    ///
    /// The Control Widget runs an App Intent rather than opening a URL, and
    /// that intent races the app's launch — on a cold start nothing is
    /// listening for the navigation notification yet. The intent therefore
    /// leaves its request in the App Group and this drains it once the scene
    /// exists. `takePendingAction` clears as it reads, so a single press opens
    /// the camera once rather than on every subsequent foreground.
    private func drainPendingWidgetAction() {
        switch WidgetBridge.takePendingAction() {
        case .scan:
            NotificationCenter.default.post(name: .snapWidgetOpenScan, object: nil)
        case nil:
            break
        }
    }

    private func handleWidgetURL(_ url: URL) {
        guard url.scheme == "snapworth" else { return }
        switch url.host {
        case "scan":
            NotificationCenter.default.post(name: .snapWidgetOpenScan, object: nil)
        case "history":
            NotificationCenter.default.post(name: .snapWidgetOpenHistory, object: nil)
        case "flips":
            // Reuses the name the notification deep links already post, rather
            // than adding a second route to the same screen.
            NotificationCenter.default.post(name: .snapOpenFlips, object: nil)
        default:
            break
        }
    }

    /// Seed widget data on every launch so the widget is never stale after reinstall.
    ///
    /// `isPro` is nil on the launch path — `writeHaul` reads the persisted
    /// entitlement — and explicit when an entitlement change is what triggered
    /// the write.
    private func seedWidgetData(isPro: Bool? = nil) {
        let ctx = sharedModelContainer.mainContext
        guard let results = try? ctx.fetch(FetchDescriptor<ScanResult>()) else { return }
        WidgetDataStore.writeHaul(results: results, isPro: isPro)
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

    var body: some View {
        Group {
            if !hasCompletedOnboarding {
                OnboardingView {
                    hasCompletedOnboarding = true
                    // Value-first: no paywall here. It surfaces once the user has
                    // seen their first scan result (handled in ScanView).
                }
                .transition(.opacity)
            } else {
                MainTabView(purchaseService: purchaseService)
                    .transition(.opacity)
            }
        }
        .animation(.easeInOut(duration: 0.35), value: hasCompletedOnboarding)
    }
}
