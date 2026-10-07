import PhotosUI
import SwiftData
import SwiftUI

struct ResultView: View {
    let result: ScanResult
    let purchaseService: any PurchaseService
    var onDismiss: () -> Void
    /// Whether this result reached SwiftData. Defaults to true so call sites
    /// showing an already-persisted find (My Finds) are unaffected.
    var didSave: Bool = true
    /// Whether this is a valuation the user has just produced, rather than one
    /// they are re-opening from My Finds or the ledger.
    ///
    /// Defaults to false, following `didSave` above: this view serves all three
    /// call sites, and `scan_result_shown` is a funnel event. Left ungated it
    /// would fire every time somebody browsed their own library, and the
    /// first-run number it feeds would be worthless.
    ///
    /// Deliberately not `coverPrice`, which is true in exactly the same cases
    /// today. That one is a presentation choice — whether to play the guess
    /// moment — and the day someone decides a fresh scan should show its number
    /// straight away, the funnel would go quiet with nothing to say it had.
    /// One is what the screen does; this is what happened.
    ///
    /// It also decides whether this sheet may rewrite the valuation. Both
    /// re-reads, "Add the tag" and "Show the full breakdown", replace the
    /// estimate, the name and the listing draft, so both are offered on a
    /// fresh result only (`addTagCard`, `FullDetailOffer`). The tag re-read
    /// was gated on `coverPrice`, and would have gone from fresh results the
    /// day the cover did, for the same reason as the funnel.
    ///
    /// A rare find's full result is fresh too, and offers both. It does not
    /// send `scan_result_shown`, though: its reveal has already put this
    /// valuation on screen and reported it (see `RareFindRevealView.play()`),
    /// and a second event would count it twice — see `priceAlreadyShown`.
    var isFreshScan: Bool = false


    @State private var vm = ResultViewModel()
    @State private var photo: UIImage?
    @State private var paidPriceText: String
    @State private var soldPriceText: String
    @State private var feesText: String
    @FocusState private var focusedField: Field?
    @State private var showShareSheet = false
    @State private var showShareChoice = false
    @State private var showTagCamera = false
    /// What the share sheet carries: the result card, or the guess story pair.
    @State private var shareItems: [Any] = []
    @State private var showPaywall = false
    /// Which locked surface opened the paywall.
    ///
    /// One sheet serves every entry point here, and it used to hard-code a
    /// single trigger — so an impression from any other surface was attributed
    /// to that one, and so was every purchase that followed it. `PaywallView`
    /// already fires `paywallViewed` from its own `onAppear`, so the tracking
    /// call that used to sit in each button was a *second* event for the same
    /// open: the funnel counted every paywall twice.
    @State private var paywallTrigger: PaywallTrigger = .snapSell
    @State private var showListingShare = false

    /// Which money field has the keyboard. Internal: the ledger cards
    /// (`FlipLedgerSection.swift`) take a binding to the sheet's focus.
    enum Field { case paid, sold, fees, guess }

    // ── Guess before the estimate ─────────────────────────────────────────────
    @AppStorage(GuessFirst.key) private var guessFirst = GuessFirst.defaultOn
    @Environment(\.modelContext) private var modelContext
    /// Per result: a fresh sheet starts covered when the preference is on.
    @State private var priceRevealed = false
    @State private var quickGuessText = ""
    /// The range the guess was scored against, captured at the reveal — see
    /// `ResultValueCard.revealedRange`.
    @State private var revealedRange: (low: Double, high: Double)?

    private var isPro: Bool { purchaseService.isSubscribed }

    /// Only a *fresh* scan starts with its value covered. Reopening the same
    /// find from My Finds or My Flips shows the number straight away — the
    /// guess is a once-per-find moment, not a toll on every visit.
    private let coverPrice: Bool

    /// See `init`'s `priceAlreadyShown`.
    private let priceAlreadyShown: Bool

