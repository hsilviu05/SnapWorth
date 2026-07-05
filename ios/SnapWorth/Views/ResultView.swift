import SwiftUI

struct ResultView: View {
    let result: ScanResult
    var onDismiss: () -> Void

    @State private var vm = ResultViewModel()

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {

                    // ── Photo card ─────────────────────────────────────────
                    Group {
                        if let data = result.imageData, let uiImg = UIImage(data: data) {
                            Image(uiImage: uiImg)
                                .resizable()
                                .scaledToFill()
                        } else {
                            Rectangle()
                                .fill(Color.snapBorder)
                                .overlay(
                                    Image(systemName: "photo")
                                        .font(.system(size: 40))
                                        .foregroundStyle(Color.snapWarmGray)
                                )
                        }
                    }
                    .frame(height: 240)
                    .clipped()
                    .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
                    .padding(.horizontal, 20)

                    // ── Item details card ──────────────────────────────────
                    VStack(alignment: .leading, spacing: 16) {

                        // Item name
                        Text(result.itemName)
                            .font(.fraunces(22, weight: .bold))
                            .foregroundStyle(Color.snapEspresso)
                            .fixedSize(horizontal: false, vertical: true)

                        // Brand + Condition chips
                        HStack(spacing: 8) {
                            if !result.brand.isEmpty && result.brand != "Unknown" {
                                ChipView(
                                    label: result.brand,
                                    color: Color.snapTerracotta.opacity(0.12),
                                    textColor: Color.snapTerracotta
                                )
                            }
                            ChipView(
                                label: result.conditionNotes.components(separatedBy: "—").first?.trimmingCharacters(in: .whitespaces) ?? result.conditionNotes,
                                color: Color.snapBorder,
                                textColor: Color.snapEspresso
                            )
                        }

                        Divider()
                            .background(Color.snapBorder)

                        // Value hero
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Estimated Resale Value")
                                .font(.snapCaption)
                                .foregroundStyle(Color.snapWarmGray)

                            ValueRangeView(low: result.valueLow, high: result.valueHigh)
                        }

                        // Confidence + source
                        HStack(spacing: 10) {
                            ConfidenceBadge(confidence: result.confidence)

                            if result.soldListingsCount > 0 {
                                Text("Based on \(result.soldListingsCount) sold listings")
                                    .font(.snapCaption)
                                    .foregroundStyle(Color.snapWarmGray)
                            }

                            Spacer()
                        }

                        // Condition notes (full)
                        if !result.conditionNotes.isEmpty {
                            VStack(alignment: .leading, spacing: 4) {
                                Text("Condition")
                                    .snapSectionHeader()
                                Text(result.conditionNotes)
                                    .font(.snapBody)
                                    .foregroundStyle(Color.snapEspresso)
                            }
                        }
                    }
                    .padding(20)
                    .snapCard()
                    .padding(.horizontal, 20)

                    // ── Listing draft card ─────────────────────────────────
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Listing Draft")
                            .snapSectionHeader()

                        Text(result.listingTitle)
                            .font(.dmSans(15, weight: .semibold))
                            .foregroundStyle(Color.snapEspresso)

                        Text(result.listingDescription)
                            .font(.snapBody)
                            .foregroundStyle(Color.snapWarmGray)
                            .fixedSize(horizontal: false, vertical: true)

                        PrimaryButton(
                            title: vm.didCopyListing ? "Copied!" : "Copy listing draft"
                        ) {
                            vm.copyListing(result: result)
                        }
                        .animation(.spring(duration: 0.2), value: vm.didCopyListing)
                    }
                    .padding(20)
                    .snapCard()
                    .padding(.horizontal, 20)

                    HStack(spacing: 6) {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(Color.snapSage)
                        Text("Saved to My Finds")
                            .font(.snapCaption)
                            .foregroundStyle(Color.snapWarmGray)
                    }
                    .padding(.bottom, 32)
                }
                .padding(.top, 12)
            }
            .background(Color.snapBackground)
            .navigationTitle("Result")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") { onDismiss() }
                        .font(.dmSans(16, weight: .semibold))
                        .foregroundStyle(Color.snapTerracotta)
                }
            }
        }
    }
}
