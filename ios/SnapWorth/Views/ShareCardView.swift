import SwiftUI
import SwiftData
import UniformTypeIdentifiers
import CoreImage
import CoreImage.CIFilterBuiltins

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

            // ── Value hero (standard or paid mode) ──────────────────────
            heroSection

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
                .fill(Color(hex: "EFE6DC"))
                .frame(height: 1)
                .padding(.horizontal, innerPad)

            HStack(spacing: 14) {
                if let qr = qrImage {
                    Image(uiImage: qr)
                        .interpolation(.none)
                        .resizable()
                        .scaledToFit()
                        .frame(width: 48, height: 48)
                        .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text("SnapWorth")
                        .font(Font.fraunces(20, weight: .bold))
                        .foregroundStyle(Color(hex: "2B211C"))
                    Text("Get SnapWorth")
                        .font(Font.dmSans(14))
                        .foregroundStyle(Color(hex: "8B7D71"))
                }
                Spacer()
            }
            .padding(.horizontal, innerPad)
            .padding(.top, 16)
            .padding(.bottom, 44)
        }
        .frame(width: ShareCardView.cardWidth, height: ShareCardView.cardHeight)
        .background(Color(hex: "FBF7F2"))                // snapBackground — fixed, not adaptive
    }

    // MARK: - Hero section (standard or paid mode)

    @ViewBuilder
    private var heroSection: some View {
        if let paid = result.paidPrice {
            VStack(spacing: 6) {
                Text(paid == 0 ? "Free →" : "Paid \(fmtCurrency(paid)) →")
                    .font(Font.dmSans(17, weight: .semibold))
                    .foregroundStyle(Color(hex: "8B7D71"))
                    .lineLimit(1)

                Text(result.formattedRange)
                    .font(Font.fraunces(50, weight: .bold))
                    .foregroundStyle(Color(hex: "6F8F6B"))
                    .minimumScaleFactor(0.45)
                    .lineLimit(1)

                if let badge = findBadge(paid: paid) {
                    Text(badge)
                        .font(Font.dmSans(14, weight: .bold))
                        .foregroundStyle(Color(hex: "FBF7F2"))
                        .padding(.horizontal, 14)
                        .padding(.vertical, 6)
                        .background(Color(hex: "6F8F6B"))
                        .clipShape(Capsule())
                }
            }
            .padding(.top, 20)
            .padding(.horizontal, innerPad)
        } else {
            Text(result.formattedRange)
                .font(Font.fraunces(56, weight: .bold))
                .foregroundStyle(Color(hex: "6F8F6B"))
                .minimumScaleFactor(0.45)
                .lineLimit(1)
                .padding(.top, 24)
                .padding(.horizontal, innerPad)
        }
    }

    private func findBadge(paid: Double) -> String? {
        if paid == 0 { return "Free find" }
        guard paid < result.valueLow else { return nil }
        let multiple = Int(round(result.valueLow / paid))
        return multiple > 1 ? "\(multiple)x find" : nil
    }

    private func fmtCurrency(_ value: Double) -> String {
        NumberFormatter.snapCurrency.string(from: NSNumber(value: value)) ?? "$\(Int(value))"
    }

    private var qrImage: UIImage? {
        guard let data = Config.appStoreURL.data(using: .utf8),
              let filter = CIFilter(name: "CIQRCodeGenerator") else { return nil }
        filter.setValue(data, forKey: "inputMessage")
        filter.setValue("M", forKey: "inputCorrectionLevel")
        guard let ci = filter.outputImage else { return nil }
        let scaled = ci.transformed(by: CGAffineTransform(scaleX: 8, y: 8))
        let context = CIContext()
        guard let cg = context.createCGImage(scaled, from: scaled.extent) else { return nil }
        return UIImage(cgImage: cg)
    }

    // MARK: - Photo section

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

#Preview("Unpaid — no photo") {
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

#Preview("Paid · 9× find") {
    let config = ModelConfiguration(isStoredInMemoryOnly: true)
    let container = try! ModelContainer(for: ScanResult.self, configurations: config)
    let result = ScanResult(
        itemName: "Off-White OOO Out of Office Sneakers",
        brand: "Off-White", category: "Shoes",
        conditionNotes: "Good", valueLow: 275, valueHigh: 475,
        confidence: "high", soldListingsCount: 18,
        listingTitle: "", listingDescription: "",
        paidPrice: 30
    )
    container.mainContext.insert(result)
    return ShareCardView(result: result, photo: nil)
        .scaleEffect(0.5, anchor: .top)
        .frame(width: 270, height: 480)
        .modelContainer(container)
}

#Preview("Free find") {
    let config = ModelConfiguration(isStoredInMemoryOnly: true)
    let container = try! ModelContainer(for: ScanResult.self, configurations: config)
    let result = ScanResult(
        itemName: "Patagonia Better Sweater",
        brand: "Patagonia", category: "Clothing",
        conditionNotes: "Good", valueLow: 65, valueHigh: 95,
        confidence: "high", soldListingsCount: 31,
        listingTitle: "", listingDescription: "",
        paidPrice: 0
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