    /// - Parameter priceAlreadyShown: the user has seen this estimate before
    ///   this sheet opened — the rare-find reveal shows it first. The guess
    ///   cover then starts lifted, and `scan_result_shown`, which the screen
    ///   that showed it first has sent, is not sent again. Nothing else
    ///   `coverPrice` or `isFreshScan` decides changes: "Sharpen this
    ///   estimate", the tag re-read, the full breakdown and the rating request
    ///   are all still there.
    init(result: ScanResult,
         purchaseService: any PurchaseService,
         onDismiss: @escaping () -> Void,
         didSave: Bool = true,
         coverPrice: Bool = false,
         priceAlreadyShown: Bool = false,
         isFreshScan: Bool = false) {
        self.result = result
        self.purchaseService = purchaseService
        self.onDismiss = onDismiss
        self.didSave = didSave
        self.coverPrice = coverPrice
        self.isFreshScan = isFreshScan
        self.priceAlreadyShown = priceAlreadyShown
        _priceRevealed = State(initialValue: priceAlreadyShown)
        _paidPriceText = State(initialValue: Self.moneyField(result.paidPrice))
        _soldPriceText = State(initialValue: Self.moneyField(result.soldPrice))
        _feesText      = State(initialValue: Self.moneyField(result.feesEstimate))
    }

    /// Formats a stored amount for an editable field ("" when unset).
    private static func moneyField(_ value: Double?) -> String {
        guard let value else { return "" }
        let fmt = value.truncatingRemainder(dividingBy: 1) == 0 ? "%.0f" : "%.2f"
        return String(format: fmt, value)
    }

