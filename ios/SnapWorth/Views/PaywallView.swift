import SwiftUI

struct PaywallView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var vm = PaywallViewModel()
    @State private var showPrivacy = false
    @State private var showTerms = false
    let purchaseService: any PurchaseService

    var body: some View {
        ZStack(alignment: .topTrailing) {
            ScrollView {
                VStack(spacing: 0) {
                    // ── Header ─────────────────────────────────────────────
                    let isYearly = vm.selectedProductID == Config.yearlyProductID
                    VStack(spacing: 16) {
                        Image(systemName: "sparkle")
                            .font(.system(size: 44, weight: .light))
                            .foregroundStyle(Color.snapTerracotta)
                            .symbolRenderingMode(.hierarchical)
                            .padding(.top, 56)

                        Text(isYearly ? "Try SnapWorth\nfree for 3 days" : "Unlock\nSnapWorth Pro")
                            .font(.fraunces(32, weight: .bold))
                            .foregroundStyle(Color.snapEspresso)
                            .multilineTextAlignment(.center)
                            .animation(.easeInOut(duration: 0.2), value: isYearly)

                        Text(isYearly ? "Then $39.99/yr. Cancel anytime." : "$4.99/week. Cancel anytime.")
                            .font(.snapCaption)
                            .foregroundStyle(Color.snapWarmGray)
                            .animation(.easeInOut(duration: 0.2), value: isYearly)
                    }
                    .padding(.bottom, 32)

                    // ── Plan cards ─────────────────────────────────────────
                    VStack(spacing: 12) {
                        PlanCard(
                            title: "Yearly",
                            price: "$39.99/yr",
                            priceDetail: "$0.77 per week",
                            badge: "BEST VALUE",
                            isSelected: vm.selectedProductID == Config.yearlyProductID
                        ) {
                            vm.selectedProductID = Config.yearlyProductID
                        }

                        PlanCard(
                            title: "Weekly",
                            price: "$4.99/wk",
                            priceDetail: "Billed weekly",
                            badge: nil,
                            isSelected: vm.selectedProductID == Config.weeklyProductID
                        ) {
                            vm.selectedProductID = Config.weeklyProductID
                        }
                    }
                    .padding(.horizontal, 20)

                    // ── Benefits ───────────────────────────────────────────
                    VStack(alignment: .leading, spacing: 14) {
                        BenefitRow(icon: "infinity", text: "Unlimited scans")
                        BenefitRow(icon: "chart.line.uptrend.xyaxis", text: "Real sold-price data")
                        BenefitRow(icon: "pencil.and.list.clipboard", text: "AI listing writer")
                        BenefitRow(icon: "clock.arrow.circlepath", text: "Full scan history")
                    }
                    .padding(20)
                    .snapCard()
                    .padding(.horizontal, 20)
                    .padding(.top, 24)

                    // ── Error ──────────────────────────────────────────────
                    if let error = vm.errorMessage {
                        Text(error)
                            .font(.snapCaption)
                            .foregroundStyle(.red)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, 20)
                            .padding(.top, 12)
                    }

                    // ── CTA ────────────────────────────────────────────────
                    VStack(spacing: 16) {
                        PrimaryButton(
                            title: vm.selectedProductID == Config.yearlyProductID
                                ? "Start Free Trial"
                                : "Subscribe Weekly",
                            isLoading: vm.isPurchasing
                        ) {
                            Task { await vm.purchase(service: purchaseService) }
                        }
                        .disabled(vm.isPurchasing || vm.isRestoring)

                        GhostButton(title: "Restore purchase", isLoading: vm.isRestoring) {
                            Task { await vm.restore(service: purchaseService) }
                        }
                        .disabled(vm.isPurchasing || vm.isRestoring)

                        VStack(spacing: 8) {
                            Text("Subscription automatically renews unless cancelled at least 24 hours before the end of the current period. Your Apple ID account will be charged for renewal within 24 hours prior to the end of the current period. Manage or cancel anytime in your Apple ID Account Settings. Any unused portion of a free trial will be forfeited upon purchase.")
                                .font(.dmSans(10))
                                .foregroundStyle(Color.snapWarmGray.opacity(0.65))
                                .multilineTextAlignment(.center)

                            HStack(spacing: 16) {
                                Button("Terms of Service") { showTerms = true }
                                Text("·").foregroundStyle(Color.snapWarmGray.opacity(0.5))
                                Button("Privacy Policy") { showPrivacy = true }
                            }
                            .font(.dmSans(11, weight: .semibold))
                            .foregroundStyle(Color.snapWarmGray)
                        }
                    }
                    .padding(.horizontal, 20)
                    .padding(.top, 24)
                    .padding(.bottom, 48)
                }
            }
            .background(Color.snapBackground)

            // ── Delayed close button ───────────────────────────────────────
            if vm.showCloseButton {
                Button {
                    dismiss()
                } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(Color.snapWarmGray)
                        .padding(10)
                        .background(Color.snapBorder)
                        .clipShape(Circle())
                }
                .padding(.top, 56)
                .padding(.trailing, 20)
                .transition(.opacity.combined(with: .scale(scale: 0.8)))
            }
        }
        .animation(.spring(duration: 0.3), value: vm.showCloseButton)
        .onAppear { vm.startCloseButtonTimer() }
        .onDisappear { vm.cancelTimer() }
        .onChange(of: vm.isPurchaseComplete) { _, complete in
            if complete { dismiss() }
        }
        .sheet(isPresented: $showPrivacy) {
            NavigationStack { PrivacyPolicyView() }
                .presentationDetents([.large])
        }
        .sheet(isPresented: $showTerms) {
            NavigationStack { TermsOfServiceView() }
                .presentationDetents([.large])
        }
    }
}

// MARK: - Benefit Row
private struct BenefitRow: View {
    let icon: String
    let text: String

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 16, weight: .medium))
                .foregroundStyle(Color.snapSage)
                .frame(width: 24)

            Text(text)
                .font(.snapBodyMedium)
                .foregroundStyle(Color.snapEspresso)

            Spacer()
        }
    }
}
