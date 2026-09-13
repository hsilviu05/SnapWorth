import SwiftUI
import SwiftData
import StoreKit

struct SettingsView: View {
    let purchaseService: any PurchaseService
    @Environment(\.modelContext) private var modelContext
    @Environment(\.requestReview) private var requestReview
    @Query private var results: [ScanResult]
    @State private var vm = SettingsViewModel()
    @State private var showPaywall = false
    @State private var showDeleteAlert = false
    @AppStorage(Analytics.enabledKey) private var analyticsEnabled = true
    @AppStorage(Haptics.preferenceKey) private var hapticsEnabled = true
    @AppStorage(GuessFirst.key) private var guessFirst = GuessFirst.defaultOn

    /// Marketing version read from the bundle so it never goes stale.
    private static var appVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "—"
    }

    var body: some View {
        NavigationStack {
            List {
                // ── Subscription card ──────────────────────────────────────
                Section {
                    SubscriptionCard(
                        isSubscribed: purchaseService.isSubscribed,
                        onUpgrade: { showPaywall = true }
                    )
                }
                .listRowInsets(EdgeInsets())
                .listRowBackground(Color.clear)

                // ── Account ────────────────────────────────────────────────
                Section("Account") {
                    if purchaseService.isSubscribed {
                        SettingsRow(icon: "creditcard", label: "Manage subscription") {
                            vm.openURL("https://apps.apple.com/account/subscriptions")
                        }
                    }
                    SettingsRow(icon: "arrow.clockwise", label: "Restore purchases",
                                isBusy: vm.isRestoring) {
                        Task { await vm.restorePurchases(service: purchaseService) }
                    }
                }

                // ── Notifications ──────────────────────────────────────────
                Section("Notifications") {
                    NavigationLink {
                        NotificationSettingsView(isPro: purchaseService.isSubscribed)
                    } label: {
                        SettingsRowLabel(icon: "bell", label: "Notifications")
                    }
                }

                // ── Legal ──────────────────────────────────────────────────
                Section("Legal") {
                    NavigationLink {
                        PrivacyPolicyView()
                    } label: {
                        SettingsRowLabel(icon: "lock.shield", label: "Privacy Policy")
                    }
                    NavigationLink {
                        TermsOfServiceView()
                    } label: {
                        SettingsRowLabel(icon: "doc.text", label: "Terms of Service")
                    }
                }

                // ── Support ────────────────────────────────────────────────
                Section("Support") {
                    NavigationLink {
                        FeedbackView(initialType: .featureRequest)
                    } label: {
                        SettingsRowLabel(icon: "lightbulb", label: "Suggest a feature")
                    }
                    NavigationLink {
                        FeedbackView(initialType: .bugReport)
                    } label: {
                        SettingsRowLabel(icon: "ant", label: "Report a bug")
                    }
                    SettingsRow(icon: "star", label: "Rate SnapWorth") {
                        requestReview()
                    }
                }

                // ── Data ───────────────────────────────────────────────────
                //
                // Offered only when there is something to clear. On a fresh
                // install the destructive row was still there, reading "This
                // will permanently delete all 0 saved scans." — and confirming
                // it ran a delete of nothing that still cancelled every ledger
                // notification and rewrote the widget and Live Activity blobs.
                if !results.isEmpty {
                    Section("Data") {
                        SettingsRow(icon: "trash", label: "Clear scan history", destructive: true) {
                            showDeleteAlert = true
                        }
                    }
                }

                // ── Feel ───────────────────────────────────────────────────
                Section {
                    Toggle(isOn: $hapticsEnabled) {
                        HStack(spacing: 14) {
                            Image(systemName: "hand.tap")
                                .snapSymbol(16, weight: .medium)
                                .foregroundStyle(Color.snapTerracottaText)
                                .frame(minWidth: 24)
                                .accessibilityHidden(true)
                            Text("Haptic feedback")
                                .font(.snapBody)
                                .foregroundStyle(Color.snapEspresso)
                        }
                        .frame(minHeight: 44)
                    }
                    .tint(Color.snapTerracotta)
                    .accessibilityLabel("Haptic feedback")
                    .accessibilityHint("Turns off the taps you feel when scanning and selecting")
                    .onChange(of: hapticsEnabled) { _, enabled in
                        // Fire once on enable so the change is felt, not just read.
                        if enabled { Haptics.selection() }
                    }

                    Toggle(isOn: $guessFirst) {
                        HStack(spacing: 14) {
                            Image(systemName: "questionmark.circle")
                                .snapSymbol(16, weight: .medium)
                                .foregroundStyle(Color.snapTerracottaText)
                                .frame(minWidth: 24)
                                .accessibilityHidden(true)
                            Text("Guess before the estimate")
                                .font(.snapBody)
                                .foregroundStyle(Color.snapEspresso)
                        }
                        .frame(minHeight: 44)
                    }
                    .tint(Color.snapTerracotta)
                    .accessibilityLabel("Guess before the estimate")
                    .accessibilityHint("When on, each result opens with the price covered until you tap Reveal")
                } header: {
                    Text("Feel")
                } footer: {
                    Text("Guess before the estimate covers the price on every new result until you tap Reveal. Turn it off to see the number straight away.")
                }

                // ── Privacy ────────────────────────────────────────────────
                Section {
                    Toggle(isOn: $analyticsEnabled) {
                        HStack(spacing: 14) {
                            Image(systemName: "chart.bar")
                                .snapSymbol(16, weight: .medium)
                                .foregroundStyle(Color.snapTerracottaText)
                                .frame(minWidth: 24)
                                .accessibilityHidden(true)
                            Text("Share anonymous analytics")
                                .font(.snapBody)
                                .foregroundStyle(Color.snapEspresso)
                        }
                        .frame(minHeight: 44)
                    }
                    .tint(Color.snapTerracotta)
                    // `@AppStorage` writes the key directly, so it never goes
                    // through `Analytics.isEnabled`'s setter and the SDK was
                    // never told to stop. Custom events did stop; the SDK's own
                    // session and install signals, which carry an identifier,
                    // did not — so turning this off left them flowing.
                    .onChange(of: analyticsEnabled) { _, _ in
                        Analytics.shared.syncBackendToPersistedFlag()
                    }
                    .accessibilityLabel("Share anonymous analytics")
                    .accessibilityHint("Anonymous usage only — never your photos, item names, or prices")
                } header: {
                    Text("Privacy")
                } footer: {
                    Text("Helps us improve SnapWorth. Anonymous usage only — never your photos, item names, or prices.")
                        .fixedSize(horizontal: false, vertical: true)
                }

                // App version
                Section {
                    HStack {
                        Spacer()
                        Text("SnapWorth · v\(Self.appVersion)")
                            .font(.snapCaption)
                            .foregroundStyle(Color.snapWarmGray)
                        Spacer()
                    }
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel("SnapWorth version \(Self.appVersion)")
                }
                .listRowBackground(Color.clear)
            }
            .scrollContentBackground(.hidden)
            .background(Color.snapBackground)
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.large)
        }
        .sheet(isPresented: $showPaywall) {
            PaywallView(purchaseService: purchaseService, trigger: .settings)
        }
        .alert(vm.noticeTitle, isPresented: $vm.showNotice) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(vm.noticeMessage)
        }
        .alert("Clear history?", isPresented: $showDeleteAlert) {
            Button("Delete all", role: .destructive) {
                do {
                    try ScanRepository(context: modelContext).deleteAll(results)
                } catch {
                    // Titled for what actually failed. This reused the restore
                    // alert's state, so a storage error arrived under the
                    // heading "Restore purchases" — telling the user their
                    // purchases could not be restored when what failed was
                    // deleting their scans.
                    vm.report("Couldn't clear history",
                              AppError.from(error).errorDescription ?? "")
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(SettingsViewModel.clearHistoryMessage(count: results.count))
        }
    }
}

