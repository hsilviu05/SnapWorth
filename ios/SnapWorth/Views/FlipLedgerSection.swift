import SwiftUI

// The flip ledger half of the result sheet, out of `ResultView` (#229, stage
// 4). The sheet keeps the text fields' state (it parses them into the result
// in `.onChange`) and the keyboard focus; these cards take bindings to both
// and read nothing else of the sheet's.

/// What the user paid, which the share card turns into a find multiple.
struct PaidPriceCard: View {
    @Binding var text: String
    let focus: FocusState<ResultView.Field?>.Binding
    /// The flip's currency (`SaleCurrency.of`, #224): the symbol beside the
    /// field and the currency VoiceOver reads the amount in.
    var currencyCode: String = "USD"

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("What did you pay?")
                .snapSectionHeader()
            HStack(spacing: 4) {
                Text(SaleCurrency.symbol(currencyCode))
                    .font(.dmSans(17, weight: .medium))
                    .foregroundStyle(Color.snapWarmGray)
                    .accessibilityHidden(true)
                TextField("0", text: $text)
                    .keyboardType(.decimalPad)
                    .font(.dmSans(17, weight: .medium))
                    .foregroundStyle(Color.snapEspresso)
                    .focused(focus, equals: .paid)
                    .accessibilityLabel("What did you pay?")
                    .accessibilityValue(LedgerAmountSpeech.value(text, code: currencyCode))
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
}

/// Scanned → listed → sold, with the sold price, fees, date and the profit.
struct FlipStatusCard: View {
    let result: ScanResult
    @Binding var soldPriceText: String
    @Binding var feesText: String
    let focus: FocusState<ResultView.Field?>.Binding
    /// Everything that has to follow a change to the ledger figures — the
    /// sheet's `ledgerDidChange`, which refreshes the widget.
    let onLedgerChange: () -> Void

    var body: some View {
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
                SaleSharingConsentCard(result: result)
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
        currencyMenu
        moneyRow(title: "Sold for", text: $soldPriceText, field: .sold)
        moneyRow(title: "Fees (optional)", text: $feesText, field: .fees)
        DatePicker("Sold date", selection: soldDateBinding, in: ...Date(), displayedComponents: .date)
            .font(.dmSans(14, weight: .medium))
            .foregroundStyle(Color.snapEspresso)
            .tint(Color.snapTerracotta)
    }

    private func moneyRow(title: LocalizedStringKey, text: Binding<String>, field: ResultView.Field) -> some View {
        HStack {
            Text(title)
                .font(.dmSans(14, weight: .medium))
                .foregroundStyle(Color.snapWarmGray)
                // The label is carried by the field below; reading it twice is
                // noise for VoiceOver.
                .accessibilityHidden(true)
            Spacer()
            Text(SaleCurrency.symbol(SaleCurrency.of(result)))
                .foregroundStyle(Color.snapWarmGray)
                .accessibilityHidden(true)
            TextField("0", text: text)
                .keyboardType(.decimalPad)
                .multilineTextAlignment(.trailing)
                .frame(minWidth: 90)
                .focused(focus, equals: field)
                .font(.dmSans(15, weight: .semibold))
                .foregroundStyle(Color.snapEspresso)
                .accessibilityLabel(title)
                .accessibilityValue(LedgerAmountSpeech.value(text.wrappedValue,
                                                             code: SaleCurrency.of(result)))
                .modifier(DollarAmountHint(code: SaleCurrency.of(result)))
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
                    Text(signedProfit(profit))
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

    private var profitAccessibilityValue: String {
        guard let profit = result.realizedProfit else {
            return String(localized: "Unknown — add what you paid to calculate it")
        }
        let amount = signedProfit(profit)
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
        onLedgerChange()
    }

    /// Which currency this flip's amounts are in (#224). Defaults to the
    /// phone's region; a sale shared from here keeps it, never relabelled USD.
    private var currencyMenu: some View {
        HStack {
            Text("Currency")
                .font(.dmSans(14, weight: .medium))
                .foregroundStyle(Color.snapWarmGray)
            Spacer()
            Menu {
                ForEach(SaleCurrency.all, id: \.self) { code in
                    Button(code) { result.saleCurrency = code }
                }
            } label: {
                Text(SaleCurrency.of(result))
                    .font(.dmSans(15, weight: .semibold))
                    .foregroundStyle(Color.snapTerracottaText)
                    .frame(minHeight: 44)
            }
            .accessibilityLabel("Currency")
            .accessibilityValue(SaleCurrency.of(result))
        }
    }

    /// The same printer My Flips uses for this flip, so the sheet and the
    /// list cannot spell one profit two ways.
    private func signedProfit(_ d: Decimal) -> String {
        SaleCurrency.signed(d, code: SaleCurrency.of(result))
    }
}

/// What VoiceOver hears for a typed amount: the amount in its currency, "80
/// lei" or "$80". These fields said "80 dollars" whatever the flip's currency
/// (AUDIT-2026-10-07, M1); text that does not parse is read as typed.
enum LedgerAmountSpeech {
    static func value(_ text: String, code: String) -> String {
        guard !text.isEmpty else { return String(localized: "Not set") }
        guard let amount = MoneyInput.parse(text), amount.isFinite else { return text }
        return SaleCurrency.format(Decimal(amount), code: code)
    }
}

/// "Enter an amount in dollars", only where the amount is in dollars. There
/// is no hint for another currency rather than a wrong one: the label says
/// which field it is and the value says the currency.
private struct DollarAmountHint: ViewModifier {
    let code: String

    func body(content: Content) -> some View {
        if code == "USD" {
            content.accessibilityHint("Enter an amount in dollars")
        } else {
            content
        }
    }
}

/// The one-time ask (#224): shown under a sold flip's profit the first time a
/// sold price is saved, while sharing is off and has never been asked about.
/// "Allow" turns sharing on, and the sale is sent when the sheet closes;
/// "Not now" is final — it is never asked again, and Settings is the way in.
struct SaleSharingConsentCard: View {
    let result: ScanResult
    @AppStorage(SaleSharing.enabledKey) private var enabled = false
    @AppStorage(SaleSharing.askedKey) private var asked = false

    var body: some View {
        if !enabled, !asked, (result.soldPrice ?? 0) > 0 {
            VStack(alignment: .leading, spacing: 10) {
                Text("Help improve estimates?")
                    .font(.dmSans(15, weight: .semibold))
                    .foregroundStyle(Color.snapEspresso)
                Text(SaleSharingCopy.explanation)
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 10) {
                    Button("Not now") { asked = true }
                        .font(.dmSans(14, weight: .semibold))
                        .foregroundStyle(Color.snapWarmGray)
                        .frame(maxWidth: .infinity, minHeight: 44)
                    Button("Allow") {
                        asked = true
                        enabled = true
                    }
                    .font(.dmSans(14, weight: .semibold))
                    .foregroundStyle(Color.snapOnAccent)
                    .frame(maxWidth: .infinity, minHeight: 44)
                    .background(Color.snapTerracottaFill)
                    .clipShape(Capsule())
                }
            }
            .padding(14)
            .background(Color.snapBackground)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .accessibilityElement(children: .contain)
        }
    }
}

/// The words around sharing, in one place, so the card and Settings say
/// the same thing and a test can read them.
enum SaleSharingCopy {
    static var explanation: String {
        String(localized: "When you mark a find sold, SnapWorth can send its sale price, its currency and the estimate you saw, to measure how close estimates are. Never the photo, the item's name, your notes or what you paid. Off unless you allow it, and you can delete what you've shared in Settings.")
    }
}

