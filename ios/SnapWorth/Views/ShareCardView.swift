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
///
/// Both the `CIContext` and the finished image are cached. This is called from
/// three card bodies, each of which re-renders on every keystroke behind the
/// share-card debounce and on every condition tap — and it was building a fresh
/// `CIContext` each time. A `CIContext` allocates a Metal command queue and its
/// backing caches; it is explicitly the object Core Image documents as
/// expensive to create and intended to be reused. The QR itself encodes a
/// constant URL, so it never needed re-rendering at all.
func snapShareCardQR(_ urlString: String = Config.appStoreURL) -> UIImage? {
    if let cached = QRCache.cached(urlString) { return cached }
    guard let data = urlString.data(using: .utf8),
          let filter = CIFilter(name: "CIQRCodeGenerator") else { return nil }
    filter.setValue(data, forKey: "inputMessage")
    filter.setValue("M", forKey: "inputCorrectionLevel")
    guard let ci = filter.outputImage else { return nil }
    let scaled = ci.transformed(by: CGAffineTransform(scaleX: 8, y: 8))
    guard let cg = QRCache.context.createCGImage(scaled, from: scaled.extent) else { return nil }
    let image = UIImage(cgImage: cg)
    QRCache.store(image, for: urlString)
    return image
}

/// Lock-guarded rather than actor-isolated: `snapShareCardQR` is called from
/// plain computed properties inside card bodies, which carry no isolation of
/// their own.
private enum QRCache {
    static let context = CIContext()

    private static let lock = NSLock()
    /// Keyed by URL; in practice one entry, for `Config.appStoreURL`.
    nonisolated(unsafe) private static var images: [String: UIImage] = [:]

    static func cached(_ key: String) -> UIImage? {
        lock.lock(); defer { lock.unlock() }
        return images[key]
    }

    static func store(_ image: UIImage, for key: String) {
        lock.lock(); defer { lock.unlock() }
        images[key] = image
    }
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
                Text(printedDollars(paid) == 0 ? "Free →" : "Paid \(fmtCurrency(paid)) →")
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

    /// `displayValueLow`, not `valueLow`: the range printed directly above this
    /// badge is condition-adjusted, and the badge was dividing the raw AI
    /// baseline. On anything the user had re-graded, the card claimed a
    /// multiple its own headline did not support — and the share card is the
    /// artefact that leaves the app.
    func findBadge(paid: Double) -> String? {
        // Both numbers as the card *prints* them, not as they are stored.
        // `snapCurrency` has `maximumFractionDigits = 0`, so a badge divided
        // out of the stored values is a claim about figures that appear
        // nowhere on the card: $0.50 paid printed "Paid $0" beside a "90x
        // find", and $1.50 printed "Paid $2" beside the 30x taken from 1.50.
        // The `== 0` test has to move with it, or a paid price the card prints
        // as $0 escapes the free branch and becomes the divisor of a multiple.
        let paidShown = printedDollars(paid)
        let low = printedDollars(result.displayValueLow)
        if paidShown == 0 { return "Free find" }
        guard paidShown < low else { return nil }
        // `floor`, not `round`. Round-to-nearest made the threshold for an
        // "Nx find" claim `low/paid >= N - 0.5`, so the very first badge a user
        // can earn was already wrong: a 1.5x find rendered as "2x find", and a
        // 2.5x as "3x" (Swift rounds half away from zero). The badge sits 6pt
        // under the headline range and 6pt under the "Paid $X" line, so the
        // card printed the two numbers that disprove its own claim — on the
        // artefact that leaves the app.
        //
        // The same class of defect as the divisor bug fixed in the comment
        // above: that corrected which number to divide, and left the rounding.
        let multiple = Int((low / paidShown).rounded(.down))
        return multiple > 1 ? "\(multiple)x find" : nil
    }

    private func fmtCurrency(_ value: Double) -> String {
        NumberFormatter.snapCurrency.string(from: NSNumber(value: value)) ?? "$\(Int(value))"
    }