// MARK: - Subscription Card
private struct SubscriptionCard: View {
    let isSubscribed: Bool

    /// Derived, not typed out. This row read "3 free scans a day" for six weeks
    /// against a compiled-in allowance of 1 — true only on the welcome day the
    /// server grants extra, wrong every day after it.
    static var freeAllowanceText: String {
        let allowed = Config.freeScansAllowed
        return "\(allowed) free scan\(allowed == 1 ? "" : "s") a day"
    }
    let onUpgrade: () -> Void

    var body: some View {
        HStack(spacing: 16) {
            Image(systemName: isSubscribed ? "crown.fill" : "crown")
                .snapSymbol(24)
                .foregroundStyle(Color.snapAmber)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 2) {
                Text(isSubscribed ? "SnapWorth Pro" : "Free Plan")
                    .font(.dmSans(16, weight: .semibold, relativeTo: .body))
                    .foregroundStyle(Color.snapEspresso)
                Text(isSubscribed
                     ? "Unlimited scans · Active"
                     : "\(Self.freeAllowanceText) · Upgrade for unlimited"
                )
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)
                .fixedSize(horizontal: false, vertical: true)
            }
            // Status is one stop; the Upgrade button stays separately focusable.
            .accessibilityElement(children: .combine)
            .accessibilityLabel(isSubscribed ? "SnapWorth Pro" : "Free Plan")
            .accessibilityValue(isSubscribed
                ? "Unlimited scans, active"
                : Self.freeAllowanceText)

