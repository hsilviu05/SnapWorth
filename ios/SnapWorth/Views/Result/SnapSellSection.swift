import SwiftUI

// The listing half of the result sheet, out of `ResultView` (#229, stage 3).
// Each view takes its inputs in its initializer and reads none of the
// sheet's @State: what it needs to change there, it asks for through a
// closure.

/// The listing draft the scan came with, and a button to copy it.
struct ListingDraftCard: View {
    let result: ScanResult
    let vm: ResultViewModel

    var body: some View {
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
}

/// Snap → Sell: a marketplace-tailored listing for Pro, and the cleaned-up
/// listing photo (#91). Free users see a blurred teaser (soft paywall), so
/// they reach the payoff before the wall. Generation is gated on `isPro`;
/// the base valuation above stays free.
struct SnapSellSection: View {
    let result: ScanResult
    let vm: ResultViewModel
    let isPro: Bool
    let purchaseService: any PurchaseService
    /// The scan's own photo, the input to "Clean up photo"; nil hides it.
    let photo: UIImage?
    /// The sheet presents the share sheet with `vm.listingShareItems`.
    let onShareListing: () -> Void
    /// The sheet presents the paywall for this trigger.
    let onPaywall: (PaywallTrigger) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("Snap → Sell")
                    .snapSectionHeader()
                Spacer()
                if !isPro { ProBadge() }
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
                    onShareListing()
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
                Text(String(localized: "\(result.itemName) — \(result.condition.displayPhrase), ready to ship"))
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
                    onPaywall(.snapSell)
                }
            }
        }
    }
}

/// The PRO tag beside a gated card's header. Shared by the sheet's cards
/// (Snap → Sell, Why this price, Add the tag), so they cannot drift.
struct ProBadge: View {
    var body: some View {
        Text("PRO")
            .font(.dmSans(11, weight: .bold))
            .foregroundStyle(Color.snapOnAccent)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(Color.snapTerracottaFill)
            .clipShape(Capsule())
            .accessibilityLabel("Pro feature")
    }
}
