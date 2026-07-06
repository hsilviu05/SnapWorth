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
                    SettingsRow(icon: "arrow.clockwise", label: "Restore purchases") {
                        Task { await vm.restorePurchases(service: purchaseService) }
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
                Section("Data") {
                    SettingsRow(icon: "trash", label: "Clear scan history", destructive: true) {
                        showDeleteAlert = true
                    }
                }

                // App version
                Section {
                    HStack {
                        Spacer()
                        Text("SnapWorth · v1.0.0")
                            .font(.snapCaption)
                            .foregroundStyle(Color.snapWarmGray)
                        Spacer()
                    }
                }
                .listRowBackground(Color.clear)
            }
            .scrollContentBackground(.hidden)
            .background(Color.snapBackground)
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.large)
        }
        .sheet(isPresented: $showPaywall) {
            PaywallView(purchaseService: purchaseService)
        }
        .alert("Restore purchases", isPresented: $vm.showRestoreAlert) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(vm.restoreMessage ?? "")
        }
        .alert("Clear history?", isPresented: $showDeleteAlert) {
            Button("Delete all", role: .destructive) {
                results.forEach { modelContext.delete($0) }
                do { try modelContext.save() } catch {
                    vm.restoreMessage = "Failed to clear history. Please try again."
                    vm.showRestoreAlert = true
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This will permanently delete all \(results.count) saved scans.")
        }
    }
}

// MARK: - Subscription Card
private struct SubscriptionCard: View {
    let isSubscribed: Bool
    let onUpgrade: () -> Void

    var body: some View {
        HStack(spacing: 16) {
            Image(systemName: isSubscribed ? "crown.fill" : "crown")
                .font(.system(size: 24))
                .foregroundStyle(Color.snapAmber)

            VStack(alignment: .leading, spacing: 2) {
                Text(isSubscribed ? "SnapWorth Pro" : "Free Plan")
                    .font(.dmSans(16, weight: .semibold))
                    .foregroundStyle(Color.snapEspresso)
                Text(isSubscribed
                     ? "Unlimited scans · Active"
                     : "3 free scans · Upgrade for unlimited"
                )
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)
            }

            Spacer()

            if !isSubscribed {
                Button("Upgrade", action: onUpgrade)
                    .font(.dmSans(13, weight: .semibold))
                    .foregroundStyle(Color.snapBackground)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 7)
                    .background(Color.snapTerracotta)
                    .clipShape(Capsule())
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
                .font(.system(size: 16, weight: .medium))
                .foregroundStyle(Color.snapTerracotta)
                .frame(width: 24)

            Text(label)
                .font(.snapBody)
                .foregroundStyle(Color.snapEspresso)
        }
    }
}

// MARK: - Settings Row
private struct SettingsRow: View {
    let icon: String
    let label: String
    var destructive: Bool = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 14) {
                Image(systemName: icon)
                    .font(.system(size: 16, weight: .medium))
                    .foregroundStyle(destructive ? Color.red : Color.snapTerracotta)
                    .frame(width: 24)

                Text(label)
                    .font(.snapBody)
                    .foregroundStyle(destructive ? Color.red : Color.snapEspresso)

                Spacer()
            }
        }
    }
}