    /// A money figure as this card prints it.
    ///
    /// `NumberFormatter.snapCurrency` has `maximumFractionDigits = 0`, so
    /// anything measured against a figure the card shows has to lose its cents
    /// the same way first. Half-even because that is `NumberFormatter`'s own
    /// default rounding: $22.50 lands on the $22 the card prints, not on $23.
    private func printedDollars(_ value: Double) -> Double {
        value.rounded(.toNearestOrEven)
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
                        .snapSymbol(72)
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

// MARK: - "Guess the price" (#95)

/// The "guess before the estimate" preference: when on, a result opens with
/// its value covered and reveals on a tap. On by default — the moment before
/// the number is the fun part — and one switch in Settings for people who
/// scan fifty things an afternoon and just want the number.
enum GuessFirst {
    static let key = "snapworth_guess_first"
    static let defaultOn = true
}

/// Which of the pair a `GuessShareCardView` renders.
enum GuessCardStyle {
    /// The question: photo, what was paid, and the estimate covered.
    case guess
    /// The answer: the estimate, labelled as an AI estimate, with the QR.
    case reveal
}

/// How a typed guess compares to the estimate, in the words the reveal shows.
///
/// Pure, so the copy is testable. Inside the range is a win; outside says by
/// how much, against the nearer end — "under the low end" rather than a
/// distance to some midpoint the user never saw.
enum GuessScoring {
    /// Scored against the bounds **as printed**, not the raw ones.
    ///
    /// The range the user is looking at is whole dollars — `formattedRange`
    /// runs through `snapCurrency`, which drops the fraction — while the
    /// bounds handed in here are fractional as a matter of course
    /// (`priceRange(for:)` scales the stored values by a condition factor).
    /// Scoring the raw values against a printed range produced a verdict that
    /// contradicted the card above it, in two ways at once:
    ///
    ///   * a guess of $45 against a printed "$45–$90" whose real low is 45.40
    ///     was *outside* the range, and the miss — 40 cents — formatted through
    ///     a 0-decimal formatter as **"$0 under the low end."** A non-zero miss
    ///     reported as zero, under a range the guess appears to match exactly.
    ///   * and the reverse: a guess of $45 against a real low of 44.60 printed
    ///     "$45" too, so two guesses the user cannot tell apart got different
    ///     verdicts.
    ///
    /// Rounding the bounds first makes the verdict answer the question the
    /// user actually asked — "did I match the number on the screen?" — and
    /// makes "$0 under" unreachable rather than merely unlikely: any guess
    /// within half a dollar of a bound now lands inside it.
    static func verdict(guess: Double, low: Double, high: Double) -> String {
        // The same rounding `snapCurrency` applies to the range on screen.
        let lo = min(low, high).rounded(), hi = max(low, high).rounded()
        let guessed = guess.rounded()
        if guessed >= lo && guessed <= hi {
            return "Spot on — your guess is inside the estimate."
        }
        if guessed < lo {
            return "\(money(lo - guessed)) under the low end."
        }
        return "\(money(guessed - hi)) over the high end."
    }

    /// Parses what the user typed: digits with an optional decimal separator,
    /// currency symbols ignored. Nil when it is not a number.
    ///
    /// Shares `MoneyInput`'s separator rules. This used to strip the comma
    /// outright, which read a comma-decimal keypad's "12,50" as 1250 — a
    /// hundredfold-wrong guess scored against the estimate, and worse than
    /// refusing the input. It still reads "$1,250" as 1250.
    static func parse(_ text: String) -> Double? {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        guard !trimmed.hasPrefix("-") else { return nil }
        guard let value = MoneyInput.parse(trimmed), value.isFinite, value >= 0 else { return nil }
        return value
    }

    private static func money(_ value: Double) -> String {
        NumberFormatter.snapCurrency.string(from: NSNumber(value: value.rounded())) ?? "$\(Int(value.rounded()))"
    }
}

/// The two story cards. Same 540×960 brand canvas and fixed colours as
/// `ShareCardView`, so they sit beside it in a story without a seam.
struct GuessShareCardView: View {
    let result: ScanResult
    let photo: UIImage?
    let style: GuessCardStyle

    static let cardWidth:  CGFloat = 540
    static let cardHeight: CGFloat = 960
    private let sidePad:  CGFloat = 24
    private let innerPad: CGFloat = 36

    var body: some View {
        VStack(spacing: 0) {
            photoSection
                .padding(.horizontal, sidePad)
                .padding(.top, 28)

            if let paid = result.paidPrice {
                Text(paid == 0 ? "Free find" : "Paid \(fmtCurrency(paid))")
                    .font(Font.dmSans(17, weight: .semibold))
                    .foregroundStyle(Color(hex: "8B7D71"))
                    .lineLimit(1)
                    .padding(.top, 22)
            }

            switch style {
            case .guess:
                Text("Guess what it could resell for 👇")
                    .font(Font.fraunces(30, weight: .bold))
                    .foregroundStyle(Color(hex: "2B211C"))
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
                    .minimumScaleFactor(0.7)
                    .padding(.top, result.paidPrice == nil ? 24 : 10)
                    .padding(.horizontal, innerPad)

                // The covered estimate. Solid, not blurred: a blur of the real
                // number can be read back by anyone who tries.
                RoundedRectangle(cornerRadius: 20, style: .continuous)
                    .fill(Color(hex: "EFE6DC"))
                    .frame(height: 92)
                    .overlay(
                        Text("$ ? ? ?")
                            .font(Font.fraunces(46, weight: .bold))
                            .foregroundStyle(Color(hex: "8B7D71").opacity(0.55))
                    )
                    .padding(.top, 16)
                    .padding(.horizontal, innerPad + 24)

            case .reveal:
                Text(result.formattedRange)
                    .font(Font.fraunces(56, weight: .bold))
                    .foregroundStyle(Color(hex: "6F8F6B"))
                    .minimumScaleFactor(0.45)
                    .lineLimit(1)
                    .padding(.top, result.paidPrice == nil ? 24 : 6)
                    .padding(.horizontal, innerPad)
                Text("AI resale estimate")
                    .font(Font.dmSans(15, weight: .medium))
                    .foregroundStyle(Color(hex: "8B7D71"))
                    .padding(.top, 2)
            }

            Text(result.itemName)
                .font(Font.fraunces(24, weight: .semibold))
                .foregroundStyle(Color(hex: "2B211C"))
                .multilineTextAlignment(.center)
                .lineLimit(2)
                .truncationMode(.tail)
                .minimumScaleFactor(0.7)
                .padding(.top, 14)
                .padding(.horizontal, innerPad)

            Spacer()

            Rectangle()
                .fill(Color(hex: "EFE6DC"))
                .frame(height: 1)
                .padding(.horizontal, innerPad)

            HStack(spacing: 14) {
                // The QR only on the reveal: the question card is the hook,
                // and a download prompt on it gives the game away.
                if style == .reveal, let qr = snapShareCardQR() {
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
                    Text(style == .reveal ? "Get SnapWorth" : "Answer on the next slide")
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

    private func fmtCurrency(_ value: Double) -> String {
        NumberFormatter.snapCurrency.string(from: NSNumber(value: value)) ?? "$\(Int(value))"
    }

    @ViewBuilder
    private var photoSection: some View {
        let w = Self.cardWidth - sidePad * 2
        if let photo {
            Image(uiImage: photo)
                .resizable()
                .scaledToFill()
                .frame(width: w, height: 440)
                .clipShape(RoundedRectangle(cornerRadius: 28, style: .continuous))
        } else {
            RoundedRectangle(cornerRadius: 28, style: .continuous)
                .fill(Color(hex: "EFE6DC"))
                .frame(width: w, height: 440)
                .overlay(
                    Image(systemName: "photo")
                        .snapSymbol(72)
                        .foregroundStyle(Color(hex: "8B7D71").opacity(0.4))
                )
        }
    }
}

// MARK: - Previews

#Preview("Unpaid — no photo") {
    let config = ModelConfiguration(isStoredInMemoryOnly: true)
    // swiftlint:disable:next force_try — preview-only in-memory container, never ships
    let container = try! ModelContainer(for: ScanResult.self, configurations: config)
    let result = ScanResult(
        itemName: "Levi's 501 Original Jeans",
        brand: "Levi's", category: "Clothing",
        conditionNotes: "Good", valueLow: 45, valueHigh: 90,
        confidence: "high", soldListingsCount: 0,
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
    // swiftlint:disable:next force_try — preview-only in-memory container, never ships
    let container = try! ModelContainer(for: ScanResult.self, configurations: config)
    let result = ScanResult(
        itemName: "Off-White OOO Out of Office Sneakers",
        brand: "Off-White", category: "Shoes",
        conditionNotes: "Good", valueLow: 275, valueHigh: 475,
        confidence: "high", soldListingsCount: 0,
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
    // swiftlint:disable:next force_try — preview-only in-memory container, never ships
    let container = try! ModelContainer(for: ScanResult.self, configurations: config)
    let result = ScanResult(
        itemName: "Patagonia Better Sweater",
        brand: "Patagonia", category: "Clothing",
        conditionNotes: "Good", valueLow: 65, valueHigh: 95,
        confidence: "high", soldListingsCount: 0,
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
    // swiftlint:disable:next force_try — preview-only in-memory container, never ships
    let container = try! ModelContainer(for: ScanResult.self, configurations: config)
    let result = ScanResult(
        itemName: "Vintage Tommy Hilfiger Oversized Windbreaker Jacket Navy Blue Size XXL",
        brand: "Tommy Hilfiger", category: "Clothing",
        conditionNotes: "Good", valueLow: 1200, valueHigh: 1600,
        confidence: "medium", soldListingsCount: 0,
        listingTitle: "", listingDescription: ""
    )
    container.mainContext.insert(result)
    return ShareCardView(result: result, photo: nil)
        .scaleEffect(0.5, anchor: .top)
        .frame(width: 270, height: 480)
        .modelContainer(container)
}
