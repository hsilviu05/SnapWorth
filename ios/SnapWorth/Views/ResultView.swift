import SwiftUI

struct ResultView: View {
    let result: ScanResult
    let purchaseService: any PurchaseService
    var onDismiss: () -> Void
    /// Whether this result reached SwiftData. Defaults to true so call sites
    /// showing an already-persisted find (My Finds) are unaffected.
    var didSave: Bool = true

    @Environment(\.displayScale) private var displayScale

    @State private var vm = ResultViewModel()
    @State private var photo: UIImage?
    @State private var paidPriceText: String
    @State private var soldPriceText: String
    @State private var feesText: String
    @FocusState private var focusedField: Field?
    @State private var showShareSheet = false
    @State private var showPaywall = false
    @State private var showListingShare = false

    private enum Field { case paid, sold, fees }

    private var isPro: Bool { purchaseService.isSubscribed }

    init(result: ScanResult,
         purchaseService: any PurchaseService,
         onDismiss: @escaping () -> Void,
         didSave: Bool = true) {
        self.result = result
        self.purchaseService = purchaseService
        self.onDismiss = onDismiss
        self.didSave = didSave
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

                        conditionCard
                            .padding(.horizontal, 20)
                            .padding(.top, 12)
                            .offset(y: -28)

                        paidPriceCard
                            .padding(.horizontal, 20)
                            .padding(.top, 12)

                        flipStatusCard
                            .padding(.horizontal, 20)
                            .padding(.top, 12)

                        detailsCard
                            .padding(.horizontal, 20)
                            .padding(.top, 12)

                        if !result.listingTitle.isEmpty || !result.listingDescription.isEmpty {
                            listingDraftCard
                                .padding(.horizontal, 20)
                                .padding(.top, 12)
                        }

                        snapSellCard
                            .padding(.horizontal, 20)
                            .padding(.top, 12)

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
                        showShareSheet = true
                    } label: {
                        circleButton(icon: "square.and.arrow.up")
                    }
                    .disabled(vm.shareCard == nil)
                    .accessibilityLabel("Share")
                    .accessibilityHint(vm.shareCard == nil
                        ? "Share card is still being prepared"
                        : "Creates a shareable card for this find")
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
                        .foregroundStyle(Color.snapTerracotta)
                }
            }
        }
        .task(id: result.id) {
            if let data = result.imageData {
                photo = await Task.detached(priority: .userInitiated) {
                    UIImage(data: data)
                }.value
            }
            vm.prepareShareCard(result: result, photo: photo, displayScale: displayScale)
        }
        .onChange(of: paidPriceText) { _, newValue in
            result.paidPrice = newValue.isEmpty ? nil : Double(newValue)
            vm.scheduleShareCardUpdate(result: result, photo: photo, displayScale: displayScale)
        }
        .onChange(of: soldPriceText) { _, newValue in
            result.soldPrice = newValue.isEmpty ? nil : Double(newValue)
        }
        .onChange(of: feesText) { _, newValue in
            result.feesEstimate = newValue.isEmpty ? nil : Double(newValue)
        }
        .sheet(isPresented: $showShareSheet) {
            if let card = vm.shareCard {
                ActivityShareSheet(items: [card]) { activityType in
                    Analytics.shared.track(.shareCardShared(activityType: activityType))
                }
            }
        }
        .sheet(isPresented: $showListingShare) {
            if let items = vm.listingShareItems {
                ActivityShareSheet(items: items) { _ in }
            }
        }
        .sheet(isPresented: $showPaywall) {
            PaywallView(purchaseService: purchaseService, trigger: .snapSell)
        }
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
            vm.scheduleShareCardUpdate(result: result, photo: photo, displayScale: displayScale)
            // Selection re-prices the estimate; announce the new value so a
            // VoiceOver user learns the outcome without hunting for it.
            UIAccessibility.post(
                notification: .announcement,
                argument: "\(condition.label). Estimate \(result.formattedRange)"
            )
        } label: {
            Text(condition.label)
                .font(.dmSans(13, weight: .semibold))
                .foregroundStyle(selected ? Color.snapOnAccent : Color.snapWarmGray)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 9)
                .background(selected ? Color.snapTerracotta : Color.clear)
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
                        ? "Not set" : "\(paidPriceText) dollars")
                    .accessibilityHint("Adds your find multiple to the share card")
            }
            Text("Adds your find multiple to the share card")
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray.opacity(0.7))
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
            UIAccessibility.post(notification: .announcement,
                                 argument: "Status \(status.label)")
        } label: {
            Text(status.label)
                .font(.dmSans(13, weight: .semibold))
                .foregroundStyle(selected ? Color.snapOnAccent : Color.snapWarmGray)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 9)
                .background(selected ? Color.snapTerracotta : Color.clear)
                .clipShape(Capsule())
                .overlay(Capsule().strokeBorder(
                    selected ? Color.snapTerracotta : Color.snapBorder,
                    lineWidth: selected ? 2 : 1))
        }
        .buttonStyle(.plain)
        .snapHitTarget()
        .accessibilityLabel(status.label)
        .accessibilityHint("Marks this find as \(status.label.lowercased())")
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

    private func moneyRow(title: String, text: Binding<String>, field: Field) -> some View {
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
                    ? "Not set" : "\(text.wrappedValue) dollars")
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
                .foregroundStyle(profit < 0 ? Color.snapTerracotta : Color.snapSage)
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

    private var profitAccessibilityValue: String {
        guard let profit = result.realizedProfit else {
            return "Unknown — add what you paid to calculate it"
        }
        let amount = Self.signedProfit(profit)
        return profit < 0 ? "Loss of \(amount)" : "Profit of \(amount)"
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
            if result.listedDate == nil { result.listedDate = Date() }
            let (id, name, listed) = (result.id, result.itemName, result.listedDate ?? Date())
            Task { await NotificationManager.shared.scheduleLedgerFollowUp(itemID: id, itemName: name, from: listed) }
        default:
            // Moved back to scanned/owned — drop any pending follow-up.
            if previous == .listed {
                let id = result.id
                Task { await NotificationManager.shared.cancelLedgerFollowUp(itemID: id) }
            }
        }
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
                                .foregroundStyle(Color.snapWarmGray.opacity(0.5))
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
                    let condition = String(
                        (result.conditionNotes
                            .components(separatedBy: CharacterSet(charactersIn: "—–-."))
                            .first?
                            .trimmingCharacters(in: .whitespaces) ?? "")
                            .prefix(22)
                    )
                    if !condition.isEmpty {
                        photoChip(condition)
                    }
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

    private var heroAccessibilityLabel: String {
        var parts = [result.itemName]
        if !result.brand.isEmpty, result.brand != "Unknown" {
            parts.append("Brand \(result.brand)")
        }
        if !result.conditionNotes.isEmpty {
            parts.append(result.conditionNotes)
        }
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

    private var valueCard: some View {
        VStack(spacing: 16) {
            VStack(spacing: 6) {
                Text("Estimated Resale Value")
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)

                ValueRangeView(low: result.displayValueLow, high: result.displayValueHigh)
            }

            Divider()

            HStack(spacing: 10) {
                ConfidenceBadge(confidence: result.confidence)

                Text("AI estimate")
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
            "\(result.formattedRange). \(result.confidence) confidence AI estimate."
        )
        // Read first when the sheet opens — it is why the user is here.
        .accessibilitySortPriority(100)
        .accessibilityAddTraits(.isSummaryElement)
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
            } else {
                lockedListingTeaser
            }

            // Honest-MVP limitation, also stated in code (see Marketplace.webSellURL):
            // we generate the text; posting stays a manual, user-controlled paste.
            Text("SnapWorth writes it — you paste & post. We never post for you.")
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray.opacity(0.7))
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
            .background(Color.snapTerracotta)
            .clipShape(Capsule())
            .accessibilityLabel("Pro feature")
    }

    private var marketplacePicker: some View {
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
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 9)
                        .background(selected ? Color.snapTerracotta : Color.clear)
                        .clipShape(Capsule())
                        .overlay(Capsule().strokeBorder(
                            selected ? Color.snapTerracotta : Color.snapBorder,
                            lineWidth: selected ? 2 : 1))
                }
                .buttonStyle(.plain)
                .snapHitTarget()
                .accessibilityLabel(marketplace.displayName)
                .accessibilityHint("Writes the listing in \(marketplace.displayName)'s style")
                .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
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
                    .foregroundStyle(Color.snapTerracotta)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: .infinity, alignment: .center)
                PrimaryButton(title: "Try again") {
                    Task { await vm.generateListing(result: result) }
                }
            }
        } else {
            PrimaryButton(
                title: vm.isGeneratingListing
                    ? "Writing your listing…"
                    : "Generate \(vm.selectedMarketplace.displayName) listing"
            ) {
                Task { await vm.generateListing(result: result) }
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
                Task { await vm.generateListing(result: result) }
            }
            .font(.dmSans(13, weight: .semibold))
            .foregroundStyle(Color.snapTerracotta)
            .frame(maxWidth: .infinity)
        }
    }

    private func priceTag(_ label: String, _ value: Double) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)
            Text(NumberFormatter.snapCurrency.string(from: NSNumber(value: value)) ?? "$\(Int(value))")
                .font(.dmSans(17, weight: .bold))
                .foregroundStyle(Color.snapEspresso)
        }
    }

    private func secondaryButton(_ title: String, icon: String, action: @escaping () -> Void) -> some View {
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
    }

    private var lockedListingTeaser: some View {
        ZStack {
            VStack(alignment: .leading, spacing: 6) {
                Text("\(result.itemName) — \(result.condition.listingPhrase), ready to ship")
                    .font(.dmSans(15, weight: .semibold))
                    .foregroundStyle(Color.snapEspresso)
                    .lineLimit(1)
                Text("A polished description tailored to \(vm.selectedMarketplace.displayName), priced to sell with a smart negotiation floor…")
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
                    .foregroundStyle(Color.snapTerracotta)
                PrimaryButton(title: "Unlock marketplace listings") {
                    Analytics.shared.track(.paywallViewed(trigger: .snapSell))
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
                    .foregroundStyle(didSave ? Color.snapSage : Color.snapAmber)
                Text(didSave
                     ? "Saved to My Finds"
                     : "Couldn't save to My Finds — this result won't be kept")
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .accessibilityElement(children: .combine)

            Text("SnapWorth")
                .font(.fraunces(13, weight: .bold))
                .foregroundStyle(Color.snapWarmGray.opacity(0.5))
                .kerning(0.5)
        }
    }
}