            Spacer()

            if !isSubscribed {
                Button("Upgrade", action: onUpgrade)
                    .font(.dmSans(13, weight: .semibold, relativeTo: .footnote))
                    .foregroundStyle(Color.snapOnAccent)
                    // Without these the surrounding text squeezes the button
                    // at accessibility sizes and "Upgrade" breaks mid-word.
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 7)
                    .background(Color.snapTerracottaFill)
                    .clipShape(Capsule())
                    .snapHitTarget()
                    .layoutPriority(1)
                    .accessibilityHint("Opens subscription options for unlimited scans")
            }
        }
        .padding(16)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }
}

// MARK: - Settings Row Label (used inside NavigationLink — no extra chevron)
private struct SettingsRowLabel: View {
    let icon: String
    let label: String

    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: icon)
                .snapSymbol(16, weight: .medium)
                .foregroundStyle(Color.snapTerracottaText)
                .frame(minWidth: 24)
                // Icon repeats the adjacent text; announcing it adds noise.
                .accessibilityHidden(true)

            Text(label)
                .font(.snapBody)
                .foregroundStyle(Color.snapEspresso)
        }
        .frame(minHeight: 44)
    }
}

// MARK: - Settings Row
private struct SettingsRow: View {
    let icon: String
    let label: String
    var destructive: Bool = false
    /// Work is running behind this row. Nothing here had a busy state, so
    /// "Restore purchases" — which can sit on `AppStore.sync()` for seconds
    /// with a system sign-in sheet over it — read as a button that did nothing.
    var isBusy: Bool = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 14) {
                Image(systemName: icon)
                    .snapSymbol(16, weight: .medium)
                    .foregroundStyle(destructive ? Color.red : Color.snapTerracottaText)
                    .frame(minWidth: 24)
                    .accessibilityHidden(true)

                Text(label)
                    .font(.snapBody)
                    .foregroundStyle(destructive ? Color.red : Color.snapEspresso)

                Spacer()

                if isBusy {
                    ProgressView()
                        .controlSize(.small)
                }
            }
            .frame(minHeight: 44)
        }
        .disabled(isBusy)
        // SwiftUI has no destructive accessibility trait (unlike UIKit), and
        // red text conveys nothing to VoiceOver — so the warning goes in the
        // hint, where it is spoken before the user activates the control.
        .accessibilityLabel(label)
        .accessibilityAddTraits(.isButton)
        .accessibilityHint(destructive ? "This cannot be undone" : "")
        // The spinner is `accessibilityHidden` by default and the row is
        // disabled, which VoiceOver announces as "dimmed" — true but not
        // informative. This says which of the two it is.
        .accessibilityValue(isBusy ? "In progress" : "")
    }
}
