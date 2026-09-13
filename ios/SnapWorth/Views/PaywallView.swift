import SwiftUI

struct PaywallView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var vm = PaywallViewModel()
    @State private var showPrivacy = false
    @State private var showTerms = false
    @State private var isReloadingPricing = false
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
                    // Only promise an offer when StoreKit says one exists —
                    // and only call it free when StoreKit says it is.
                    let offer = yearly.introductoryOffer

                    VStack(spacing: 16) {
                        Image(systemName: "sparkle")
                            .snapSymbol(44, weight: .light)
                            .foregroundStyle(Color.snapTerracottaText)
                            .symbolRenderingMode(.hierarchical)
                            .padding(.top, 56)

                        Text(PaywallCopy.headline(isYearly: isYearly, offer: offer))
                            .font(.fraunces(32, weight: .bold, relativeTo: .largeTitle))
                            .foregroundStyle(Color.snapEspresso)
                            .multilineTextAlignment(.center)
                            .snapAnimation(.easeInOut(duration: 0.2), value: isYearly)
                            .accessibilityAddTraits(.isHeader)

                        Text(PaywallCopy.subheadline(isYearly: isYearly,
                                                     price: selected.displayPrice,
                                                     offer: offer))
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
                        // Redacted per card, not across both: a fetch that
                        // returns one plan and not the other should show the
                        // real price it has rather than hide it behind a
                        // placeholder, and must never show a placeholder that
                        // reads like a price it doesn't have.
                        .redacted(reason: isLoaded(Config.yearlyProductID) ? [] : .placeholder)

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
                        .redacted(reason: isLoaded(Config.monthlyProductID) ? [] : .placeholder)
                    }
                    .padding(.horizontal, 20)

                    // ── Benefits ───────────────────────────────────────────
                    // Every row below is a real gate — one of the places that
                    // actually presents this paywall (see `PaywallTrigger`).
                    // "Full scan history" used to head the list and is not
                    // gated at all: HistoryView's grid has no `isPro` check.
                    VStack(alignment: .leading, spacing: 14) {
                        ForEach(PaywallCopy.benefits, id: \.text) { benefit in
                            BenefitRow(icon: benefit.icon, text: benefit.text)
                        }
                    }
                    .padding(20)
                    .snapCard()
                    .padding(.horizontal, 20)
                    .padding(.top, 24)
                    .accessibilityElement(children: .contain)
                    .accessibilityLabel("What's included")

                    // ── Deferred purchase (Ask to Buy / SCA) ───────────────
                    // Not red: nothing failed, and nothing is owed.
                    if let pending = vm.pendingMessage {
                        Text(pending)
                            .font(.snapCaption)
                            .foregroundStyle(Color.snapWarmGray)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, 20)
                            .padding(.top, 12)
                    }

                    // ── Pricing unavailable ────────────────────────────────
                    // The fetch didn't come back with every plan. Without this
                    // the cards read "—" forever and the CTA sat there inert
                    // with nothing said; the retry was reachable only by
                    // dismissing and reopening the sheet. It no longer requires
                    // a *total* failure: one plan missing is enough to strand a
                    // user on it, because the missing one may be the selected
                    // one.
                    if purchaseService.pricingFailed {
                        VStack(spacing: 10) {
                            Text(PaywallCopy.pricingProblem(
                                hasSomePricing: !purchaseService.pricing.isEmpty))
                                .font(.snapCaption)
                                .foregroundStyle(Color.snapWarmGray)
                                .multilineTextAlignment(.center)

                            GhostButton(title: "Try again", isLoading: isReloadingPricing) {
                                Task {
                                    isReloadingPricing = true
                                    await purchaseService.reloadProducts()
                                    vm.reconcileSelection(with: purchaseService.pricing)
                                    isReloadingPricing = false
                                }
                            }
                            .disabled(isReloadingPricing)
                        }
                        .padding(.horizontal, 20)
                        .padding(.top, 16)
                    }

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
                            title: PaywallCopy.ctaTitle(isYearly: isYearly, offer: offer),
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
                                .foregroundStyle(Color.snapWarmGray)
                                .multilineTextAlignment(.center)

                            HStack(spacing: 16) {
                                Button("Terms of Service") { showTerms = true }
                                    .snapHitTarget()
                                    .accessibilityHint("Opens the terms of service")
                                Text("·")
                                    .foregroundStyle(Color.snapWarmGray)
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
            // The default selection is yearly. If that is the plan StoreKit
            // didn't return, leaving it selected means a disabled CTA over
            // "Loading plans…" with a perfectly purchasable monthly card
            // sitting right there unselected.
            vm.reconcileSelection(with: purchaseService.pricing)
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

    /// Whether StoreKit actually returned this plan — not whether the fetch
    /// finished. A partial fetch finishes.
    func isLoaded(_ productID: String) -> Bool {
        purchaseService.pricing[productID] != nil
    }

    func yearlyDetail(_ plan: PlanPricing) -> String {
        PaywallCopy.planDetail(weekly: plan.displayPricePerWeek,
                               offer: plan.introductoryOffer)
    }
}

// MARK: - Paywall copy

