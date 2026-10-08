import SwiftUI

struct PaywallView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var vm = PaywallViewModel()
    @State private var showPrivacy = false
    @State private var showTerms = false
    @State private var isReloadingPricing = false
    /// Referrals (#97): the entry is hidden unless the server has them on.
    @State private var referralsEnabled = false
    @State private var showRedeemInvite = false
    let purchaseService: any PurchaseService
    /// What surfaced this paywall — attributed to `paywall_viewed`,
    /// `paywall_dismissed` and the three purchase events.
    let trigger: PaywallTrigger
    /// What the header and the top of the list lead with, or nil for the
    /// offer as the headline over the list in its usual order.
    ///
    /// The trigger's own pitch (`PaywallCopy.pitch(for:)`) unless the caller
    /// passes one. A caller does when the trigger's pitch would promise what
    /// buying there does not deliver — see `ResultView.paywallPitch(for:)`.
    /// The trigger is still what the events report, so a paywall that leads
    /// with the offer is counted at the gate it opened from.
    let pitch: PaywallCopy.Pitch?

    init(purchaseService: any PurchaseService, trigger: PaywallTrigger = .upgradeButton) {
        self.init(purchaseService: purchaseService, trigger: trigger,
                  pitch: PaywallCopy.pitch(for: trigger))
    }

    init(purchaseService: any PurchaseService, trigger: PaywallTrigger,
         pitch: PaywallCopy.Pitch?) {
        self.purchaseService = purchaseService
        self.trigger = trigger
        self.pitch = pitch
    }

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

                        Text(PaywallCopy.headline(pitch: pitch, isYearly: isYearly, offer: offer))
                            .font(.fraunces(32, weight: .bold, relativeTo: .largeTitle))
                            .foregroundStyle(Color.snapEspresso)
                            .multilineTextAlignment(.center)
                            .snapAnimation(.easeInOut(duration: 0.2), value: isYearly)
                            .accessibilityAddTraits(.isHeader)

                        Text(PaywallCopy.subheadline(pitch: pitch, isYearly: isYearly,
                                                     price: selected.displayPrice,
                                                     offer: offer))
                            .font(.snapCaption)
                            .foregroundStyle(Color.snapWarmGray)
                            .multilineTextAlignment(.center)
                            .snapAnimation(.easeInOut(duration: 0.2), value: isYearly)

                        // The free alternative, said on the paywall that the
                        // spent allowance opened: when the scan is back, and
                        // the streak a return keeps going. Read at render, so
                        // it is never shown to someone with a scan left.
                        if PaywallCopy.showsFreeScanReturn(for: trigger,
                                                           remaining: FreeScanCounter.remaining) {
                            Text(PaywallCopy.freeScanReturn(resetsAt: FreeScanCounter.nextReset(),
                                                            streak: ScanStreak.current()))
                                .font(.snapCaption)
                                .foregroundStyle(Color.snapWarmGray)
                                .multilineTextAlignment(.center)
                                .padding(.horizontal, 12)
                                .padding(.vertical, 6)
                                .background(Color.snapBorder.opacity(0.5))
                                .clipShape(Capsule())
                        }
                    }
                    .padding(.bottom, 32)
                    // One header stop: the offer and its price read together.
                    .accessibilityElement(children: .combine)
                    .accessibilitySortPriority(100)

                    // ── Plan cards ─────────────────────────────────────────
                    // Each a placeholder until StoreKit returns its plan, per
                    // card, not across both: a fetch that returns one plan and
                    // not the other should show the real price it has rather
                    // than hide it behind a placeholder, and must never show a
                    // placeholder that reads like a price it doesn't have.
                    //
                    // A placeholder also cannot be tapped (`PlanCard.isLoaded`).
                    // A tap on the grey card moved the selection to a product
                    // StoreKit never returned, which drives three derived
                    // values wrong at once: the CTA goes inert, `pricing(_:)`
                    // falls back to a "—" placeholder, and the subheadline
                    // reads "Loading plans…" forever with nothing loading.
                    // `reconcileSelection` runs only from `.task` and the
                    // retry, so it cannot undo it — the paywall could not be
                    // bought from at all, and the way out was guessing that
                    // tapping the other card back repairs the screen.
                    VStack(spacing: 12) {
                        PlanCard(
                            title: String(localized: "Yearly"),
                            price: yearly.displayPrice,
                            priceDetail: yearlyDetail(yearly),
                            badge: yearly.savingsPercent.map { String(localized: "SAVE \($0.formatted(.percent))") }
                                ?? String(localized: "BEST VALUE"),
                            isSelected: vm.selectedProductID == Config.yearlyProductID,
                            isLoaded: isLoaded(Config.yearlyProductID)
                        ) {
                            Haptics.selection()
                            vm.selectedProductID = Config.yearlyProductID
                        }

                        PlanCard(
                            title: String(localized: "Monthly"),
                            price: monthly.displayPrice,
                            priceDetail: String(localized: "Flexible, cancel anytime"),
                            badge: nil,
                            isSelected: vm.selectedProductID == Config.monthlyProductID,
                            isLoaded: isLoaded(Config.monthlyProductID)
                        ) {
                            Haptics.selection()
                            vm.selectedProductID = Config.monthlyProductID
                        }
                    }
                    .padding(.horizontal, 20)

                    // ── Benefits ───────────────────────────────────────────
                    // Every row below is a real gate — one of the places that
                    // actually presents this paywall (see `PaywallTrigger`).
                    // "Full scan history" used to head the list and is not
                    // gated at all: HistoryView's grid has no `isPro` check.
                    VStack(alignment: .leading, spacing: 14) {
                        ForEach(PaywallCopy.benefits(pitch: pitch), id: \.text) { benefit in
                            BenefitRow(icon: benefit.icon, text: benefit.text)
                        }
                        Text(PaywallCopy.fairUse)
                            .font(.snapCaption)
                            .foregroundStyle(Color.snapWarmGray)
                            .fixedSize(horizontal: false, vertical: true)
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
                            // `ctaTitle` returns copy that is already translated, so this is a
                            // key that will not be found — and `LocalizedStringKey` falls back to
                            // showing the string it was built from, which is what we want.
                            title: LocalizedStringKey(PaywallCopy.ctaTitle(isYearly: isYearly, offer: offer)),
                            isLoading: vm.isPurchasing
                        ) {
                            Task { await vm.purchase(service: purchaseService, trigger: trigger) }
                        }
                        // Never let a tap through before StoreKit has confirmed
                        // the product exists — that path produced the "purchase
                        // unavailable" errors App Review rejects for.
                        .disabled(vm.isPurchasing || vm.isRestoring || !isPurchasable)

                        GhostButton(title: "Restore purchase", isLoading: vm.isRestoring) {
                            Task { await vm.restore(service: purchaseService) }
                        }
                        .disabled(vm.isPurchasing || vm.isRestoring)

                        if referralsEnabled {
                            GhostButton(title: "Have an invite code?") { showRedeemInvite = true }
                                .disabled(vm.isPurchasing || vm.isRestoring)
                        }

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
        .onAppear { vm.didAppear(trigger: trigger) }
        .onDisappear { vm.didDisappear(trigger: trigger) }
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
        .sheet(isPresented: $showRedeemInvite) {
            RedeemInviteView()
        }
        .task { referralsEnabled = await ReferralAPIClient.shared.status().enabled }
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
        guard isYearly, let offer, offer.isFree else {
            return String(localized: "Unlock\nSnapWorth Pro")
        }
        return String(localized: "Try SnapWorth\nfree for \(duration(offer))")
    }

    /// The offer spelled out, with what is charged once it ends.
    ///
    /// A paid offer states its price here rather than in the headline: the
    /// headline is two lines of display type, and a price that needs a "then"
    /// clause to be true does not belong in it.
    static func subheadline(isYearly: Bool, price: String, offer: IntroOffer?) -> String {
        guard price != "—" else { return String(localized: "Loading plans…") }
        let regular = isYearly ? String(localized: "\(price)/year")
                               : String(localized: "\(price)/month")
        guard isYearly, let offer else {
            return String(localized: "\(regular). Cancel anytime.")
        }
        switch offer.kind {
        case .freeTrial:
            return String(localized: "Then \(regular). Cancel anytime.")
        case .payUpFront:
            return String(localized:
                "\(offer.displayPrice) for your first \(duration(offer)), then \(regular). Cancel anytime.")
        case .payAsYouGo:
            return String(localized:
                "\(offer.displayPrice) per \(perPeriod(offer)) for \(duration(offer)), then \(regular). Cancel anytime.")
        }
    }

    /// The yearly card's detail line: value framing, then the offer.
    static func planDetail(weekly: String?, offer: IntroOffer?) -> String {
        var parts: [String] = []
        if let weekly { parts.append(String(localized: "\(weekly) per week")) }
        if let offer { parts.append(offerPhrase(offer)) }
        return parts.isEmpty ? String(localized: "Best value") : parts.joined(separator: " · ")
    }

    /// The offer in as few words as a card row allows.
    static func offerPhrase(_ offer: IntroOffer) -> String {
        switch offer.kind {
        case .freeTrial:
            // Attributive compound — "3-day free trial", never "3-days". One key per
            // unit, each with the count as its only argument, so a language that
            // inflects at 1 / 2–19 / 20 can say all three from the string table;
            // English is invariant and repeats itself, which is the point.
            switch singular(offer.unit) {
            case "day":   return String(localized: "\(offer.totalUnits)-day free trial")
            case "week":  return String(localized: "\(offer.totalUnits)-week free trial")
            case "month": return String(localized: "\(offer.totalUnits)-month free trial")
            case "year":  return String(localized: "\(offer.totalUnits)-year free trial")
            default:      return "\(offer.totalUnits)-\(offer.unit) free trial"
            }
        case .payUpFront:
            return String(localized: "\(offer.displayPrice) for your first \(duration(offer))")
        case .payAsYouGo:
            return String(localized: "\(offer.displayPrice) per \(perPeriod(offer)) for \(duration(offer))")
        }
    }

    /// "Start Free Trial" is a claim about money, so it needs a free offer.
    static func ctaTitle(isYearly: Bool, offer: IntroOffer?) -> String {
        if isYearly, let offer, offer.isFree { return String(localized: "Start Free Trial") }
        return isYearly ? String(localized: "Subscribe Yearly")
                        : String(localized: "Subscribe Monthly")
    }

    /// Shown beside the retry when a product fetch came back short.
    ///
    /// A partial fetch is a different situation from an empty one: something is
    /// purchasable, so the copy must not imply the screen is dead.
    static func pricingProblem(hasSomePricing: Bool) -> String {
        hasSomePricing
            ? String(localized: "Couldn't load every plan. Try again, or continue with the one shown.")
            : String(localized: "Couldn't load plans. Check your connection and try again.")
    }

    /// The whole offer, e.g. "3 days" — one period times however many run.
    static func duration(_ offer: IntroOffer) -> String {
        phrase(count: offer.totalUnits, unit: offer.unit)
    }

    /// What a pay-as-you-go price is charged *per*: "month", or "2 weeks" if
    /// the period is longer than one unit. Never "per 1 month".
    static func perPeriod(_ offer: IntroOffer) -> String {
        offer.unitCount == 1 ? unitName(offer.unit)
                             : phrase(count: offer.unitCount, unit: offer.unit)
    }

    /// One period, with no number in front of it: "month", "lună".
    static func unitName(_ unit: String) -> String {
        switch singular(unit) {
        case "day":   return String(localized: "day", comment: "One subscription period")
        case "week":  return String(localized: "week", comment: "One subscription period")
        case "month": return String(localized: "month", comment: "One subscription period")
        case "year":  return String(localized: "year", comment: "One subscription period")
        default:      return unit
        }
    }

    /// StoreKit hands us a singular unit; this survives a caller that doesn't.
    static func singular(_ unit: String) -> String {
        unit.hasSuffix("s") ? String(unit.dropLast()) : unit
    }

    /// "3 days" / "1 day". The `hasSuffix` guard is the "3 dayss" bug's
    /// gravestone: `unit` is singular by construction in the service, and this
    /// makes it impossible for a caller to double up even if it isn't.
    static func phrase(count: Int, unit: String) -> String {
        switch singular(unit) {
        case "day":   return String(localized: "\(count) days")
        case "week":  return String(localized: "\(count) weeks")
        case "month": return String(localized: "\(count) months")
        case "year":  return String(localized: "\(count) years")
        default:
            let needsPlural = count != 1 && !unit.hasSuffix("s")
            return "\(count) \(unit)\(needsPlural ? "s" : "")"
        }
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
        Benefit(icon: "infinity", text: String(localized: "Unlimited scans")),
        // Not "a whole pile in one go": Pro scans are capped at 60 an hour,
        // and one address at 60 requests across scans and drafts, so a big
        // pile pauses partway. What Haul does promise is that the camera
        // never waits for a valuation, and that it keeps every photo.
        Benefit(icon: "square.stack.3d.up.fill",
                text: String(localized: "Haul mode — snap item after item while each is valued, with a running total")),
        Benefit(icon: "chart.line.uptrend.xyaxis",
                text: String(localized: "Why it's worth that — four price points and what drives them")),
        Benefit(icon: "cart.fill", text: String(localized: "Snap → Sell marketplace listings")),
        Benefit(icon: "tag.fill", text: String(localized: "Read the care tag for a sharper estimate")),
        // History, not the value: the portfolio total is on everyone's
        // History tab. Only its trend (`.portfolioTrend`) and `.trends` are Pro.
        Benefit(icon: "chart.pie.fill", text: String(localized: "Portfolio value history and thrift trends")),
        Benefit(icon: "square.and.arrow.up", text: String(localized: "Unlimited sold flips, and CSV export")),
    ]

    // ── Why it opened ────────────────────────────────────────────────

    /// What a paywall opened from a gate says first: the thing the user
    /// reached for, as the headline, and its row at the top of the list.
    ///
    /// The headline and the list used to be the same for every trigger, so
    /// someone who tapped "Add the tag" read the trial pitch with the care
    /// tag fifth in the list, below four things they had not asked about.
    ///
    /// The row is named by its icon: unique in the list, and stable where the
    /// text is not — it is translated, and reworded more often than redrawn.
    struct Pitch: Equatable {
        let headline: String
        let leadIcon: String
    }

    /// Exhaustive on purpose, so a new trigger cannot ship without someone
    /// deciding what its paywall leads with.
    ///
    /// None of these may say "free": the headline is shown whatever the offer
    /// is, and the word needs `IntroOffer.isFree` (see the type's comment).
    /// The offer moves to the line under it — see `subheadline(pitch:…)`.
    static func pitch(for trigger: PaywallTrigger) -> Pitch? {
        switch trigger {
        // Nobody reached for anything: the intro paywall after the first
        // result, and Settings' own "Upgrade". The offer stays the headline.
        case .onboarding, .settings:
            return nil
        // `.upgradeButton` is the capsule the Scan tab shows only once the
        // allowance is spent, so it is the scan limit by another door.
        case .scanLimit, .upgradeButton:
            return Pitch(headline: String(localized: "Keep scanning today"), leadIcon: "infinity")
        // Not "in one go": a haul shares the hourly scan limit and can pause
        // partway (see the Haul row below). It keeps every photo and values
        // the rest when it may, so the whole haul does get scanned.
        case .haul:
            return Pitch(headline: String(localized: "Scan a whole haul"),
                         leadIcon: "square.stack.3d.up.fill")
        // True where buying shows the find's breakdown: a fresh result,
        // re-read once the purchase lands, or a full panel. Not on a free
        // user's thin find reopened from My Finds or My Flips, which nothing
        // re-reads; ResultView passes no pitch there
        // (`ResultView.paywallPitch(for:)`).
        case .valuationDetail:
            return Pitch(headline: String(localized: "See why this price"),
                         leadIcon: "chart.line.uptrend.xyaxis")
        case .snapSell:
            return Pitch(headline: String(localized: "Turn finds into listings"), leadIcon: "cart.fill")
        case .addTag:
            return Pitch(headline: String(localized: "Read the care tag"), leadIcon: "tag.fill")
        case .portfolioTrend:
            return Pitch(headline: String(localized: "See your value over time"),
                         leadIcon: "chart.pie.fill")
        case .trends:
            return Pitch(headline: String(localized: "See what's trending"), leadIcon: "chart.pie.fill")
        case .ledgerHistory:
            return Pitch(headline: String(localized: "See every flip"), leadIcon: "square.and.arrow.up")
        case .ledgerExport:
            return Pitch(headline: String(localized: "Export your flips"), leadIcon: "square.and.arrow.up")
        }
    }

    static func headline(pitch: Pitch?, isYearly: Bool, offer: IntroOffer?) -> String {
        pitch?.headline ?? headline(isYearly: isYearly, offer: offer)
    }

    /// Under a pitch, the headline no longer names the free trial, so this
    /// line must: "Then $39.99/year" would follow nothing, and the trial would
    /// be stated only by the button. Every other case already spells its
    /// offer out in full and is unchanged.
    static func subheadline(pitch: Pitch?, isYearly: Bool, price: String,
                            offer: IntroOffer?) -> String {
        guard pitch != nil, price != "—", isYearly, let offer, offer.isFree else {
            return subheadline(isYearly: isYearly, price: price, offer: offer)
        }
        let regular = String(localized: "\(price)/year")
        return String(localized: "Free for \(duration(offer)), then \(regular). Cancel anytime.")
    }

    /// The pitch's row first, the rest in their usual order.
    static func benefits(pitch: Pitch?) -> [Benefit] {
        guard let icon = pitch?.leadIcon,
              let lead = benefits.first(where: { $0.icon == icon }) else { return benefits }
        return [lead] + benefits.filter { $0 != lead }
    }

    /// Whether the paywall says when the free scan is back.
    ///
    /// Only where the spent allowance opened it, and only while it is spent:
    /// the reset is the free alternative to buying, and on any other paywall,
    /// or with a scan left, it would answer a question nobody asked.
    static func showsFreeScanReturn(for trigger: PaywallTrigger, remaining: Int) -> Bool {
        remaining == 0 && (trigger == .scanLimit || trigger == .upgradeButton)
    }

    /// "🔥 5-day streak · Next free scan at 8:00 PM" — the streak from two
    /// days on, as the Scan tab shows it. Stated, not promised: buying does
    /// not extend a streak, and a scan tonight may fall on a day it already
    /// counts, so nothing here says what a purchase or a return would do to it.
    static func freeScanReturn(resetsAt: Date, streak: Int) -> String {
        let reset = String(localized: "Next free scan at \(FreeScanCounter.resetClockTime(resetsAt))")
        guard streak >= 2 else { return reset }
        return [String(localized: "🔥 \(streak)-day streak"), reset].joined(separator: " · ")
    }

    /// What "Unlimited scans" means, said where it is sold. The server caps
    /// each Pro device per hour (`ratelimit.PRO_SCAN_RATE_MAX_REQUESTS`) and
    /// a 429's wait is what is left of that hour, so "up to an hour" is the
    /// most it can be. No number: the cap is server configuration, and the
    /// per-address cap drafts share can stop scanning sooner.
    static let fairUse = String(localized: "Unlimited scans are subject to fair use: very heavy use can pause scanning for up to an hour.")
}

