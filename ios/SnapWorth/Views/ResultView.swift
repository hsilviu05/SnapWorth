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

    private enum Field { case paid, sold, fees, guess }

    // ── Guess before the estimate ─────────────────────────────────────────────
    @AppStorage(GuessFirst.key) private var guessFirst = GuessFirst.defaultOn
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.modelContext) private var modelContext
    /// Per result: a fresh sheet starts covered when the preference is on.
    @State private var priceRevealed = false
    @State private var quickGuessText = ""
    /// The range the guess was scored against, captured at the reveal.
    ///
    /// The estimate goes on moving afterwards — the condition chips re-price
    /// it, the tag re-read replaces it outright — but the guess was entered
    /// once, against the number as it stood then, and the field it was typed
    /// into goes away with the cover. Scoring the live range meant a condition
    /// correction silently re-graded a verdict the user had already been given
    /// and could no longer answer: "spot on" could become "$12 under the low
    /// end" because they told the app the jacket was more worn than it looked.
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
                        heroPhoto(width: geo.size.width)

                        valueCard
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
                            // `.offset(y: -28)`, copied from `valueCard`'s
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
                            // follows `valueCard`, whose own -28 offset leaves
                            // 28pt of phantom space; -8 there gives the same
                            // 20pt gap that 12 gives against `addTagCard`'s
                            // 8pt bottom padding in the revealed branch.
                            .padding(.top, priceCovered ? -8 : 12)

                        paidPriceCard
                            .padding(.horizontal, 20)
                            .padding(.top, 12)

                        flipStatusCard
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
                                listingDraftCard
                                    .padding(.horizontal, 20)
                                    .padding(.top, 12)
                            }

                            snapSellCard
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

    // MARK: - Paid Price Card

    private var paidPriceCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("What did you pay?")
                .snapSectionHeader()
            HStack(spacing: 4) {
                Text("$")
                    .font(.dmSans(17, weight: .medium))
                    .foregroundStyle(Color.snapWarmGray)
                    .accessibilityHidden(true)
                TextField("0", text: $paidPriceText)
                    .keyboardType(.decimalPad)
                    .font(.dmSans(17, weight: .medium))
                    .foregroundStyle(Color.snapEspresso)
                    .focused($focusedField, equals: .paid)
                    .accessibilityLabel("What did you pay?")
                    .accessibilityValue(paidPriceText.isEmpty
                        ? String(localized: "Not set")
                        : String(localized: "\(paidPriceText) dollars"))
                    .accessibilityHint("Adds your find multiple to the share card")
            }
            Text("Adds your find multiple to the share card")
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)
                // Already spoken as the field's hint.
                .accessibilityHidden(true)
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .shadow(color: Color.snapCardShadow.opacity(0.08), radius: 24, x: 0, y: 8)
    }

    // MARK: - Flip Status Card

    private var flipStatusCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Flip status")
                .snapSectionHeader()

            HStack(spacing: 8) {
                ForEach(FlipStatus.allCases) { status in
                    statusChip(status)
                }
            }
            .accessibilityElement(children: .contain)
            .accessibilityLabel("Flip status")
            .accessibilityValue(result.status.label)

            if result.status == .sold {
                soldFields
                Divider()
                profitRow
            }
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .shadow(color: Color.snapCardShadow.opacity(0.08), radius: 24, x: 0, y: 8)
    }

    private func statusChip(_ status: FlipStatus) -> some View {
        let selected = result.status == status
        return Button {
            setStatus(status)
            // `String(localized:)`: `argument` is `Any?`, so a bare literal
            // here is announced verbatim — "Status" in English inside every
            // translated label.
            UIAccessibility.post(notification: .announcement,
                                 argument: String(localized: "Status \(status.label)"))
        } label: {
            Text(status.label)
                .font(.dmSans(13, weight: .semibold))
                .foregroundStyle(selected ? Color.snapOnAccent : Color.snapWarmGray)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 9)
                .background(selected ? Color.snapTerracottaFill : Color.clear)
                .clipShape(Capsule())
                .overlay(Capsule().strokeBorder(
                    selected ? Color.snapTerracotta : Color.snapBorder,
                    lineWidth: selected ? 2 : 1))
        }
        .buttonStyle(.plain)
        .snapHitTarget()
        .accessibilityLabel(status.label)
        .accessibilityHint(String(localized: "Marks this find as \(status.label.lowercased())"))
        .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
    }

    @ViewBuilder
    private var soldFields: some View {
        moneyRow(title: "Sold for", text: $soldPriceText, field: .sold)
        moneyRow(title: "Fees (optional)", text: $feesText, field: .fees)
        DatePicker("Sold date", selection: soldDateBinding, in: ...Date(), displayedComponents: .date)
            .font(.dmSans(14, weight: .medium))
            .foregroundStyle(Color.snapEspresso)
            .tint(Color.snapTerracotta)
    }

    private func moneyRow(title: LocalizedStringKey, text: Binding<String>, field: Field) -> some View {
        HStack {
            Text(title)
                .font(.dmSans(14, weight: .medium))
                .foregroundStyle(Color.snapWarmGray)
                // The label is carried by the field below; reading it twice is
                // noise for VoiceOver.
                .accessibilityHidden(true)
            Spacer()
            Text("$")
                .foregroundStyle(Color.snapWarmGray)
                .accessibilityHidden(true)
            TextField("0", text: text)
                .keyboardType(.decimalPad)
                .multilineTextAlignment(.trailing)
                .frame(minWidth: 90)
                .focused($focusedField, equals: field)
                .font(.dmSans(15, weight: .semibold))
                .foregroundStyle(Color.snapEspresso)
                .accessibilityLabel(title)
                .accessibilityValue(text.wrappedValue.isEmpty
                    ? String(localized: "Not set")
                    : String(localized: "\(text.wrappedValue) dollars"))
                .accessibilityHint("Enter an amount in dollars")
        }
    }

    private var profitRow: some View {
        HStack {
            Text("Profit")
                .font(.dmSans(15, weight: .semibold))
                .foregroundStyle(Color.snapEspresso)
            Spacer()
            if let profit = result.realizedProfit {
                // Sign and an explicit arrow carry the outcome, so profit/loss
                // is distinguishable without relying on green vs terracotta.
                Label {
                    Text(Self.signedProfit(profit))
                } icon: {
                    Image(systemName: profit < 0 ? "arrow.down.right" : "arrow.up.right")
                        .snapSymbol(13, weight: .bold)
                }
                .labelStyle(.titleAndIcon)
                .font(.dmSans(17, weight: .bold))
                .foregroundStyle(profit < 0 ? Color.snapTerracottaText : Color.snapSageText)
            } else {
                // Sold but no cost basis → profit unknown; never guessed.
                Text("—")
                    .font(.dmSans(17, weight: .bold))
                    .foregroundStyle(Color.snapWarmGray)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Profit")
        .accessibilityValue(profitAccessibilityValue)
    }

    /// The result's confidence as a phrase, for the two places that speak it.
    private var confidencePhrase: String { snapConfidencePhrase(result.confidence) }

    /// The chosen grade, worded for the locked listing teaser.
    private var conditionPhrase: String { result.condition.displayPhrase }

    private var profitAccessibilityValue: String {
        guard let profit = result.realizedProfit else {
            return String(localized: "Unknown — add what you paid to calculate it")
        }
        let amount = Self.signedProfit(profit)
        return profit < 0
            ? String(localized: "Loss of \(amount)")
            : String(localized: "Profit of \(amount)")
    }

    private var soldDateBinding: Binding<Date> {
        Binding(
            get: { result.soldDate ?? Date() },
            set: { result.soldDate = $0 }
        )
    }

    private func setStatus(_ status: FlipStatus) {
        Haptics.selection()
        let previous = result.status
        result.status = status

        switch status {
        case .sold:
            if result.soldDate == nil { result.soldDate = Date() }
            if previous != .sold { Analytics.shared.track(.ledgerItemMarkedSold) }
            // No longer needs a "did it sell?" nudge.
            let id = result.id
            Task { await NotificationManager.shared.cancelLedgerFollowUp(itemID: id) }
        case .listed:
            if previous != .listed { Analytics.shared.track(.ledgerItemMarkedListed) }
            // Coming back to Listed restarts the clock. Keeping the original
            // date puts the fire date 14 days after the *first* listing —
            // already in the past for anything listed over two weeks ago — and
            // a past-dated request is dropped silently, so a relisted item
            // never got the "did it sell?" nudge that is the whole point of
            // the status. Re-tapping Listed while already Listed is left
            // alone; only a real re-entry reseeds the date.
            if previous != .listed || result.listedDate == nil { result.listedDate = Date() }
            let (id, name, listed) = (result.id, result.itemName, result.listedDate ?? Date())
            Task { await NotificationManager.shared.scheduleLedgerFollowUp(itemID: id, itemName: name, from: listed) }
        default:
            // Moved back to scanned/owned — drop any pending follow-up.
            if previous == .listed {
                let id = result.id
                Task { await NotificationManager.shared.cancelLedgerFollowUp(itemID: id) }
            }
        }

        // Outside the switch: every branch moved `status`, and two of them also
        // moved `soldDate`, which is the other half of the widget's month
        // filter.
        ledgerDidChange()
    }

    private static func signedProfit(_ d: Decimal) -> String {
        let money = NumberFormatter.snapCurrency.string(from: NSDecimalNumber(decimal: abs(d))) ?? "$0"
        return d < 0 ? "−\(money)" : "+\(money)"
    }

    // MARK: - Hero Photo

    private func heroPhoto(width: CGFloat) -> some View {
        ZStack(alignment: .bottomLeading) {
            Group {
                if let img = photo {
                    Image(uiImage: img)
                        .resizable()
                        .scaledToFill()
                        .frame(width: width, height: 360)
                        .clipped()
                } else {
                    Rectangle()
                        .fill(Color.snapBorder)
                        .frame(width: width, height: 360)
                        .overlay(
                            Image(systemName: "photo")
                                .snapSymbol(48)
                                .foregroundStyle(Color.snapWarmGray)
                        )
                }
            }

            LinearGradient(
                stops: [
                    .init(color: .clear, location: 0.4),
                    .init(color: Color.black.opacity(0.65), location: 1.0)
                ],
                startPoint: .top,
                endPoint: .bottom
            )
            .frame(width: width, height: 360)

            VStack(alignment: .leading, spacing: 8) {
                Text(result.itemName)
                    .font(.fraunces(24, weight: .bold))
                    .foregroundStyle(.white)
                    .shadow(color: .black.opacity(0.3), radius: 4, x: 0, y: 2)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(width: max(0, width - 40), alignment: .leading)

                HStack(spacing: 8) {
                    if !result.brand.isEmpty && result.brand != "Unknown" {
                        photoChip(result.brand)
                    }
                    // The grade itself, as the Condition chips below word it.
                    // This used to be cut out of `conditionNotes` — split on
                    // dashes and full stops, first 22 characters — which fit
                    // v1's "Good — light pilling" notes. v2 writes prose, so
                    // the chip read "Well", "Pre" or "Moderate fading throug",
                    // in English in every language, and could contradict the
                    // grade the user had picked. The notes stay whole in the
                    // Condition card further down.
                    photoChip(result.condition.label)
                }
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 44)
            .frame(width: width, alignment: .leading)
        }
        .frame(width: width, height: 360)
        .ignoresSafeArea(edges: .top)
        // The photo is decoration; the item name and its chips are the content.
        // Combining them gives one clear stop instead of an image plus three
        // orphaned fragments.
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(heroAccessibilityLabel)
        .accessibilityAddTraits(.isHeader)
        .accessibilitySortPriority(90)
    }

    /// What the hero's chips say, spoken — so the grade, as on the chip. The
    /// full notes are read in the Condition card below.
    private var heroAccessibilityLabel: String {
        var parts = [result.itemName]
        if !result.brand.isEmpty, result.brand != "Unknown" {
            parts.append(String(localized: "Brand \(result.brand)"))
        }
        parts.append(String(localized: "Condition \(result.condition.label)"))
        return parts.joined(separator: ". ")
    }

    private func photoChip(_ label: String) -> some View {
        Text(label)
            .font(.snapLabel)
            .foregroundStyle(.white)
            .lineLimit(1)
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(Color.white.opacity(0.2))
            .background(.ultraThinMaterial)
            .clipShape(Capsule())
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

    // MARK: - Value Card

    /// Whether the value is still under its cover.
    private var priceCovered: Bool { coverPrice && guessFirst && !priceRevealed }

    private var quickGuess: Double? { GuessScoring.parse(quickGuessText) }

    private var quickVerdict: String? {
        guard priceRevealed, let quickGuess else { return nil }
        // Scored against the range as it stood at the reveal — see
        // `revealedRange`. The fallback covers the reveal itself, where the
        // frozen range and the live one are the same number anyway.
        let scored = revealedRange
            ?? (low: result.displayValueLow, high: result.displayValueHigh)
        return GuessScoring.verdict(guess: quickGuess, low: scored.low,
                                    high: scored.high)
    }

    @ViewBuilder
    private var valueCard: some View {
        if priceCovered {
            coveredValueCard
        } else {
            revealedValueCard
        }
    }

    /// The moment before the number. The range is under a solid cover with an
    /// optional guess; one tap on Reveal springs it in with a haptic.
    private var coveredValueCard: some View {
        VStack(spacing: 14) {
            Text("What do you think it could resell for?")
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)

            ZStack {
                ValueRangeView(low: result.displayValueLow, high: result.displayValueHigh)
                    .blur(radius: 18)
                    .opacity(0.25)
                    .accessibilityHidden(true)
                Text("$ ? ? ?")
                    .font(.fraunces(34, weight: .bold, relativeTo: .largeTitle))
                    .foregroundStyle(Color.snapWarmGray)
                    .accessibilityHidden(true)
            }
            .frame(maxWidth: .infinity)

            HStack(spacing: 6) {
                Text("$")
                    .font(.dmSans(17, weight: .medium))
                    .foregroundStyle(Color.snapWarmGray)
                    .accessibilityHidden(true)
                TextField("Your guess (optional)", text: $quickGuessText)
                    .keyboardType(.decimalPad)
                    .font(.dmSans(17, weight: .medium))
                    .foregroundStyle(Color.snapEspresso)
                    .focused($focusedField, equals: .guess)
                    .accessibilityLabel("Your guess in dollars, optional")
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(Color.snapBackground)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))

            PrimaryButton(title: "Reveal the estimate") { revealPrice() }
        }
        .padding(20)
        .frame(maxWidth: .infinity)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .shadow(color: Color.snapCardShadow.opacity(0.12), radius: 24, x: 0, y: 8)
        .accessibilitySortPriority(100)
    }

    private var revealedValueCard: some View {
        VStack(spacing: 16) {
            VStack(spacing: 6) {
                Text("Estimated Resale Value")
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)

                ValueRangeView(low: result.displayValueLow, high: result.displayValueHigh)
            }

            if let quickVerdict {
                Text(quickVerdict)
                    .font(.dmSans(15, weight: .semibold))
                    .foregroundStyle(Color.snapEspresso)
                    .multilineTextAlignment(.center)
                    .transition(.opacity)
            }

            Divider()

            HStack(spacing: 10) {
                ConfidenceBadge(confidence: result.confidence)

                // "AI estimate" unless real sales backed the number (#40).
                Text(result.valuationSource.caption)
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)
                    .lineLimit(1)

                Spacer()
            }
        }
        .padding(20)
        .frame(maxWidth: .infinity)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .shadow(color: Color.snapCardShadow.opacity(0.12), radius: 24, x: 0, y: 8)
        // The headline result: one VoiceOver stop that states the value, its
        // confidence, and that it's an estimate — rather than four fragments.
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Estimated resale value")
        .accessibilityValue(
            result.valuationSource.spokenSummary(
                range: result.formattedRange, confidence: confidencePhrase)
            + (quickVerdict.map { " \($0)" } ?? "")
        )
        // Read first when the sheet opens — it is why the user is here.
        .accessibilitySortPriority(100)
        .accessibilityAddTraits(.isSummaryElement)
    }

    // `@MainActor` explicitly: this mutates view state, runs an animation and
    // posts an accessibility announcement, and the SDK's isolation on
    // `UIAccessibility.post` has moved between Xcode versions. The only caller
    // is the reveal button's action, formed in `body`, so it is already on the
    // main actor — the annotation just says so where the compiler can check it.
    @MainActor
    private func revealPrice() {
        guard !priceRevealed else { return }
        focusedField = nil
        // Freeze what the verdict is scored against before the range is free
        // to move again — see `revealedRange`.
        revealedRange = (low: result.displayValueLow, high: result.displayValueHigh)
        withAnimation(reduceMotion ? .easeInOut(duration: 0.2)
                                   : .spring(response: 0.45, dampingFraction: 0.62)) {
            priceRevealed = true
        }
        Haptics.success()
        // The number the whole flow exists for, spoken.
        //
        // The button the user just activated lives inside the card that
        // disappears, so VoiceOver focus is destroyed and nothing is
        // announced. `.isSummaryElement` and `.accessibilitySortPriority`
        // above affect ordering and screen summaries, not announcements — the
        // card reads correctly if you navigate to it, and a VoiceOver user is
        // given no reason to think there is anything to navigate to.
        //
        // `GuessFirst.defaultOn` is true, so this is the default path on every
        // fresh scan: a VoiceOver user meets it on their first result. Every
        // other state change in this file already announces — the condition
        // chip, the status chip, and the tag re-read, that last one added
        // because "a haptic is the whole of the feedback... and said nothing
        // at all to VoiceOver". The same wording as the card's own
        // `accessibilityValue`, so the announcement and the element agree.
        UIAccessibility.post(
            notification: .announcement,
            argument: result.valuationSource.revealAnnouncement(
                range: result.formattedRange, confidence: confidencePhrase)
                + (quickVerdict.map { " \($0)" } ?? "")
        )
        Analytics.shared.track(.guessRevealed(withGuess: quickGuess != nil))
    }

    // MARK: - Add the tag (#88)

    /// The second photo. Identification is what the estimate rests on, and the
    /// tag is the biggest single lever on it — which is why "Sharpen this
    /// estimate" above so often says "photograph the tag" and, until now, gave
    /// the user nowhere to put it.
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
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text("Sharpen this estimate")
                        .snapSectionHeader()
                    Spacer()
                    if !isPro { proBadge }
                }
                Text("Photograph the care tag, size label or sole stamp and we'll re-read the item with both photos.")
                    .font(.snapBody)
                    .foregroundStyle(Color.snapWarmGray)
                    .fixedSize(horizontal: false, vertical: true)

                if let tagError = vm.tagError {
                    Text(tagError)
                        .font(.snapCaption)
                        .foregroundStyle(Color.snapTerracottaText)
                        .fixedSize(horizontal: false, vertical: true)
                }

                if let tagSuccess = vm.tagSuccess {
                    Label(tagSuccess, systemImage: "checkmark.circle.fill")
                        .font(.snapCaption)
                        .foregroundStyle(Color.snapSageText)
                        .fixedSize(horizontal: false, vertical: true)
                }

                PrimaryButton(title: vm.isRescanning ? "Re-reading…" : "Add the tag") {
                    guard !vm.isRescanning else { return }
                    if isPro {
                        vm.tagError = nil
                        showTagCamera = true
                    } else {
                        paywallTrigger = .addTag
                        showPaywall = true
                    }
                }
                .disabled(vm.isRescanning)
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.snapCard)
            .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
            .shadow(color: Color.snapCardShadow.opacity(0.08), radius: 24, x: 0, y: 8)
        }
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

    /// The panel the backend has been paying for since July. Pro sees it all;
    /// free sees the header, a blurred first line, and the way in.
    @ViewBuilder
    private var whyThisPriceCard: some View {
        if let detail = result.valuationDetail {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Text("Why this price")
                        .snapSectionHeader()
                    Spacer()
                    if !isPro { proBadge }
                }
                if isPro {
                    ValuationDetailView(detail: detail,
                                        priceFactor: result.conditionPriceFactor,
                                        gradeWasOverridden: result.conditionWasOverridden)
                    switch fullDetailOffer {
                    case .reread:           fullDetailPrompt
                    case .scannedBeforePro: scannedBeforeProNote
                    // `.teaserNewScansOnly` is a free user's; it never gets here.
                    case .none, .teaserNewScansOnly:
                        EmptyView()
                    }
                } else {
                    lockedDetailTeaser(detail,
                                       newScansOnly: fullDetailOffer == .teaserNewScansOnly)
                }
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.snapCard)
            .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
            .shadow(color: Color.snapCardShadow.opacity(0.08), radius: 24, x: 0, y: 8)
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
    /// summary it was saved with" (`lockedDetailTeaser`). A headline that
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

    /// For a subscriber looking at a fresh result that was saved with only
    /// the free part of the panel. Worded without claiming *why* it is thin,
    /// because the blob cannot say; in practice it is a scan made before Pro.
    ///
    /// It names everything a re-read replaces (`applySharpened`). It said only
    /// that "the estimate may change", and a tap also renames the item and
    /// rewrites its details and listing draft.
    private var fullDetailPrompt: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(FullDetailOffer.rereadPrompt)
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)
                .fixedSize(horizontal: false, vertical: true)
            if let fullDetailError = vm.fullDetailError {
                Text(fullDetailError)
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapTerracottaText)
                    .fixedSize(horizontal: false, vertical: true)
            }
            PrimaryButton(title: vm.isRescanning ? "Re-reading…" : "Show the full breakdown") {
                rereadForFullDetail()
            }
            .disabled(vm.isRescanning)
        }
    }

    /// For a subscriber reopening a thin find from My Finds or My Flips,
    /// which is not re-read (`FullDetailOffer`). Without it the panel is a
    /// score, a sentence and a grade, and nothing says that is not all Pro
    /// has to show.
    ///
    /// "Scanned before Pro" names the usual cause. The blob cannot prove it:
    /// a scan the server answered as free while the device was already Pro is
    /// thin too. That is a scan in the moments after a purchase, before the
    /// server has been told, and "before Pro" is how the server saw it.
    private var scannedBeforeProNote: some View {
        VStack(alignment: .leading, spacing: 4) {
            Label("Scanned before Pro", systemImage: "clock")
                .font(.dmSans(13, weight: .semibold))
                .foregroundStyle(Color.snapEspresso)
            Text("The full breakdown is only available for new scans. This find keeps the summary it was saved with.")
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
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

    /// What a free user sees in place of the panel: a blurred first line, and
    /// the way in.
    ///
    /// `newScansOnly` is a thin find reopened from My Finds or My Flips
    /// (`FullDetailOffer.teaserNewScansOnly`). Buying does not bring that
    /// find's breakdown: it is not re-read, and the panel then says "Scanned
    /// before Pro". This teaser used to offer to "Unlock why this price" there
    /// too, above four price points and what drives the value, and a purchase
    /// from it delivered the label instead. So the button there sells Pro
    /// rather than this find's panel, and the caption says the breakdown comes
    /// with new scans and this find keeps its summary, as the label will say
    /// once they have bought. The paywall it opens keeps to that too
    /// (`paywallPitch(for:)`).
    private func lockedDetailTeaser(_ detail: ValuationDetail, newScansOnly: Bool) -> some View {
        ZStack {
            VStack(alignment: .leading, spacing: 6) {
                Text(detail.confidenceSummary
                     ?? String(localized: "Confidence \(detail.confidenceScore ?? 0) out of 100"))
                    .font(.dmSans(15, weight: .semibold))
                    .foregroundStyle(Color.snapEspresso)
                    .lineLimit(1)
                Text(detail.valueDrivers.first
                     ?? detail.improveEstimate.first
                     ?? String(localized: "Quick-sale, expected and best-case prices, and what moves them…"))
                    .font(.snapBody)
                    .foregroundStyle(Color.snapWarmGray)
                    .lineLimit(2)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .blur(radius: 5)
            .accessibilityHidden(true)

            VStack(spacing: 10) {
                Image(systemName: "lock.fill")
                    .snapSymbol(18)
                    .foregroundStyle(Color.snapTerracottaText)
                PrimaryButton(title: newScansOnly ? "Upgrade to Pro" : "Unlock why this price") {
                    paywallTrigger = .valuationDetail
                    showPaywall = true
                }
                Text(FullDetailOffer.teaserCaption(newScansOnly: newScansOnly))
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
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

    // MARK: - Listing Draft Card

    private var listingDraftCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Listing Draft")
                .snapSectionHeader()

            if !result.listingTitle.isEmpty {
                Text(result.listingTitle)
                    .font(.dmSans(15, weight: .semibold))
                    .foregroundStyle(Color.snapEspresso)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

            if !result.listingDescription.isEmpty {
                Text(result.listingDescription)
                    .font(.snapBody)
                    .foregroundStyle(Color.snapWarmGray)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

            PrimaryButton(
                title: vm.didCopyListing ? "Copied!" : "Copy listing draft"
            ) {
                vm.copyListing(result: result)
            }
            .snapAnimation(.spring(duration: 0.2), value: vm.didCopyListing)
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .shadow(color: Color.snapCardShadow.opacity(0.08), radius: 24, x: 0, y: 8)
    }

    // MARK: - Snap → Sell Card

    /// Premium: a marketplace-tailored listing. Free users see a blurred teaser
    /// (soft paywall) so they reach the payoff before the wall. Generation is
    /// gated on `isPro`; the base valuation above stays free.
    private var snapSellCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("Snap → Sell")
                    .snapSectionHeader()
                Spacer()
                if !isPro { proBadge }
            }

            Text("A marketplace-ready listing, tailored to where you sell.")
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)

            marketplacePicker

            if isPro {
                proListingContent
                listingPhotoSection
            } else {
                lockedListingTeaser
            }

            // Honest-MVP limitation, also stated in code (see Marketplace.webSellURL):
            // we generate the text; posting stays a manual, user-controlled paste.
            Text("SnapWorth writes it — you paste & post. We never post for you.")
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .shadow(color: Color.snapCardShadow.opacity(0.08), radius: 24, x: 0, y: 8)
    }

    private var proBadge: some View {
        Text("PRO")
            .font(.dmSans(11, weight: .bold))
            .foregroundStyle(Color.snapOnAccent)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(Color.snapTerracottaFill)
            .clipShape(Capsule())
            .accessibilityLabel("Pro feature")
    }

    private var marketplacePicker: some View {
        // Seven marketplaces no longer fit as equal-width capsules on an
        // iPhone, so the row scrolls; US platforms come first (see `Marketplace`).
        ScrollView(.horizontal, showsIndicators: false) {
          HStack(spacing: 8) {
            ForEach(Marketplace.allCases) { marketplace in
                let selected = vm.selectedMarketplace == marketplace
                Button {
                    Haptics.selection()
                    vm.selectMarketplace(marketplace)
                } label: {
                    Text(marketplace.displayName)
                        .font(.dmSans(13, weight: .semibold))
                        .foregroundStyle(selected ? Color.snapOnAccent : Color.snapWarmGray)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 9)
                        .background(selected ? Color.snapTerracottaFill : Color.clear)
                        .clipShape(Capsule())
                        .overlay(Capsule().strokeBorder(
                            selected ? Color.snapTerracotta : Color.snapBorder,
                            lineWidth: selected ? 2 : 1))
                }
                .buttonStyle(.plain)
                .snapHitTarget()
                .accessibilityLabel(marketplace.displayName)
                .accessibilityHint(String(localized:
                    "Writes the listing in \(marketplace.displayName)'s style"))
                .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
            }
          }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Marketplace")
        .accessibilityValue(vm.selectedMarketplace.displayName)
    }

    @ViewBuilder
    private var proListingContent: some View {
        if let listing = vm.generatedListing {
            generatedListingView(listing)
        } else if let error = vm.listingError {
            VStack(spacing: 10) {
                Text(error)
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapTerracottaText)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: .infinity, alignment: .center)
                PrimaryButton(title: "Try again") {
                    Task { await vm.generateListing(result: result, purchaseService: purchaseService) }
                }
            }
        } else {
            PrimaryButton(
                title: vm.isGeneratingListing
                    ? LocalizedStringKey("Writing your listing…")
                    : LocalizedStringKey("Generate \(vm.selectedMarketplace.displayName) listing")
            ) {
                Task { await vm.generateListing(result: result, purchaseService: purchaseService) }
            }
            .disabled(vm.isGeneratingListing)
        }
    }

    private func generatedListingView(_ listing: GeneratedListing) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(listing.title)
                .font(.dmSans(15, weight: .semibold))
                .foregroundStyle(Color.snapEspresso)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)

            Text(listing.description)
                .font(.snapBody)
                .foregroundStyle(Color.snapWarmGray)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)

            HStack(spacing: 20) {
                priceTag("Ask", listing.listingPrice)
                priceTag("Floor", listing.negotiationFloor)
                Spacer()
            }

            HStack(spacing: 8) {
                secondaryButton(vm.didCopyGenerated ? "Copied!" : "Copy", icon: "doc.on.doc") {
                    vm.copyGeneratedListing()
                }
                secondaryButton("Share", icon: "square.and.arrow.up") {
                    vm.shareGeneratedListing()
                    showListingShare = true
                }
            }
            .snapAnimation(.spring(duration: 0.2), value: vm.didCopyGenerated)

            PrimaryButton(title: "Open \(listing.marketplace.displayName)") {
                vm.openMarketplace(listing.marketplace)
            }

            Button("Regenerate") {
                vm.generatedListing = nil
                Task { await vm.generateListing(result: result, purchaseService: purchaseService) }
            }
            .font(.dmSans(13, weight: .semibold))
            .foregroundStyle(Color.snapTerracottaText)
            .frame(maxWidth: .infinity, minHeight: 44)
            .contentShape(Rectangle())
            .accessibilityHint("Writes a new listing for this item")
        }
    }

    // MARK: - Listing photo (#91)

    /// "Clean up photo": the item lifted off the shop background, on-device,
    /// on a plain backdrop and shaped for the selected marketplace. An export
    /// only; the scan's own photo is never replaced.
    @ViewBuilder
    private var listingPhotoSection: some View {
        if let photo {
            VStack(alignment: .leading, spacing: 10) {
                Divider().padding(.vertical, 4)
                Text("Listing photo")
                    .snapSectionHeader()

                if let cleaned = vm.cleanedPhoto {
                    Image(uiImage: cleaned)
                        .resizable()
                        .scaledToFit()
                        .frame(maxWidth: .infinity)
                        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .strokeBorder(Color.snapBorder, lineWidth: 1))
                        .accessibilityLabel(Text("Cleaned-up listing photo"))

                    Text("Sized for \(vm.selectedMarketplace.displayName)")
                        .font(.snapCaption)
                        .foregroundStyle(Color.snapWarmGray)

                    Picker("Background", selection: Binding(
                        get: { vm.photoBackdrop },
                        set: { vm.photoBackdrop = $0 })) {
                        ForEach(ListingPhotoBackdrop.allCases) { backdrop in
                            Text(backdrop.label).tag(backdrop)
                        }
                    }
                    .pickerStyle(.segmented)

                    HStack(spacing: 8) {
                        secondaryButton(vm.didCopyPhoto ? "Copied!" : "Copy", icon: "doc.on.doc") {
                            vm.copyCleanedPhoto()
                        }
                        secondaryButton("Save to Photos", icon: "square.and.arrow.down") {
                            Task { await vm.saveCleanedPhoto() }
                        }
                    }
                    .snapAnimation(.spring(duration: 0.2), value: vm.didCopyPhoto)

                    if let message = vm.photoSaveMessage {
                        Text(message)
                            .font(.snapCaption)
                            .foregroundStyle(Color.snapWarmGray)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                } else {
                    secondaryButton(vm.isCleaningPhoto ? "Cleaning up…" : "Clean up photo",
                                    icon: "wand.and.stars") {
                        Task { await vm.cleanUpPhoto(photo) }
                    }
                    .disabled(vm.isCleaningPhoto)

                    if let note = vm.photoCleanupNote {
                        Text(note)
                            .font(.snapCaption)
                            .foregroundStyle(Color.snapTerracottaText)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    private func priceTag(_ label: LocalizedStringKey, _ value: Double) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)
            Text(NumberFormatter.snapCurrency.string(from: NSNumber(value: value)) ?? "$\(Int(value))")
                .font(.dmSans(17, weight: .bold))
                .foregroundStyle(Color.snapEspresso)
        }
    }

    private func secondaryButton(_ title: LocalizedStringKey, icon: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Image(systemName: icon)
                Text(title)
            }
            .font(.dmSans(14, weight: .semibold))
            .foregroundStyle(Color.snapEspresso)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 11)
            .background(Color.snapBackground)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(Color.snapBorder, lineWidth: 1))
        }
        .buttonStyle(.plain)
        // 11pt of padding around a 14pt label draws a pill just under 40pt
        // tall, and `.plain` makes that drawn pill the entire strike zone —
        // while `Open <marketplace>` directly below it is an explicit 44.
        //
        // The floor goes *outside* the label, so the pill keeps its exact
        // size, radius, border and fill; only the tappable area grows to the
        // 44pt the condition pills, Regenerate and PrimaryButton in this same
        // card already honour. The finding's own suggestion — padding 11 → 13
        // — would visibly fatten the pill instead, which is not a change to
        // make days before a release.
        .snapHitTarget()
    }

    private var lockedListingTeaser: some View {
        ZStack {
            VStack(alignment: .leading, spacing: 6) {
                Text(String(localized: "\(result.itemName) — \(conditionPhrase), ready to ship"))
                    .font(.dmSans(15, weight: .semibold))
                    .foregroundStyle(Color.snapEspresso)
                    .lineLimit(1)
                Text(String(localized: "A polished description tailored to \(vm.selectedMarketplace.displayName), priced to sell with a smart negotiation floor…"))
                    .font(.snapBody)
                    .foregroundStyle(Color.snapWarmGray)
                    .lineLimit(2)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .blur(radius: 5)
            .accessibilityHidden(true)

            VStack(spacing: 10) {
                Image(systemName: "lock.fill")
                    .snapSymbol(18)
                    .foregroundStyle(Color.snapTerracottaText)
                PrimaryButton(title: "Unlock marketplace listings") {
                    paywallTrigger = .snapSell
                    showPaywall = true
                }
            }
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
