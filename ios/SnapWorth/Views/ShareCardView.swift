import SwiftUI
import SwiftData
import UniformTypeIdentifiers

// MARK: - Transferable wrapper for ShareLink

struct ShareableImage: Transferable {
    let uiImage: UIImage

    static var transferRepresentation: some TransferRepresentation {
        DataRepresentation(exportedContentType: .png) { img in
            guard let data = img.uiImage.pngData() else {
                throw ShareCardError.renderFailed
            }
            return data
        }
    }
}

private enum ShareCardError: Error { case renderFailed }

// MARK: - Branded share card

/// Fixed 540×960 pt canvas. Rendered via ImageRenderer at scale ≥ 2
/// → minimum 1080×1920 px output (9:16, correct for IG Stories / WhatsApp status / TikTok).
/// Uses hardcoded brand colours so output is identical in light and dark mode.
struct ShareCardView: View {
    let result: ScanResult
    let photo: UIImage?

    static let cardWidth:  CGFloat = 540
    static let cardHeight: CGFloat = 960

    private let sidePad:  CGFloat = 24
    private let innerPad: CGFloat = 36

    var body: some View {
        VStack(spacing: 0) {
            // ── Item photo / placeholder ─────────────────────────────────
            photoSection
                .padding(.horizontal, sidePad)
                .padding(.top, 28)

            // ── Value — hero element (largest text on card) ──────────────
            Text(result.formattedRange)
                .font(Font.fraunces(56, weight: .bold))
                .foregroundStyle(Color(hex: "6F8F6B"))   // snapSage
                .minimumScaleFactor(0.45)
                .lineLimit(1)
                .padding(.top, 24)
                .padding(.horizontal, innerPad)

            // ── Item name ────────────────────────────────────────────────
            Text(result.itemName)
                .font(Font.fraunces(26, weight: .semibold))
                .foregroundStyle(Color(hex: "2B211C"))   // snapEspresso
                .multilineTextAlignment(.center)
                .lineLimit(2)
                .truncationMode(.tail)
                .minimumScaleFactor(0.7)
                .padding(.top, 10)
                .padding(.horizontal, innerPad)

            Spacer()

            // ── Footer ───────────────────────────────────────────────────
            Rectangle()
                .fill(Color(hex: "EFE6DC"))              // snapBorder
                .frame(height: 1)
                .padding(.horizontal, innerPad)

            VStack(spacing: 4) {
                Text("SnapWorth")
                    .font(Font.fraunces(20, weight: .bold))
                    .foregroundStyle(Color(hex: "2B211C"))
                Text("Know what it's worth.")
                    .font(Font.dmSans(14))
                    .foregroundStyle(Color(hex: "8B7D71")) // snapWarmGray
            }
            .padding(.top, 16)
            .padding(.bottom, 44)
        }
        .frame(width: ShareCardView.cardWidth, height: ShareCardView.cardHeight)
        .background(Color(hex: "FBF7F2"))                // snapBackground — fixed, not adaptive
    }

    @ViewBuilder
    private var photoSection: some View {
        let w = ShareCardView.cardWidth - sidePad * 2
        if let photo {
            Image(uiImage: photo)
                .resizable()
                .scaledToFill()
                .frame(width: w, height: 460)
                .clipShape(RoundedRectangle(cornerRadius: 28, style: .continuous))
        } else {
            // Text-only fallback when scan has no photo
            RoundedRectangle(cornerRadius: 28, style: .continuous)
                .fill(Color(hex: "EFE6DC"))
                .frame(width: w, height: 460)
                .overlay(
                    Image(systemName: "photo")
                        .font(.system(size: 72))
                        .foregroundStyle(Color(hex: "8B7D71").opacity(0.4))
                )
        }
    }
}

// MARK: - Previews

#Preview("Missing photo") {
    let config = ModelConfiguration(isStoredInMemoryOnly: true)
    let container = try! ModelContainer(for: ScanResult.self, configurations: config)
    let result = ScanResult(
        itemName: "Levi's 501 Original Jeans",
        brand: "Levi's", category: "Clothing",
        conditionNotes: "Good", valueLow: 45, valueHigh: 90,
        confidence: "high", soldListingsCount: 12,
        listingTitle: "", listingDescription: ""
    )
    container.mainContext.insert(result)
    return ShareCardView(result: result, photo: nil)
        .scaleEffect(0.5, anchor: .top)
        .frame(width: 270, height: 480)
        .modelContainer(container)
}

#Preview("Long name / large value") {
    let config = ModelConfiguration(isStoredInMemoryOnly: true)
    let container = try! ModelContainer(for: ScanResult.self, configurations: config)
    let result = ScanResult(
        itemName: "Vintage Tommy Hilfiger Oversized Windbreaker Jacket Navy Blue Size XXL",
        brand: "Tommy Hilfiger", category: "Clothing",
        conditionNotes: "Good", valueLow: 1200, valueHigh: 1600,
        confidence: "medium", soldListingsCount: 5,
        listingTitle: "", listingDescription: ""
    )
    container.mainContext.insert(result)
    return ShareCardView(result: result, photo: nil)
        .scaleEffect(0.5, anchor: .top)
        .frame(width: 270, height: 480)
        .modelContainer(container)
}
