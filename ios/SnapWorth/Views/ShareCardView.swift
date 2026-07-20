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

/// QR code for the App Store link, shared by every branded card so the footer
/// is identical everywhere.
func snapShareCardQR(_ urlString: String = Config.appStoreURL) -> UIImage? {
    guard let data = urlString.data(using: .utf8),
          let filter = CIFilter(name: "CIQRCodeGenerator") else { return nil }
    filter.setValue(data, forKey: "inputMessage")
    filter.setValue("M", forKey: "inputCorrectionLevel")
    guard let ci = filter.outputImage else { return nil }
    let scaled = ci.transformed(by: CGAffineTransform(scaleX: 8, y: 8))
    guard let cg = CIContext().createCGImage(scaled, from: scaled.extent) else { return nil }
    return UIImage(cgImage: cg)
}

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

    private var qrImage: UIImage? { snapShareCardQR() }

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

// MARK: - "Share my month" card

/// Same fixed 540×960 brand canvas, QR + footer rules as `ShareCardView` —
/// renders the month's realized profit, items sold and best flip. Only ever
/// shown for a month that actually has sold items (caller guards this).
struct MonthShareCardView: View {
    let monthTitle: String          // e.g. "July 2026"
    let realizedProfit: Decimal
    let itemsSold: Int
    let bestFlipName: String?
    let bestFlipProfit: Decimal?

    static let cardWidth:  CGFloat = 540
    static let cardHeight: CGFloat = 960
    private let innerPad: CGFloat = 36

    private var isProfit: Bool { realizedProfit >= 0 }
    private var accent: Color { isProfit ? Color(hex: "6F8F6B") : Color(hex: "C4562F") }

    var body: some View {
        VStack(spacing: 0) {
            Spacer().frame(height: 104)

            Text("MY FLIPS · \(monthTitle.uppercased())")
                .font(Font.dmSans(16, weight: .bold))
                .tracking(2)
                .foregroundStyle(Color(hex: "8B7D71"))
                .multilineTextAlignment(.center)
                .padding(.horizontal, innerPad)

            Text(signed(realizedProfit))
                .font(Font.fraunces(84, weight: .bold))
                .foregroundStyle(accent)
                .minimumScaleFactor(0.4)
                .lineLimit(1)
                .padding(.top, 24)
                .padding(.horizontal, innerPad)

            Text(isProfit ? "profit this month" : "net this month")
                .font(Font.dmSans(18))
                .foregroundStyle(Color(hex: "8B7D71"))
                .padding(.top, 6)

            Spacer().frame(height: 64)

            HStack(alignment: .top, spacing: 0) {
                statBlock(value: "\(itemsSold)", label: itemsSold == 1 ? "item sold" : "items sold")
                if let name = bestFlipName, let profit = bestFlipProfit {
                    Rectangle().fill(Color(hex: "EFE6DC")).frame(width: 1, height: 72)
                    statBlock(value: signed(profit), label: "best flip", caption: name)
                }
            }
            .padding(.horizontal, innerPad)

            Spacer()

            Rectangle()
                .fill(Color(hex: "EFE6DC"))
                .frame(height: 1)
                .padding(.horizontal, innerPad)

            HStack(spacing: 14) {
                if let qr = snapShareCardQR() {
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
        .frame(width: Self.cardWidth, height: Self.cardHeight)
        .background(Color(hex: "FBF7F2"))
    }

    @ViewBuilder
    private func statBlock(value: String, label: String, caption: String? = nil) -> some View {
        VStack(spacing: 4) {
            Text(value)
                .font(Font.fraunces(30, weight: .bold))
                .foregroundStyle(Color(hex: "2B211C"))
                .lineLimit(1)
                .minimumScaleFactor(0.5)
            Text(label)
                .font(Font.dmSans(14))
                .foregroundStyle(Color(hex: "8B7D71"))
            if let caption {
                Text(caption)
                    .font(Font.dmSans(12))
                    .foregroundStyle(Color(hex: "8B7D71"))
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .padding(.horizontal, 10)
            }
        }
        .frame(maxWidth: .infinity)
    }

    private func signed(_ d: Decimal) -> String {
        let money = NumberFormatter.snapCurrency.string(from: NSDecimalNumber(decimal: abs(d))) ?? "$0"
        return d < 0 ? "−\(money)" : "+\(money)"
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
