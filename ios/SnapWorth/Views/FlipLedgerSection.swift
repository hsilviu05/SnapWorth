import SwiftUI

// The flip ledger half of the result sheet, out of `ResultView` (#229, stage
// 4). The sheet keeps the text fields' state (it parses them into the result
// in `.onChange`) and the keyboard focus; these cards take bindings to both
// and read nothing else of the sheet's.

/// What the user paid, which the share card turns into a find multiple.
struct PaidPriceCard: View {
    @Binding var text: String
    let focus: FocusState<ResultView.Field?>.Binding

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("What did you pay?")
                .snapSectionHeader()
            HStack(spacing: 4) {
                Text("$")
                    .font(.dmSans(17, weight: .medium))
                    .foregroundStyle(Color.snapWarmGray)
                    .accessibilityHidden(true)
                TextField("0", text: $text)
                    .keyboardType(.decimalPad)
                    .font(.dmSans(17, weight: .medium))
                    .foregroundStyle(Color.snapEspresso)
                    .focused(focus, equals: .paid)
                    .accessibilityLabel("What did you pay?")
                    .accessibilityValue(text.isEmpty
                        ? String(localized: "Not set")
                        : String(localized: "\(text) dollars"))
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

    private func moneyRow(title: LocalizedStringKey, text: Binding<String>, field: ResultView.Field) -> some View {
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
                .focused(focus, equals: field)
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
        onLedgerChange()
    }

    private static func signedProfit(_ d: Decimal) -> String {
        let money = NumberFormatter.snapCurrency.string(from: NSDecimalNumber(decimal: abs(d))) ?? "$0"
        return d < 0 ? "−\(money)" : "+\(money)"
    }
}