/// Pure copy helpers, lifted out of the view so they can be tested.
///
/// Two rules hold across everything below.
///
/// **The word "free" requires `IntroOffer.isFree`.** The offer used to arrive
/// as a `String?` and every caller read "non-nil" as "free trial", so a paid
/// introductory offer produced the headline "Try SnapWorth free for $9.99 for
/// 3 months" — free and priced in one sentence, and the half a reader believes
/// is "free". The configured product is a real 3-day free trial today, so this
/// was never on screen; it becomes so the moment the offer is changed in App
/// Store Connect, which is a change made without touching the app.
///
/// **A unit is pluralised exactly once.** The headline shipped reading "free
/// for 3 dayss" because the service pluralised a sentence and the view
/// pluralised the result. The service now emits a singular `unit` and a count,
/// and nothing round-trips through a string.
enum PaywallCopy {
    static func headline(isYearly: Bool, offer: IntroOffer?) -> String {
        guard isYearly, let offer, offer.isFree else { return "Unlock\nSnapWorth Pro" }
        return "Try SnapWorth\nfree for \(duration(offer))"
    }

    /// The offer spelled out, with what is charged once it ends.
    ///
    /// A paid offer states its price here rather than in the headline: the
    /// headline is two lines of display type, and a price that needs a "then"
    /// clause to be true does not belong in it.
    static func subheadline(isYearly: Bool, price: String, offer: IntroOffer?) -> String {
        guard price != "—" else { return "Loading plans…" }
        let regular = "\(price)/\(isYearly ? "year" : "month")"
        guard isYearly, let offer else { return "\(regular). Cancel anytime." }
        switch offer.kind {
        case .freeTrial:
            return "Then \(regular). Cancel anytime."
        case .payUpFront:
            return "\(offer.displayPrice) for your first \(duration(offer)), "
                + "then \(regular). Cancel anytime."
        case .payAsYouGo:
            return "\(offer.displayPrice) per \(perPeriod(offer)) for \(duration(offer)), "
                + "then \(regular). Cancel anytime."
        }
    }

    /// The yearly card's detail line: value framing, then the offer.
    static func planDetail(weekly: String?, offer: IntroOffer?) -> String {
        var parts: [String] = []
        if let weekly { parts.append("\(weekly) per week") }
        if let offer { parts.append(offerPhrase(offer)) }
        return parts.isEmpty ? "Best value" : parts.joined(separator: " · ")
    }

    /// The offer in as few words as a card row allows.
    static func offerPhrase(_ offer: IntroOffer) -> String {
        switch offer.kind {
        case .freeTrial:
            // Attributive compound — "3-day free trial", never "3-days".
            return "\(offer.totalUnits)-\(offer.unit) free trial"
        case .payUpFront:
            return "\(offer.displayPrice) for your first \(duration(offer))"
        case .payAsYouGo:
            return "\(offer.displayPrice) per \(perPeriod(offer)) for \(duration(offer))"
        }
    }

    /// "Start Free Trial" is a claim about money, so it needs a free offer.
    static func ctaTitle(isYearly: Bool, offer: IntroOffer?) -> String {
        if isYearly, let offer, offer.isFree { return "Start Free Trial" }
        return isYearly ? "Subscribe Yearly" : "Subscribe Monthly"
    }

    /// Shown beside the retry when a product fetch came back short.
    ///
    /// A partial fetch is a different situation from an empty one: something is
    /// purchasable, so the copy must not imply the screen is dead.
    static func pricingProblem(hasSomePricing: Bool) -> String {
        hasSomePricing
            ? "Couldn't load every plan. Try again, or continue with the one shown."
            : "Couldn't load plans. Check your connection and try again."
    }

    /// The whole offer, e.g. "3 days" — one period times however many run.
    static func duration(_ offer: IntroOffer) -> String {
        phrase(count: offer.totalUnits, unit: offer.unit)
    }

    /// What a pay-as-you-go price is charged *per*: "month", or "2 weeks" if
    /// the period is longer than one unit. Never "per 1 month".
    static func perPeriod(_ offer: IntroOffer) -> String {
        offer.unitCount == 1 ? offer.unit : phrase(count: offer.unitCount, unit: offer.unit)
    }

    /// "3 days" / "1 day". The `hasSuffix` guard is the "3 dayss" bug's
    /// gravestone: `unit` is singular by construction in the service, and this
    /// makes it impossible for a caller to double up even if it isn't.
    static func phrase(count: Int, unit: String) -> String {
        let needsPlural = count != 1 && !unit.hasSuffix("s")
        return "\(count) \(unit)\(needsPlural ? "s" : "")"
    }

    /// One row of the paywall's "what's included" card.
    struct Benefit: Equatable {
        let icon: String
        let text: String
    }

    /// The real Pro gates, in the order a user meets them. Each one maps to a
    /// `PaywallTrigger` that presents this screen, so the list can be checked
    /// against the gates rather than drifting from them.
    static let benefits: [Benefit] = [
        Benefit(icon: "infinity", text: "Unlimited scans"),
        Benefit(icon: "chart.line.uptrend.xyaxis",
                text: "Why it's worth that — four price points and what drives them"),
        Benefit(icon: "cart.fill", text: "Snap → Sell marketplace listings"),
        Benefit(icon: "arrow.triangle.2.circlepath", text: "Thrift Flip profit calculator"),
        Benefit(icon: "tag.fill", text: "Read the care tag for a sharper estimate"),
        Benefit(icon: "chart.pie.fill", text: "Portfolio value, trend and thrift trends"),
        Benefit(icon: "square.and.arrow.up", text: "Unlimited sold flips, and CSV export"),
    ]
}

// MARK: - Benefit Row
private struct BenefitRow: View {
    let icon: String
    let text: String

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .snapSymbol(16, weight: .medium)
                .foregroundStyle(Color.snapSageText)
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
