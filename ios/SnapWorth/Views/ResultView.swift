import SwiftUI

struct ResultView: View {
    let result: ScanResult
    var onDismiss: () -> Void

    @Environment(\.displayScale) private var displayScale

    @State private var vm = ResultViewModel()
    @State private var photo: UIImage?
    @State private var paidPriceText: String
    @State private var soldPriceText: String
    @State private var feesText: String
    @FocusState private var focusedField: Field?
    @State private var showShareSheet = false

    private enum Field { case paid, sold, fees }

    init(result: ScanResult, onDismiss: @escaping () -> Void) {
        self.result = result
        self.onDismiss = onDismiss
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
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button(action: onDismiss) {
                        circleButton(icon: "xmark")
                    }
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
                TextField("0", text: $paidPriceText)
                    .keyboardType(.decimalPad)
                    .font(.dmSans(17, weight: .medium))
                    .foregroundStyle(Color.snapEspresso)
                    .focused($focusedField, equals: .paid)
            }
            Text("Adds your find multiple to the share card")
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray.opacity(0.7))
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
        } label: {
            Text(status.label)
                .font(.dmSans(13, weight: .semibold))
                .foregroundStyle(selected ? Color.snapBackground : Color.snapWarmGray)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 9)
                .background(selected ? Color.snapTerracotta : Color.clear)
                .clipShape(Capsule())
                .overlay(Capsule().strokeBorder(Color.snapBorder, lineWidth: selected ? 0 : 1))
        }
        .buttonStyle(.plain)
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
            Spacer()
            Text("$").foregroundStyle(Color.snapWarmGray)
            TextField("0", text: text)
                .keyboardType(.decimalPad)
                .multilineTextAlignment(.trailing)
                .frame(maxWidth: 90)
                .focused($focusedField, equals: field)
                .font(.dmSans(15, weight: .semibold))
                .foregroundStyle(Color.snapEspresso)
        }
    }

    private var profitRow: some View {
        HStack {
            Text("Profit")
                .font(.dmSans(15, weight: .semibold))
                .foregroundStyle(Color.snapEspresso)
            Spacer()
            if let profit = result.realizedProfit {
                Text(Self.signedProfit(profit))
                    .font(.dmSans(17, weight: .bold))
                    .foregroundStyle(profit < 0 ? Color.snapTerracotta : Color.snapSage)
            } else {
                // Sold but no cost basis → profit unknown; never guessed.
                Text("—")
                    .font(.dmSans(17, weight: .bold))
                    .foregroundStyle(Color.snapWarmGray)
            }
        }
    }

    private var soldDateBinding: Binding<Date> {
        Binding(
            get: { result.soldDate ?? Date() },
            set: { result.soldDate = $0 }
        )
    }

    private func setStatus(_ status: FlipStatus) {
        UISelectionFeedbackGenerator().selectionChanged()
        let wasSold = result.status == .sold
        result.status = status
        if status == .sold {
            if result.soldDate == nil { result.soldDate = Date() }
            if !wasSold { Analytics.shared.track(.ledgerItemMarkedSold) }
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
                                .font(.system(size: 48))
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
            .font(.system(size: 14, weight: .semibold))
            .foregroundStyle(Color.snapEspresso)
            .frame(width: 36, height: 36)
            .background(.ultraThinMaterial)
            .clipShape(Circle())
    }

    // MARK: - Value Card

    private var valueCard: some View {
        VStack(spacing: 16) {
            VStack(spacing: 6) {
                Text("Estimated Resale Value")
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)

                ValueRangeView(low: result.valueLow, high: result.valueHigh)
            }

            Divider()

            HStack(spacing: 10) {
                ConfidenceBadge(confidence: result.confidence)

                if result.soldListingsCount > 0 {
                    Text("from \(result.soldListingsCount) sold listings")
                        .font(.snapCaption)
                        .foregroundStyle(Color.snapWarmGray)
                        .lineLimit(1)
                }

                Spacer()
            }
        }
        .padding(20)
        .frame(maxWidth: .infinity)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .shadow(color: Color.snapCardShadow.opacity(0.12), radius: 24, x: 0, y: 8)
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
            .animation(.spring(duration: 0.2), value: vm.didCopyListing)
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .shadow(color: Color.snapCardShadow.opacity(0.08), radius: 24, x: 0, y: 8)
    }

    // MARK: - Footer

    private var footer: some View {
        VStack(spacing: 12) {
            HStack(spacing: 6) {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(Color.snapSage)
                Text("Saved to My Finds")
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)
            }

            Text("SnapWorth")
                .font(.fraunces(13, weight: .bold))
                .foregroundStyle(Color.snapWarmGray.opacity(0.5))
                .kerning(0.5)
        }
    }
}
