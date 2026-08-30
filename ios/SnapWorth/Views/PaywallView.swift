import SwiftUI

struct PaywallView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var vm = PaywallViewModel()
    @State private var showPrivacy = false
    @State private var showTerms = false
    let purchaseService: any PurchaseService
    /// What surfaced this paywall — attributed to `paywall_viewed`.
    var trigger: PaywallTrigger = .upgradeButton

    var body: some View {
        ZStack(alignment: .topTrailing) {
            ScrollView {
                VStack(spacing: 0) {
                    // ── Header ─────────────────────────────────────────────
                    let isYearly = vm.selectedProductID == Config.yearlyProductID
                    let yearly = pricing(Config.yearlyProductID)
                    let monthly = pricing(Config.monthlyProductID)
                    let selected = isYearly ? yearly : monthly
                    // Only promise a trial when StoreKit says one exists.
                    let trial = yearly.introductoryOffer

                    VStack(spacing: 16) {
                        Image(systemName: "sparkle")
                            .snapSymbol(44, weight: .light)
                            .foregroundStyle(Color.snapTerracotta)
                            .symbolRenderingMode(.hierarchical)
                            .padding(.top, 56)

                        Text(PaywallCopy.headline(isYearly: isYearly, trial: trial))
                            .font(.fraunces(32, weight: .bold, relativeTo: .largeTitle))
                            .foregroundStyle(Color.snapEspresso)
                            .multilineTextAlignment(.center)
                            .snapAnimation(.easeInOut(duration: 0.2), value: isYearly)
                            .accessibilityAddTraits(.isHeader)

                        Text(subheadline(isYearly: isYearly, plan: selected, trial: trial))
                            .font(.snapCaption)
                            .foregroundStyle(Color.snapWarmGray)
                            .multilineTextAlignment(.center)
                            .snapAnimation(.easeInOut(duration: 0.2), value: isYearly)
                    }
                    .padding(.bottom, 32)
                    // One header stop: the offer and its price read together.
                    .accessibilityElement(children: .combine)
                    .accessibilitySortPriority(100)

                    // ── Plan cards ─────────────────────────────────────────
                    VStack(spacing: 12) {
                        PlanCard(
                            title: "Yearly",
                            price: yearly.displayPrice,
                            priceDetail: yearlyDetail(yearly),
                            badge: yearly.savingsPercent.map { "SAVE \($0)%" } ?? "BEST VALUE",
                            isSelected: vm.selectedProductID == Config.yearlyProductID
                        ) {
                            Haptics.selection()
                            vm.selectedProductID = Config.yearlyProductID
                        }

                        PlanCard(
                            title: "Monthly",
                            price: monthly.displayPrice,
                            priceDetail: "Flexible, cancel anytime",
                            badge: nil,
                            isSelected: vm.selectedProductID == Config.monthlyProductID
                        ) {
                            Haptics.selection()
                            vm.selectedProductID = Config.monthlyProductID
                        }
                    }
                    .padding(.horizontal, 20)
                    .redacted(reason: purchaseService.isPricingLoaded ? [] : .placeholder)

                    // ── Benefits ───────────────────────────────────────────
                    VStack(alignment: .leading, spacing: 14) {
                        BenefitRow(icon: "infinity", text: "Unlimited scans")
                        BenefitRow(icon: "chart.line.uptrend.xyaxis", text: "AI resale estimates")
                        BenefitRow(icon: "cart.fill", text: "Snap → Sell marketplace listings")
                        BenefitRow(icon: "arrow.triangle.2.circlepath", text: "Thrift Flip profit calculator")
                        BenefitRow(icon: "clock.arrow.circlepath", text: "Full scan history")
                    }
                    .padding(20)
                    .snapCard()
                    .padding(.horizontal, 20)
                    .padding(.top, 24)
                    .accessibilityElement(children: .contain)
                    .accessibilityLabel("What's included")

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
                            title: ctaTitle(isYearly: isYearly, trial: trial),
                            isLoading: vm.isPurchasing
                        ) {
                            Task { await vm.purchase(service: purchaseService) }
                        }
                        // Never let a tap through before StoreKit has confirmed
                        // the product exists — that path produced the "purchase
                        // unavailable" errors App Review rejects for.
                        .disabled(vm.isPurchasing || vm.isRestoring || !isPurchasable)

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
                                    .snapHitTarget()
                                    .accessibilityHint("Opens the terms of service")
                                Text("·")
                                    .foregroundStyle(Color.snapWarmGray.opacity(0.5))
                                    .accessibilityHidden(true)
                                Button("Privacy Policy") { showPrivacy = true }
                                    .snapHitTarget()
                                    .accessibilityHint("Opens the privacy policy")
                            }
                            .font(.dmSans(11, weight: .semibold, relativeTo: .caption2))
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
                        .snapSymbol(14, weight: .semibold)
                        .foregroundStyle(Color.snapWarmGray)
                        .padding(10)
                        .background(Color.snapBorder)
                        .clipShape(Circle())
                }
                .snapHitTarget()
                .padding(.top, 56)
                .padding(.trailing, 20)
                .transition(.opacity.combined(with: .scale(scale: 0.8)))
                .accessibilityLabel("Close")
                .accessibilityHint("Dismisses this offer")
                // Escape route must be reachable first, not after the whole
                // marketing page.
                .accessibilitySortPriority(200)
            }
        }
        .snapAnimation(.spring(duration: 0.3), value: vm.showCloseButton)
        .task {
            // Self-heal a failed initial product fetch: the paywall is the only
            // place pricing matters, so retry on presentation rather than
            // leaving the user with an inert "—".
            if !purchaseService.isPricingLoaded || purchaseService.pricing.isEmpty {
                await purchaseService.reloadProducts()
            }
        }
        .onAppear {
            vm.startCloseButtonTimer()
            Analytics.shared.track(.paywallViewed(trigger: trigger))
        }
        .onDisappear { vm.cancelTimer() }
        .onChange(of: vm.isPurchaseComplete) { _, complete in
            if complete { dismiss() }
        }
        .sheet(isPresented: $showPrivacy) {
            NavigationStack { PrivacyPolicyView() }
                .presentationDetents([.large])
                .presentationDragIndicator(.visible)
        }
        .sheet(isPresented: $showTerms) {
            NavigationStack { TermsOfServiceView() }
                .presentationDetents([.large])
                .presentationDragIndicator(.visible)
        }
    }
}

