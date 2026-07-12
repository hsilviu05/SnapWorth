import SwiftUI

struct ResultView: View {
    let result: ScanResult
    var onDismiss: () -> Void

    @State private var vm = ResultViewModel()
    @State private var photo: UIImage?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 0) {

                    // ── Hero photo ─────────────────────────────────────────
                    heroPhoto

                    // ── Value card (overlaps photo bottom) ─────────────────
                    valueCard
                        .padding(.horizontal, 20)
                        .offset(y: -28)
                        .zIndex(1)

                    // ── Details card ───────────────────────────────────────
                    detailsCard
                        .padding(.horizontal, 20)
                        .padding(.top, -12)

                    // ── Listing draft card ─────────────────────────────────
                    if !result.listingTitle.isEmpty || !result.listingDescription.isEmpty {
                        listingDraftCard
                            .padding(.horizontal, 20)
                            .padding(.top, 12)
                    }

                    // ── Footer ─────────────────────────────────────────────
                    footer
                        .padding(.top, 20)
                        .padding(.bottom, 40)
                }
            }
            .background(Color.snapBackground)
            .ignoresSafeArea(edges: .top)
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(.hidden, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    ShareLink(item: vm.shareText(result: result)) {
                        Image(systemName: "square.and.arrow.up")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundStyle(Color.snapEspresso)
                            .frame(width: 36, height: 36)
                            .background(.ultraThinMaterial)
                            .clipShape(Circle())
                    }
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    CircleToolbarButton(icon: "xmark") { onDismiss() }
                }
            }
        }
        .task(id: result.id) {
            guard let data = result.imageData else { return }
            photo = await Task.detached(priority: .userInitiated) {
                UIImage(data: data)
            }.value
        }
    }

    // MARK: - Hero Photo

    private var heroPhoto: some View {
        ZStack(alignment: .bottomLeading) {
            Group {
                if let img = photo {
                    Image(uiImage: img)
                        .resizable()
                        .scaledToFill()
                } else {
                    Rectangle()
                        .fill(Color.snapBorder)
                        .overlay(
                            Image(systemName: "photo")
                                .font(.system(size: 48))
                                .foregroundStyle(Color.snapWarmGray.opacity(0.5))
                        )
                }
            }
            .frame(height: 360)
            .clipped()

            // Gradient overlay
            LinearGradient(
                stops: [
                    .init(color: .clear, location: 0.4),
                    .init(color: Color.black.opacity(0.65), location: 1.0)
                ],
                startPoint: .top,
                endPoint: .bottom
            )

            // Item name + chips on photo
            VStack(alignment: .leading, spacing: 8) {
                Text(result.itemName)
                    .font(.fraunces(26, weight: .bold))
                    .foregroundStyle(.white)
                    .shadow(color: .black.opacity(0.3), radius: 4, x: 0, y: 2)
                    .fixedSize(horizontal: false, vertical: true)

                HStack(spacing: 8) {
                    if !result.brand.isEmpty && result.brand != "Unknown" {
                        photoChip(result.brand, color: Color.white.opacity(0.2))
                    }
                    let conditionLabel = result.conditionNotes
                        .components(separatedBy: CharacterSet(charactersIn: "—–-"))
                        .first?
                        .trimmingCharacters(in: .whitespaces)
                        .prefix(20)
                        .description ?? ""
                    if !conditionLabel.isEmpty {
                        photoChip(conditionLabel, color: Color.white.opacity(0.2))
                    }
                }
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 44)
        }
    }

    private func photoChip(_ label: String, color: Color) -> some View {
        Text(label)
            .font(.snapLabel)
            .foregroundStyle(.white)
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(color)
            .background(.ultraThinMaterial)
            .clipShape(Capsule())
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

            Divider().background(Color.snapBorder)

            HStack(spacing: 10) {
                ConfidenceBadge(confidence: result.confidence)

                if result.soldListingsCount > 0 {
                    Text("from \(result.soldListingsCount) sold listings")
                        .font(.snapCaption)
                        .foregroundStyle(Color.snapWarmGray)
                }

                Spacer()
            }
        }
        .padding(20)
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
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
            .snapCard()
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
            }

            if !result.listingDescription.isEmpty {
                Text(result.listingDescription)
                    .font(.snapBody)
                    .foregroundStyle(Color.snapWarmGray)
                    .fixedSize(horizontal: false, vertical: true)
            }

            PrimaryButton(
                title: vm.didCopyListing ? "Copied!" : "Copy listing draft"
            ) {
                vm.copyListing(result: result)
            }
            .animation(.spring(duration: 0.2), value: vm.didCopyListing)
        }
        .padding(20)
        .snapCard()
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

// MARK: - Toolbar Button

private struct CircleToolbarButton: View {
    let icon: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: icon)
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(Color.snapEspresso)
                .frame(width: 36, height: 36)
                .background(.ultraThinMaterial)
                .clipShape(Circle())
        }
    }
}