// MARK: - Subscription not recognised

extension View {
    /// What a subscriber sees when the server will not honour their
    /// subscription even after it was re-sent: not the paywall, whose
    /// "Subscribe Yearly" would sell them the plan they pay for and could
    /// start a crossgrade. See `PurchaseService.confirmingSubscription`.
    ///
    /// Restore runs `AppStore.sync()`, sends the subscription to the server
    /// and waits for its answer, then says what it found — see
    /// `SubscriptionRestore`. Support opens a mail carrying the device's
    /// support id, which is what lets the operator look it up.
    func subscriptionUnconfirmedAlert(isPresented: Binding<Bool>,
                                      purchaseService: any PurchaseService) -> some View {
        modifier(SubscriptionUnconfirmedAlert(isPresented: isPresented,
                                              purchaseService: purchaseService))
    }
}

private struct SubscriptionUnconfirmedAlert: ViewModifier {
    @Binding var isPresented: Bool
    let purchaseService: any PurchaseService
    /// What Restore found, shown once it is known.
    @State private var notice: SubscriptionRestore.Notice?

    func body(content: Content) -> some View {
        content
            .alert("We couldn't confirm your subscription", isPresented: $isPresented) {
                Button("Restore purchase") {
                    Task {
                        switch await SubscriptionRestore.run(purchaseService) {
                        case .cancelled:        break
                        case .stillUnconfirmed: isPresented = true
                        case .notice(let found): notice = found
                        }
                    }
                }
                Button("Contact support") {
                    // English on purpose, like the feedback form's: support sorts
                    // mail on the subject line.
                    if let url = SupportMail.composeURL(subject: "SnapWorth Subscription not recognised",
                                                        body: "\n\n\(SupportMail.diagnostics)") {
                        UIApplication.shared.open(url)
                    }
                }
                Button("OK", role: .cancel) {}
            } message: {
                Text(AppError.subscriptionUnconfirmed.errorDescription ?? "")
            }
            .alert(notice?.title ?? "",
                   isPresented: Binding(get: { notice != nil },
                                        set: { if !$0 { notice = nil } }),
                   presenting: notice) { _ in
                Button("OK", role: .cancel) {}
            } message: { found in
                Text(found.message)
            }
    }
}

