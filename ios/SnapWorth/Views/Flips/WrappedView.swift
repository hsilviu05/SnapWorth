import SwiftUI

/// Year in Finds: the year's card, previewed, with one button to share it.
///
/// Offered from My Finds in December and January (`ThriftWrapped.offeredYear`)
/// to anyone with enough finds that year. Sharing is free for everyone; the
/// profit line is Pro, because a year of sales is the all-time ledger
/// (`.ledgerHistory`) by another name.
struct WrappedView: View {
    let wrapped: ThriftWrapped
    let purchaseService: any PurchaseService

    @Environment(\.dismiss) private var dismiss
    @State private var includeProfit = true
    @State private var showPaywall = false
    @State private var shareImage: UIImage?
    @State private var renderFailed = false

    /// Read live, so the toggle replaces the lock as soon as a purchase lands.
    private var isPro: Bool { purchaseService.isSubscribed }

    private var showsProfit: Bool { isPro && wrapped.hasProfit && includeProfit }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    preview

                    if wrapped.hasProfit {
                        if isPro {
                            Toggle(isOn: $includeProfit) {
                                Text("Show my profit on the card")
                                    .font(.snapBody)
                                    .foregroundStyle(Color.snapEspresso)
                            }
                            .tint(Color.snapTerracotta)
                        } else {
                            Button { showPaywall = true } label: {
                                HStack(spacing: 12) {
                                    Image(systemName: "lock.fill")
                                        .snapSymbol(15, weight: .semibold)
                                        .foregroundStyle(Color.snapTerracottaText)
                                        .accessibilityHidden(true)
                                    Text(String(localized: "Add your \(String(wrapped.year)) profit with Pro"))
                                        .font(.snapBodyMedium)
                                        .foregroundStyle(Color.snapEspresso)
                                    Spacer()
                                    Image(systemName: "chevron.right")
                                        .snapSymbol(13, weight: .semibold)
                                        .foregroundStyle(Color.snapWarmGray)
                                        .accessibilityHidden(true)
                                }
                                .padding(14)
                                .background(Color.snapTerracotta.opacity(0.10))
                                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                            }
                            .buttonStyle(.plain)
                        }
                    }

                    PrimaryButton(title: "Share my year") { share() }
                }
                .padding(20)
            }
            .background(Color.snapBackground)
            .navigationTitle("Year in Finds")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .onAppear { Analytics.shared.track(.wrappedOpened(isPro: isPro)) }
        .sheet(isPresented: Binding(get: { shareImage != nil },
                                    set: { if !$0 { shareImage = nil } })) {
            if let shareImage {
                let withProfit = showsProfit
                ActivityShareSheet(items: [shareImage]) { _ in
                    Analytics.shared.track(.wrappedShared(withProfit: withProfit))
                }
            }
        }
        .sheet(isPresented: $showPaywall) {
            PaywallView(purchaseService: purchaseService, trigger: .wrapped)
        }
        .alert("Couldn't make the card", isPresented: $renderFailed) {
            Button("OK", role: .cancel) {}
        } message: {
            Text("Nothing was shared. Try again in a moment.")
        }
    }

    private var card: WrappedShareCardView {
        WrappedShareCardView(wrapped: wrapped, showsProfit: showsProfit)
    }

    /// The card at its real size, scaled down to the screen, so the preview is
    /// exactly what is shared.
    private var preview: some View {
        GeometryReader { geo in
            let scale = geo.size.width / WrappedShareCardView.cardWidth
            card
                .scaleEffect(scale, anchor: .topLeading)
                .frame(width: geo.size.width, alignment: .topLeading)
        }
        .aspectRatio(WrappedShareCardView.cardWidth / WrappedShareCardView.cardHeight,
                     contentMode: .fit)
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .shadow(color: Color.snapCardShadow, radius: 12, y: 4)
        .accessibilityElement(children: .combine)
    }

    private func share() {
        let renderer = ImageRenderer(content: card)
        renderer.scale = ResultViewModel.shareCardScale
        if let image = renderer.uiImage {
            shareImage = image
        } else {
            renderFailed = true
        }
    }
}

/// The My Finds banner that opens Year in Finds, in season only.
struct WrappedBanner: View {
    let year: Int
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            HStack(spacing: 14) {
                Image(systemName: "gift.fill")
                    .snapSymbol(20, weight: .medium)
                    .foregroundStyle(Color.snapTerracottaText)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(String(localized: "Your \(String(year)) Year in Finds is ready"))
                        .font(.dmSans(15, weight: .semibold))
                        .foregroundStyle(Color.snapEspresso)
                    Text("Your year in finds, ready to share")
                        .font(.snapCaption)
                        .foregroundStyle(Color.snapWarmGray)
                }
                Spacer()
                Image(systemName: "chevron.right")
                    .snapSymbol(13, weight: .semibold)
                    .foregroundStyle(Color.snapWarmGray)
                    .accessibilityHidden(true)
            }
            .padding(16)
            .background(Color.snapTerracotta.opacity(0.10))
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .strokeBorder(Color.snapTerracotta.opacity(0.25), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
    }
}
