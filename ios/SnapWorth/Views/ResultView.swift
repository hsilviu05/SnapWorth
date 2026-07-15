import SwiftUI

struct ResultView: View {
    let result: ScanResult
    var onDismiss: () -> Void

    @Environment(\.displayScale) private var displayScale

    @State private var vm = ResultViewModel()
    @State private var photo: UIImage?

    var body: some View {
        NavigationStack {
            GeometryReader { geo in
                ScrollView {
                    VStack(spacing: 0) {
                        heroPhoto(width: geo.size.width)

                        valueCard
                            .padding(.horizontal, 20)
                            .offset(y: -28)

                        detailsCard
                            .padding(.horizontal, 20)
                            .padding(.top, -12)

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
            }
            .background(Color.snapBackground)
            .ignoresSafeArea(edges: .top)
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(.hidden, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    if let card = vm.shareCard {
                        ShareLink(
                            item: ShareableImage(uiImage: card),
                            preview: SharePreview(result.itemName)
                        ) {
                            circleButton(icon: "square.and.arrow.up")
                        }
                    } else {
                        circleButton(icon: "square.and.arrow.up")
                    }
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button(action: onDismiss) {
                        circleButton(icon: "xmark")
                    }
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