// MARK: - Pricing copy
//
// Every string below is derived from StoreKit's own `Product`, so it is already
// in the user's storefront currency and locale. Nothing here hardcodes an
// amount, a currency symbol, or a trial length: doing so previously showed
// non-US users a US-dollar figure while Apple charged them in local currency.

private extension PaywallView {
    var isPurchasable: Bool {
        purchaseService.pricing[vm.selectedProductID] != nil
    }

    func pricing(_ productID: String) -> PlanPricing {
        purchaseService.pricing[productID] ?? .loading(productID)
    }

    func subheadline(isYearly: Bool, plan: PlanPricing, trial: String?) -> String {
        guard plan.displayPrice != "—" else { return "Loading plans…" }
        let period = isYearly ? "year" : "month"
        if isYearly, trial != nil {
            return "Then \(plan.displayPrice)/\(period). Cancel anytime."
        }
        return "\(plan.displayPrice)/\(period). Cancel anytime."
    }

    func yearlyDetail(_ plan: PlanPricing) -> String {
        var parts: [String] = []
        if let weekly = plan.displayPricePerWeek { parts.append("\(weekly) per week") }
        if let intro = plan.introductoryOffer { parts.append(intro) }
        return parts.isEmpty ? "Best value" : parts.joined(separator: " · ")
    }

    func ctaTitle(isYearly: Bool, trial: String?) -> String {
        if isYearly, trial != nil { return "Start Free Trial" }
        return isYearly ? "Subscribe Yearly" : "Subscribe Monthly"
    }

}

// MARK: - Paywall copy

/// Pure copy helpers, lifted out of the view so they can be tested.
///
/// The headline is the highest-intent string in the app, and it shipped reading
/// "free for 3 dayss": `StoreKitPurchaseService.introductoryDescription`
/// pluralised the unit, then `trialDuration` pluralised the result again. Both
/// sides are now defensive — the source emits the singular attributive form
/// ("3-day free trial"), and `trialDuration` will not re-pluralise a unit that
/// already ends in "s".
enum PaywallCopy {
    static func headline(isYearly: Bool, trial: String?) -> String {
        guard isYearly, let trial else { return "Unlock\nSnapWorth Pro" }
        return "Try SnapWorth\nfree for \(trialDuration(trial))"
    }

    /// "3-day free trial" → "3 days". Falls back to the raw phrase.
    static func trialDuration(_ offer: String) -> String {
        let head = offer.replacingOccurrences(of: " free trial", with: "")
        let parts = head.split(separator: "-")
        guard parts.count == 2, let count = Int(parts[0]) else { return head }
        let unit = parts[1]
        let needsPlural = count != 1 && !unit.hasSuffix("s")
        return "\(count) \(unit)\(needsPlural ? "s" : "")"
    }
}

// MARK: - Benefit Row
private struct BenefitRow: View {
    let icon: String
    let text: String

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .snapSymbol(16, weight: .medium)
                .foregroundStyle(Color.snapSage)
                .frame(minWidth: 24)

            Text(text)
                .font(.snapBodyMedium)
                .foregroundStyle(Color.snapEspresso)
                .fixedSize(horizontal: false, vertical: true)

            Spacer()
        }
        // The icon is decorative — the text already names the benefit.
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(text)
    }
}
