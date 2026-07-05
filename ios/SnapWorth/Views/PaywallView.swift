import SwiftUI

struct PaywallView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var vm = PaywallViewModel()
    let purchaseService: any PurchaseService

    var body: some View {
        ZStack(alignment: .topTrailing) {
            ScrollView {
                VStack(spacing: 0) {
                    // ── Header ─────────────────────────────────────────────
                    VStack(spacing: 16) {
                        Image(systemName: "sparkle")
                            .font(.system(size: 44, weight: .light))
                            .foregroundStyle(Color.snapTerracotta)
                            .symbolRenderingMode(.hierarchical)
                            .padding(.top, 56)

                        Text("Try SnapWorth\nfree for 3 days")
                            .font(.fraunces(32, weight: .bold))
                            .foregroundStyle(Color.snapEspresso)
                            .multilineTextAlignment(.center)

                        Text("Then auto-renews. Cancel anytime.")
                            .font(.snapCaption)
                            .foregroundStyle(Color.snapWarmGray)
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

                        Button("Restore purchase") {
                            Task { await vm.restore(service: purchaseService) }
                        }
                        .font(.snapBody)
                        .foregroundStyle(Color.snapWarmGray)

                        Group {
                            Text("By subscribing you agree to our ")
                            + Text("Terms").underline()
                            + Text(" and ")
                            + Text("Privacy Policy").underline()
                            + Text(".")
                        }
                        .font(.dmSans(11))
                        .foregroundStyle(Color.snapWarmGray.opacity(0.7))
                        .multilineTextAlignment(.center)
                        .onTapGesture {
                            UIApplication.shared.open(
                                URL(string: "https://snapworth-backend-production.up.railway.app/privacy")!
                            )
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
        .onChange(of: vm.isPurchaseComplete) { _, complete in
            if complete { dismiss() }
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