/// What the unconfirmed-subscription alert's Restore established.
///
/// It was `Task { try? await restorePurchases() }`. The alert closed on the
/// tap, the server push behind a restore is detached, and `isSubscribed` was
/// already true — which is why the alert was up — so nothing on screen
/// changed whether the restore worked, failed or was never going to: the
/// "dead button" App Review cites under Guideline 2.1, on the one alert that
/// appears right after a sync has failed. Now the server's answer is awaited
/// and reported.
enum SubscriptionRestore {
    enum Outcome: Equatable {
        /// Apple's sign-in was dismissed: the user's decision, not a result.
        case cancelled
        /// The server still will not honour it: the same alert again, with
        /// its way to support.
        case stillUnconfirmed
        case notice(Notice)
    }

    struct Notice: Equatable {
        let title: String
        let message: String
    }

    @MainActor
    static func run(_ service: any PurchaseService) async -> Outcome {
        do {
            try await service.restorePurchases()
        } catch {
            let appError = AppError.from(error)
            guard appError != .purchaseCancelled else { return .cancelled }
            return .notice(Notice(title: String(localized: "Restore purchases"),
                                  message: appError.errorDescription ?? ""))
        }
        switch await service.resyncEntitlement() {
        case .confirmed:
            // The refused request is not re-run from here: the alert cannot
            // tell which of a screen's requests it was, and a tag re-read's
            // label photo is already gone.
            return .notice(Notice(
                title: String(localized: "Subscription confirmed"),
                message: String(localized: "SnapWorth recognizes your subscription now. Please try again.")))
        case .notSubscribed:
            // StoreKit no longer shows one either, so the next refusal goes
            // to the paywall, which is then the right answer.
            return .notice(Notice(title: String(localized: "Restore purchases"),
                                  message: String(localized: "No active subscription found on this Apple ID.")))
        case .unreachable(let reason, let error):
            Analytics.shared.track(.entitlementSyncFailed(reason: reason))
            return .notice(Notice(title: String(localized: "We couldn't confirm your subscription"),
                                  message: error.errorDescription ?? ""))
        case .failed(let reason):
            Analytics.shared.track(.entitlementSyncFailed(reason: reason))
            return .stillUnconfirmed
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
