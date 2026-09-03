import SwiftUI
import SwiftData
import UIKit

/// Thrift Flip — decide instantly, while thrifting, whether an item can be
/// resold at a profit. Scan the item (reuses the valuation core), read its shelf
/// price (OCR or manual), pick where you'd sell, and get a green/red verdict.
///
/// Premium: the profit verdict is gated (soft paywall / blurred teaser). The base
/// valuation (item + resale range) stays free so the user reaches the money-moment
/// before any wall.
struct ThriftFlipView: View {
    let purchaseService: any PurchaseService

    @Environment(\.modelContext) private var modelContext
    @Environment(\.dismiss) private var dismiss

    @State private var vm = ThriftFlipViewModel()
    @State private var capture: CaptureRequest?
    @FocusState private var focusedField: Field?

    private enum Field { case purchase, resale, shipping }

    private var isPro: Bool { purchaseService.isSubscribed }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 12) {
                    if vm.scanResult != nil {
                        loadedContent
                    } else {
                        emptyState
                    }
                }
                .padding(20)
            }
            .scrollDismissesKeyboard(.interactively)
            .background(Color.snapBackground)
            .navigationTitle("Thrift Flip")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("Close") { dismiss() }
                        .foregroundStyle(Color.snapTerracotta)
                }
                if vm.scanResult != nil {
                    ToolbarItem(placement: .navigationBarTrailing) {
                        Button("New item") { vm.reset() }
                            .foregroundStyle(Color.snapTerracotta)
                    }
                }
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") { focusedField = nil }
                        .font(.dmSans(15, weight: .semibold))
                        .foregroundStyle(Color.snapTerracotta)
                }
            }
        }
        .sheet(item: $capture) { request in
            CameraImagePicker(sourceType: request.source) { image in
                handleCaptured(image, for: request.target)
            }
            .ignoresSafeArea()
        }
        .sheet(isPresented: $vm.showPaywall) {
            PaywallView(purchaseService: purchaseService, trigger: .thriftFlip)
        }
    }

    // MARK: - Empty state (scan the item)

    private var emptyState: some View {
        VStack(spacing: 16) {
            Image(systemName: "arrow.triangle.2.circlepath")
                .snapSymbol(40, weight: .light)
                .foregroundStyle(Color.snapTerracotta)
                .padding(.top, 40)

            Text("Should you flip it?")
                .font(.fraunces(22, weight: .bold))
                .foregroundStyle(Color.snapEspresso)

            Text("Scan the item to get its resale value, add the shop price, and see your profit after fees — before you buy.")
                .font(.snapBody)
                .foregroundStyle(Color.snapWarmGray)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)

            if vm.isScanningItem {
                ProgressView("Identifying…")
                    .tint(Color.snapTerracotta)
                    .padding(.top, 8)
            } else {
                VStack(spacing: 10) {
                    PrimaryButton(title: "Scan item") { present(.item, source: .camera) }
                    Button("Choose from library") { present(.item, source: .photoLibrary) }
                        .font(.dmSans(14, weight: .semibold))
                        .foregroundStyle(Color.snapTerracotta)
                }
                .padding(.top, 8)
            }

            if let error = vm.scanError {
                Text(error)
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapTerracotta)
                    .multilineTextAlignment(.center)
            }
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: - Loaded content

    @ViewBuilder
    private var loadedContent: some View {
        if let result = vm.scanResult {
            itemHeader(result)
            marketplacePicker
            inputsCard
            verdictSection
            if isPro, let calc = vm.calculation, calc.isProfitable, !vm.didSaveToLedger {
                saveToLedgerButton
            }
            honestNote
        }
    }

    private func itemHeader(_ result: ScanResult) -> some View {
        HStack(spacing: 14) {
            if let image = vm.itemImage {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFill()
                    .frame(width: 64, height: 64)
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            }
            VStack(alignment: .leading, spacing: 4) {
                Text(result.itemName)
                    .font(.dmSans(16, weight: .semibold))
                    .foregroundStyle(Color.snapEspresso)
                    .lineLimit(2)
                Text("Resale \(result.formattedRange)")
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)
            }
            Spacer()
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
    }

    private var marketplacePicker: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Where you'd sell")
                .snapSectionHeader()
            ScrollView(.horizontal, showsIndicators: false) {
              HStack(spacing: 8) {
                ForEach(Marketplace.allCases) { marketplace in
                    let selected = vm.selectedMarketplace == marketplace
                    Button {
                        Haptics.selection()
                        vm.selectedMarketplace = marketplace
                    } label: {
                        Text(marketplace.displayName)
                            .font(.dmSans(13, weight: .semibold))
                            .foregroundStyle(selected ? Color.snapOnAccent : Color.snapWarmGray)
                            .padding(.horizontal, 14)
                            .padding(.vertical, 9)
                            .background(selected ? Color.snapTerracotta : Color.clear)
                            .clipShape(Capsule())
                            .overlay(Capsule().strokeBorder(Color.snapBorder, lineWidth: selected ? 0 : 1))
                    }
                    .buttonStyle(.plain)
                }
              }
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
    }

    private var inputsCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            // Shop price with OCR
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("Shop price")
                        .snapSectionHeader()
                    Spacer()
                    Button {
                        present(.tag, source: .camera)
                    } label: {
                        HStack(spacing: 5) {
                            if vm.isReadingTag {
                                ProgressView().controlSize(.mini)
                            } else {
                                Image(systemName: "text.viewfinder")
                            }
                            Text("Scan tag")
                        }
                        .font(.dmSans(13, weight: .semibold))
                        .foregroundStyle(Color.snapTerracotta)
                    }
                    .disabled(vm.isReadingTag)
                }
                moneyField($vm.shelfPriceText, field: .purchase, placeholder: "0")
                if let note = vm.ocrNote {
                    Text(note)
                        .font(.snapCaption)
                        .foregroundStyle(Color.snapWarmGray.opacity(0.8))
                }
            }

            Divider()

            labeledMoneyRow("Expected resale", text: $vm.resalePriceText, field: .resale)
            labeledMoneyRow("Shipping (optional)", text: $vm.shippingText, field: .shipping)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
    }

    // MARK: - Verdict

    @ViewBuilder
    private var verdictSection: some View {
        if let calc = vm.calculation {
            verdictCard(calc)
        } else {
            Text("Add the shop price to see your profit.")
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
        }
    }

    private func verdictCard(_ calc: FlipCalculation) -> some View {
        let green = calc.isProfitable
        let accent = green ? Color.snapSage : Color.snapTerracotta

        return VStack(spacing: 14) {
            // Headline verdict
            HStack(spacing: 8) {
                Image(systemName: green ? "checkmark.seal.fill" : "xmark.seal.fill")
                Text(green ? "Worth flipping" : "Skip it")
                    .font(.dmSans(17, weight: .bold))
            }
            .foregroundStyle(accent)

            // The money line — blurred for free users (soft paywall).
            ZStack {
                VStack(spacing: 8) {
                    Text(verdictLine(calc))
                        .font(.dmSans(16, weight: .semibold))
                        .foregroundStyle(Color.snapEspresso)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)

                    HStack(spacing: 20) {
                        metric("Net profit", ThriftFlipViewModel.signedMoney(calc.netProfit), accent)
                        if let roi = calc.roi {
                            metric("ROI", ThriftFlipViewModel.percent(roi), Color.snapEspresso)
                        } else if let margin = calc.margin {
                            metric("Margin", ThriftFlipViewModel.percent(margin), Color.snapEspresso)
                        }
                    }

                    feeBreakdown(calc)
                }
                .blur(radius: isPro ? 0 : 7)
                .accessibilityHidden(!isPro)

                if !isPro {
                    VStack(spacing: 10) {
                        Image(systemName: "lock.fill").foregroundStyle(Color.snapTerracotta)
                        Text("Unlock to reveal your profit")
                            .font(.dmSans(14, weight: .semibold))
                            .foregroundStyle(Color.snapEspresso)
                        PrimaryButton(title: "Unlock Thrift Flip") {
                            Analytics.shared.track(.paywallViewed(trigger: .thriftFlip))
                            vm.showPaywall = true
                        }
                    }
                }
            }

            if calc.feesUnknown {
                Text("Fees for this marketplace aren't in the table — this is before fees.")
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray.opacity(0.8))
                    .multilineTextAlignment(.center)
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity)
        .background(accent.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).strokeBorder(accent.opacity(0.35), lineWidth: 1))
        .onAppear { if isPro { vm.trackVerdict() } }
    }

    private func verdictLine(_ calc: FlipCalculation) -> String {
        let resale = ThriftFlipViewModel.money(calc.resalePrice)
        let profit = ThriftFlipViewModel.signedMoney(calc.netProfit)
        return calc.isProfitable
            ? "Resell at \(resale) → \(profit) profit after fees."
            : "Resell at \(resale) → \(profit) after fees. Not worth it."
    }

    private func metric(_ label: String, _ value: String, _ color: Color) -> some View {
        VStack(spacing: 2) {
            Text(label).font(.snapCaption).foregroundStyle(Color.snapWarmGray)
            Text(value).font(.dmSans(18, weight: .bold)).foregroundStyle(color)
        }
    }

    private func feeBreakdown(_ calc: FlipCalculation) -> some View {
        VStack(spacing: 3) {
            breakdownRow("Resale", ThriftFlipViewModel.money(calc.resalePrice))
            breakdownRow("− Fees", ThriftFlipViewModel.money(calc.platformFees))
            if calc.shippingCost > 0 {
                breakdownRow("− Shipping", ThriftFlipViewModel.money(calc.shippingCost))
            }
            breakdownRow("− Paid", ThriftFlipViewModel.money(calc.purchasePrice))
        }
        .padding(.top, 4)
    }

    private func breakdownRow(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).font(.snapCaption).foregroundStyle(Color.snapWarmGray)
            Spacer()
            Text(value).font(.dmSans(13, weight: .medium)).foregroundStyle(Color.snapEspresso)
        }
    }

    private var saveToLedgerButton: some View {
        Button {
            vm.saveToLedger(repository: ScanRepository(context: modelContext))
            Haptics.success()
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "bag.badge.plus")
                Text("Save to My Flips")
            }
            .font(.dmSans(15, weight: .semibold))
            .foregroundStyle(Color.snapEspresso)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 13)
            .background(Color.snapCard)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(Color.snapBorder, lineWidth: 1))
        }
        .buttonStyle(.plain)
    }

    private var honestNote: some View {
        Text("Resale is an AI estimate and fees are approximate — treat the verdict as a guide, not a guarantee.")
            .font(.snapCaption)
            .foregroundStyle(Color.snapWarmGray.opacity(0.7))
            .multilineTextAlignment(.center)
            .padding(.top, 4)
    }

    // MARK: - Inputs

    private func moneyField(_ text: Binding<String>, field: Field, placeholder: String) -> some View {
        HStack(spacing: 4) {
            Text("$").font(.dmSans(17, weight: .medium)).foregroundStyle(Color.snapWarmGray)
            TextField(placeholder, text: text)
                .keyboardType(.decimalPad)
                .font(.dmSans(17, weight: .medium))
                .foregroundStyle(Color.snapEspresso)
                .focused($focusedField, equals: field)
        }
    }

    private func labeledMoneyRow(_ title: String, text: Binding<String>, field: Field) -> some View {
        HStack {
            Text(title).font(.dmSans(14, weight: .medium)).foregroundStyle(Color.snapWarmGray)
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

    // MARK: - Capture routing

    private func present(_ target: CaptureTarget, source: UIImagePickerController.SourceType) {
        let resolved = UIImagePickerController.isSourceTypeAvailable(source) ? source : .photoLibrary
        capture = CaptureRequest(target: target, source: resolved)
    }

    private func handleCaptured(_ image: UIImage, for target: CaptureTarget) {
        capture = nil
        switch target {
        case .item: Task { await vm.scanItem(image: image, purchaseService: purchaseService) }
        case .tag:  Task { await vm.readPriceTag(image: image) }
        }
    }

    private enum CaptureTarget { case item, tag }

    private struct CaptureRequest: Identifiable {
        let id = UUID()
        let target: CaptureTarget
        let source: UIImagePickerController.SourceType
    }
}

// ── Camera / library picker ───────────────────────────────────────────────────

/// Thin wrapper over `UIImagePickerController` so Thrift Flip can capture an item
/// photo or a price tag from the camera (or the library on devices without one).
/// Kept here rather than in a shared file — it's used only by this feature.
private struct CameraImagePicker: UIViewControllerRepresentable {
    let sourceType: UIImagePickerController.SourceType
    let onImage: (UIImage) -> Void

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.sourceType = sourceType
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ picker: UIImagePickerController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(onImage: onImage) }

    final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let onImage: (UIImage) -> Void
        init(onImage: @escaping (UIImage) -> Void) { self.onImage = onImage }

        func imagePickerController(_ picker: UIImagePickerController,
                                   didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
            if let image = info[.originalImage] as? UIImage {
                onImage(image)
            }
            picker.presentingViewController?.dismiss(animated: true)
        }

        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            picker.presentingViewController?.dismiss(animated: true)
        }
    }
}