    var body: some View {
        NavigationStack {
            GeometryReader { geo in
                ScrollView {
                    VStack(spacing: 0) {
                        ResultHeroPhoto(result: result, photo: photo, width: geo.size.width)

                        ResultValueCard(result: result, covered: priceCovered,
                                        revealed: $priceRevealed, guessText: $quickGuessText,
                                        revealedRange: $revealedRange, focus: $focusedField)
                            .padding(.horizontal, 20)
                            .offset(y: -28)

                        // The ladder would give the covered number away.
                        if !priceCovered {
                            whyThisPriceCard
                                .padding(.horizontal, 20)
                                // The value card above is offset up by 28pt for
                                // the hero overlap; -12 here leaves a 16pt gap to
                                // it, and the bottom padding keeps Condition from
                                // sitting on this card's shadow.
                                .padding(.top, -12)
                                .padding(.bottom, 8)

                            addTagCard
                                .padding(.horizontal, 20)
                                .padding(.bottom, 8)
                        }

                        conditionCard
                            .padding(.horizontal, 20)
                            // Padding, not `offset`. This carried
                            // `.offset(y: -28)`, copied from `ResultValueCard`'s
                            // hero overlap above — but offset moves pixels and
                            // not the layout frame, so the VStack went on
                            // reserving the original slot: 28pt came off the
                            // gap above this card and was added to the gap
                            // below it. On screen that is a cramped join to
                            // "Sharpen this estimate" and a ~40pt hole before
                            // "What did you pay?".
                            //
                            // It cannot simply be dropped, because the value
                            // it was absorbing is real in one branch. With the
                            // price hidden, `whyThisPriceCard` and
                            // `addTagCard` are both absent and this card
                            // follows `ResultValueCard`, whose own -28 offset
                            // leaves 28pt of phantom space; -8 there gives the same
                            // 20pt gap that 12 gives against `addTagCard`'s
                            // 8pt bottom padding in the revealed branch.
                            .padding(.top, priceCovered ? -8 : 12)

                        PaidPriceCard(text: $paidPriceText, focus: $focusedField,
                                      currencyCode: SaleCurrency.of(result))
                            .padding(.horizontal, 20)
                            .padding(.top, 12)

                        FlipStatusCard(result: result, soldPriceText: $soldPriceText,
                                       feesText: $feesText, focus: $focusedField,
                                       onLedgerChange: ledgerDidChange)
                            .padding(.horizontal, 20)
                            .padding(.top, 12)

                        detailsCard
                            .padding(.horizontal, 20)
                            .padding(.top, 12)

                        // Both of these print the covered number by another
                        // route: "Copy listing draft" puts `Asking: $45–$90`
                        // on the clipboard, and a generated Snap → Sell
                        // listing shows Ask and Floor, both derived
                        // server-side from the same range. Leaving them up
                        // while the value card still reads "$ ? ? ?" ends the
                        // guess before Reveal is ever tapped, so they wait
                        // with the ladder — the same reasoning as the comment
                        // on `priceCovered` above.
                        if !priceCovered {
                            if !result.listingTitle.isEmpty || !result.listingDescription.isEmpty {
                                ListingDraftCard(result: result, vm: vm)
                                    .padding(.horizontal, 20)
                                    .padding(.top, 12)
                            }

                            SnapSellSection(result: result, vm: vm, isPro: isPro,
                                            purchaseService: purchaseService, photo: photo,
                                            onShareListing: { showListingShare = true },
                                            onPaywall: { trigger in
                                                paywallTrigger = trigger
                                                showPaywall = true
                                            })
                                .padding(.horizontal, 20)
                                .padding(.top, 12)
                        }

                        footer
                            .padding(.top, 20)
                            .padding(.bottom, 40)
                    }
                    // Push content under the hero photo which ignores safe area
                    .padding(.top, 0)
                }
                .scrollIndicators(.hidden)
                .scrollDismissesKeyboard(.interactively)
            }
            .background(Color.snapBackground)
            .ignoresSafeArea(edges: .top)
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(.hidden, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button {
                        guard vm.shareCard != nil else { return }
                        Analytics.shared.track(.shareCardOpened)
                        showShareChoice = true
                    } label: {
                        circleButton(icon: "square.and.arrow.up")
                    }
                    .disabled(vm.shareCard == nil)
                    .accessibilityLabel("Share")
                    .accessibilityHint(vm.shareCard == nil
                        ? String(localized: "Share card is still being prepared")
                        : String(localized: "Share this find as a card, or as a guess-the-price story"))
                    .confirmationDialog("Share", isPresented: $showShareChoice, titleVisibility: .hidden) {
                        Button("Result card") {
                            if let card = vm.shareCard {
                                shareItems = [card]
                                showShareSheet = true
                            }
                        }
                        Button("Guess-the-price story (question, then reveal)") {
                            let cards = vm.renderGuessCards(result: result, photo: photo)
                            shareItems = [cards.guess, cards.reveal].compactMap { $0 }
                            guard !shareItems.isEmpty else { return }
                            Analytics.shared.track(.guessCardShared(style: "pair"))
                            showShareSheet = true
                        }
                    }
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button(action: onDismiss) {
                        circleButton(icon: "xmark")
                    }
                    .accessibilityLabel("Done")
                    .accessibilityHint("Closes this result and returns to the camera")
                }
                // Keyboard toolbar must live in the SAME .toolbar block as the
                // nav items — a second, separate .toolbar can be dropped by
                // SwiftUI, leaving the decimal pad with no way to dismiss.
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") { focusedField = nil }
                        .font(.dmSans(15, weight: .semibold))
                        .foregroundStyle(Color.snapTerracottaText)
                }
            }
        }
        // Shared sale outcomes (#224): once, as the sheet closes, when the
        // user has opted in. Off, `sync` does nothing at all.
        .onDisappear {
            let result = result
            Task { await SaleSharing.shared.sync(result) }
        }
        .task(id: result.id) {
            // Keyed on `result.id`, so this is once per valuation shown rather
            // than once per redraw. `scan_completed` fires when the response
            // lands; persistence, image encoding and sheet presentation all sit
            // between that and the user actually seeing a number.
            //
            // `isFirstRun()` rather than `isFirstScan()`: the tally has
            // already been recorded by the time this view appears, so the first
            // valuation reads 1, not 0. Correct under either ordering.
            //
            // Not after a rare-find reveal, which sent it when it put this
            // valuation on screen (`priceAlreadyShown`).
            if reportsScanResultShown {
                Analytics.shared.track(.scanResultShown(isFirst: ScanTally.isFirstRun()))
            }
            if let data = result.imageData {
                photo = await Task.detached(priority: .userInitiated) {
                    UIImage(data: data)
                }.value
            }
            vm.prepareShareCard(result: result, photo: photo)
        }
        // The rating request waits for the number. Keyed on the cover, so it
        // runs when a fresh sheet opens uncovered (guess-first off) and again
        // at the reveal; a sheet dismissed inside the pause cancels it rather
        // than prompting over the camera.
        .task(id: priceCovered) {
            guard Self.asksForReview(isFreshScan: isFreshScan, priceCovered: priceCovered,
                                     confidence: result.confidence) else { return }
            try? await Task.sleep(for: .seconds(2))
            guard !Task.isCancelled else { return }
            ReviewPrompt.requestIfDue()
        }
        // `Double(newValue)` here discarded every comma-decimal amount — see
        // `MoneyInput`. Clearing the field still clears the stored value; a
        // half-typed or unparseable one now leaves the last good value alone
        // instead of writing nil on the way through.
        .onChange(of: paidPriceText) { _, newValue in
            if newValue.isEmpty { result.paidPrice = nil }
            else if let parsed = MoneyInput.parse(newValue) { result.paidPrice = parsed }
            vm.scheduleShareCardUpdate(result: result, photo: photo)
            ledgerDidChange()
        }
        .onChange(of: soldPriceText) { _, newValue in
            if newValue.isEmpty { result.soldPrice = nil }
            else if let parsed = MoneyInput.parse(newValue) { result.soldPrice = parsed }
            ledgerDidChange()
        }
        .onChange(of: feesText) { _, newValue in
            if newValue.isEmpty { result.feesEstimate = nil }
            else if let parsed = MoneyInput.parse(newValue) { result.feesEstimate = parsed }
            ledgerDidChange()
        }
        .fullScreenCover(isPresented: $showTagCamera) {
            TagCameraSheet { image in
                showTagCamera = false
                if let image { rescan(withTag: image) }
            }
        }
        .sheet(isPresented: $showShareSheet) {
            if !shareItems.isEmpty {
                ActivityShareSheet(items: shareItems) { activityType in
                    Analytics.shared.track(.shareCardShared(activityType: activityType))
                }
            }
        }
        .sheet(isPresented: $showListingShare) {
            if let items = vm.listingShareItems {
                ActivityShareSheet(items: items) { _ in
                    if let marketplace = vm.generatedListing?.marketplace {
                        Analytics.shared.track(.listingShared(marketplace: marketplace.rawValue))
                    }
                }
            }
        }
        .sheet(isPresented: $showPaywall, onDismiss: {
            // Bought from "Unlock why this price" on a fresh result: that is
            // what they paid to see, and this find was saved without it. A
            // find reopened from My Finds or My Flips is not re-read after a
            // purchase either; its teaser said so, and its panel says why
            // (`FullDetailOffer`).
            if ResultViewModel.rereadsAfterPaywall(trigger: paywallTrigger,
                                                   offer: fullDetailOffer) {
                rereadForFullDetail()
            }
        }) {
            PaywallView(purchaseService: purchaseService, trigger: paywallTrigger,
                        pitch: paywallPitch(for: paywallTrigger))
        }
        .subscriptionUnconfirmedAlert(isPresented: $vm.showSubscriptionUnconfirmed,
                                      purchaseService: purchaseService)
    }

    // MARK: - Condition Card

    /// Lets the user correct the AI's condition read. Re-scales the estimate
    /// (via `ScanResult.priceRange`) and feeds the listing generator + flip math.
    private var conditionCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Condition")
                .snapSectionHeader()

            HStack(spacing: 8) {
                ForEach(Condition.allCases) { condition in
                    conditionChip(condition)
                }
            }
            // Read as one control ("Condition, Like New") rather than four
            // unrelated buttons.
            .accessibilityElement(children: .contain)
            .accessibilityLabel("Condition")
            .accessibilityValue(result.condition.label)
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .shadow(color: Color.snapCardShadow.opacity(0.08), radius: 24, x: 0, y: 8)
    }

    private func conditionChip(_ condition: Condition) -> some View {
        let selected = result.condition == condition
        return Button {
            Haptics.selection()
            result.condition = condition
            valuationDidChange()
            // Selection re-prices the estimate; announce the new value so a
            // VoiceOver user learns the outcome without hunting for it.
            UIAccessibility.post(
                notification: .announcement,
                argument: priceCovered
                    ? condition.label
                    : String(localized: "\(condition.label). Estimate \(result.formattedRange)")
            )
        } label: {
            Text(condition.label)
                .font(.dmSans(13, weight: .semibold))
                .foregroundStyle(selected ? Color.snapOnAccent : Color.snapWarmGray)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 9)
                .background(selected ? Color.snapTerracottaFill : Color.clear)
                .clipShape(Capsule())
                // Selection is carried by a border weight as well as fill
                // colour, so it survives Differentiate Without Color.
                .overlay(Capsule().strokeBorder(
                    selected ? Color.snapTerracotta : Color.snapBorder,
                    lineWidth: selected ? 2 : 1))
        }
        .buttonStyle(.plain)
        .snapHitTarget()
        .accessibilityLabel(condition.label)
        .accessibilityHint("Re-prices the estimate for this condition")
        // `.isSelected` is what makes VoiceOver say "selected" — colour alone
        // conveys nothing to it.
        .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
    }

    private func circleButton(icon: String) -> some View {
        Image(systemName: icon)
            .snapSymbol(14, weight: .semibold)
            .foregroundStyle(Color.snapEspresso)
            // 36pt was below Apple's 44pt minimum touch target (HIG). The
            // visible circle stays 36 so the toolbar look is unchanged; the
            // tappable area is expanded around it.
            .frame(width: 36, height: 36)
            .background(.ultraThinMaterial)
            .clipShape(Circle())
            .snapHitTarget()
    }

    // MARK: - Guess cover

    /// Whether the value is still under its cover.
    private var priceCovered: Bool { coverPrice && guessFirst && !priceRevealed }

    // MARK: - Add the tag (#88)

    /// "Sharpen this estimate" (`AddTagCard`).
    ///
    /// Offered on a fresh result only (`isFreshScan` marks one): re-pricing a
    /// find from My Finds weeks later would rewrite a number the user has
    /// already acted on. The full-breakdown re-read keeps the same rule
    /// (`FullDetailOffer`). This was gated on `coverPrice`, which is true in
    /// the same cases today but says whether to play the guess moment, not
    /// whether the valuation is new — see `isFreshScan`.
    @ViewBuilder
    private var addTagCard: some View {
        if offersTagReread {
            AddTagCard(vm: vm, isPro: isPro,
                       onAddTag: { showTagCamera = true },
                       onPaywall: openPaywall)
        }
    }

    private func openPaywall(_ trigger: PaywallTrigger) {
        paywallTrigger = trigger
        showPaywall = true
    }

    /// Everything that has to follow a change to this item's valuation.
    ///
    /// There are exactly three ways a saved item's value moves without a row
    /// being inserted or deleted: the condition chips, the tag re-read, and
    /// the full-breakdown re-read (`rereadForFullDetail`). The chip did four of
    /// these things and the tag re-read did one, so a re-read that
    /// tripled an estimate left behind a Home Screen widget and a thrift-run
    /// Live Activity still totalling the old number, a cached share card that
    /// would post the old number, and a generated listing priced for it. The
    /// comment on the chip asserted a condition change was "the only way that
    /// moves" — the re-read was the second, and shipped later.
    ///
    /// One function rather than a second copy of the list: the next path that
    /// moves a value will have the same four obligations, and the way this went
    /// wrong was a list that had to be remembered. The model-layer half — the
    /// value history, the widgets, the Live Activity — is
    /// `ScanRepository.valuationDidChange`, so a path outside this screen
    /// makes the same call; what is left here is what only this screen holds.
    private func valuationDidChange() {
        ScanRepository(context: modelContext).valuationDidChange(result)
        // The share card is an eagerly rendered bitmap, so it holds the old
        // item name and the old range until something re-renders it.
        vm.scheduleShareCardUpdate(result: result, photo: photo)
        // A listing is written for one condition and one estimate. Keeping it
        // would show marketplace copy quoting a price the app no longer states
        // — the same reason `selectMarketplace` clears it.
        vm.generatedListing = nil
        vm.listingError = nil
    }

    /// Everything that has to follow a change to this item's ledger figures.
    ///
    /// `WidgetDataStore.writeHaul` computes the Pro widget's month-to-date line
    /// from status, sold date and realized profit — sold price less paid price
    /// less fees — and this sheet is the app's only ledger editor. Only
    /// `valuationDidChange` was resyncing, so a user who marked a find sold and
    /// typed what it went for was left with "This month: $0 from 0 flips" on
    /// the Home Screen until they happened to scan or delete something, which
    /// is the next thing that touches the widget.
    ///
    /// Deliberately *not* `valuationDidChange`: the estimate itself has not
    /// moved, so there is no new portfolio point to record — and throwing away
    /// a generated listing because the user typed a purchase price would
    /// destroy work they are in the middle of using.
    private func ledgerDidChange() {
        ScanRepository(context: modelContext).refreshWidget()
    }

    /// The tag re-read; the work and its rules are
    /// `ResultViewModel.rescanWithTag` (#229).
    private func rescan(withTag tagImage: UIImage) {
        Task {
            await vm.rescanWithTag(tagImage, photo: photo, result: result,
                                   purchaseService: purchaseService) {
                valuationDidChange()
                priceRevealed = true          // the user has seen the first number already
            }
        }
    }

    // MARK: - Why this price (#87)

    /// "Why this price" (`WhyThisPriceCard`), when the scan came with a panel.
    @ViewBuilder
    private var whyThisPriceCard: some View {
        if let detail = result.valuationDetail {
            WhyThisPriceCard(result: result, detail: detail, isPro: isPro,
                             offer: fullDetailOffer, vm: vm,
                             onReread: rereadForFullDetail,
                             onPaywall: openPaywall)
        }
    }

    /// What "Why this price" says about a thin panel, to a subscriber under it
    /// and to a free user in the teaser — see `FullDetailOffer`. Internal
    /// rather than private so a test can build this sheet the way each call
    /// site does and read the answer.
    var fullDetailOffer: FullDetailOffer {
        FullDetailOffer(isPro: isPro, isFreshScan: isFreshScan,
                        detail: result.valuationDetail)
    }

    /// Whether this sheet reports `scan_result_shown`: a fresh scan, whose
    /// price nothing has reported already (a rare find's reveal shows it and
    /// reports it first). Browsing My Finds or My Flips is not a scan.
    var reportsScanResultShown: Bool { isFreshScan && !priceAlreadyShown }

    /// The tag re-read is offered on a fresh result only, on the same signal
    /// as the full-breakdown re-read (`FullDetailOffer`), so the two cannot
    /// come apart the day either changes.
    var offersTagReread: Bool { isFreshScan }

    /// Whether to ask for a rating once this result is on screen: a fresh
    /// scan, after the price has been uncovered (never while a guess is
    /// still hiding it), and not on an estimate too weak to be pleased by.
    static func asksForReview(isFreshScan: Bool, priceCovered: Bool, confidence: String) -> Bool {
        isFreshScan && !priceCovered && ReviewPrompt.isWorthAskingAbout(confidence: confidence)
    }

    /// What the paywall opened from this result leads with.
    ///
    /// The trigger's pitch, except "See why this price" on a thin find
    /// reopened from My Finds or My Flips. Nothing re-reads that find, so
    /// buying brings "Scanned before Pro" and not its breakdown, and its
    /// teaser has just said so: "Upgrade to Pro", and "This find keeps the
    /// summary it was saved with" (`WhyThisPriceCard`). A headline that
    /// takes that back one tap later, on the screen that takes the money, is
    /// the claim the teaser was reworded to stop making. Its paywall leads
    /// with the offer instead, over the usual list, whose breakdown row
    /// describes what Pro does on any new scan.
    ///
    /// `.scannedBeforePro` too, because it is what the same find becomes once
    /// the purchase lands: the sheet is rebuilt while the paywall is still on
    /// screen, and the headline must not change to the promise on the way out.
    /// Only the copy changes; the trigger stays `.valuationDetail`, so the
    /// events still report the gate. Internal, like `fullDetailOffer`, so a
    /// test can build the sheet the way My Finds does and read the answer.
    func paywallPitch(for trigger: PaywallTrigger) -> PaywallCopy.Pitch? {
        guard trigger == .valuationDetail else { return PaywallCopy.pitch(for: trigger) }
        switch fullDetailOffer {
        case .none, .reread:
            return PaywallCopy.pitch(for: trigger)
        case .teaserNewScansOnly, .scannedBeforePro:
            return nil
        }
    }

    /// The full-breakdown re-read; the work and its rules are
    /// `ResultViewModel.rereadForFullDetail` (#229).
    private func rereadForFullDetail() {
        Task {
            await vm.rereadForFullDetail(offer: fullDetailOffer, photo: photo, result: result,
                                         purchaseService: purchaseService) {
                valuationDidChange()
                priceRevealed = true
            }
        }
    }

    // MARK: - Details Card

    @ViewBuilder
    private var detailsCard: some View {
        if !result.conditionNotes.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                Text("Condition")
                    .snapSectionHeader()
                Text(result.conditionNotes)
                    .font(.snapBody)
                    .foregroundStyle(Color.snapEspresso)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.snapCard)
            .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
            .shadow(color: Color.snapCardShadow.opacity(0.08), radius: 24, x: 0, y: 8)
        }
    }

    // MARK: - Footer

    private var footer: some View {
        VStack(spacing: 12) {
            HStack(spacing: 6) {
                Image(systemName: didSave
                      ? "checkmark.circle.fill"
                      : "exclamationmark.triangle.fill")
                    .foregroundStyle(didSave ? Color.snapSageText : Color.snapTerracottaText)
                Text(didSave
                     ? String(localized: "Saved to My Finds")
                     : String(localized: "Couldn't save to My Finds — this result won't be kept"))
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .accessibilityElement(children: .combine)

            Text("SnapWorth")
                .font(.fraunces(13, weight: .bold))
                .foregroundStyle(Color.snapWarmGray)
                .kerning(0.5)
        }
    }
}
