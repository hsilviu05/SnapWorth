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
    static func verdict(guess: Double, low: Double, high: Double) -> String {
        let lo = min(low, high), hi = max(low, high)
        if guess >= lo && guess <= hi {
            return "Spot on — your guess is inside the estimate."
        }
        if guess < lo {
            return "\(money(lo - guess)) under the low end."
        }
        return "\(money(guess - hi)) over the high end."
    }

    /// Parses what the user typed: digits with an optional decimal point,
    /// currency symbols and grouping ignored. Nil when it is not a number.
    static func parse(_ text: String) -> Double? {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        guard !trimmed.hasPrefix("-") else { return nil }
        let cleaned = trimmed.filter { $0.isNumber || $0 == "." }
        guard !cleaned.isEmpty, let value = Double(cleaned), value.isFinite, value >= 0 else { return nil }
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

/// The in-app game: guess first, tap to reveal, then share the pair.
///
/// The reveal is a spring — the covered number scales up and un-blurs while
/// the cover pill falls away — with a success haptic. Reduce Motion gets the
/// state change as a cross-fade (`snapAnimation` drops the movement), and
/// VoiceOver gets one element that reads as a button until revealed.
struct GuessThePriceSheet: View {
    let result: ScanResult
    let photo: UIImage?

    @Environment(\.dismiss) private var dismiss
    @Environment(\.displayScale) private var displayScale
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var vm = ResultViewModel()
    @State private var guessText = ""
    @State private var revealed = false
    @State private var shareItems: [UIImage]?
    @State private var shareStyle = "pair"
    @State private var showShare = false
    @FocusState private var guessFocused: Bool

    private var guess: Double? { GuessScoring.parse(guessText) }

    private var verdict: String? {
        guard revealed, let guess else { return nil }
        return GuessScoring.verdict(guess: guess, low: result.displayValueLow, high: result.displayValueHigh)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 18) {
                    if let photo {
                        Image(uiImage: photo)
                            .resizable()
                            .scaledToFill()
                            .frame(height: 240)
                            .frame(maxWidth: .infinity)
                            .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
                            .accessibilityHidden(true)
                    }

                    Text(result.itemName)
                        .font(.fraunces(22, weight: .semibold, relativeTo: .title2))
                        .foregroundStyle(Color.snapEspresso)
                        .multilineTextAlignment(.center)
                        .lineLimit(2)

                    if let paid = result.paidPrice {
                        Text(paid == 0 ? "Free find" : "You paid \(fmtCurrency(paid))")
                            .font(.snapCaption)
                            .foregroundStyle(Color.snapWarmGray)
                    }

                    if !revealed {
                        HStack(spacing: 6) {
                            Text("$")
                                .font(.dmSans(17, weight: .medium))
                                .foregroundStyle(Color.snapWarmGray)
                                .accessibilityHidden(true)
                            TextField("Your guess (optional)", text: $guessText)
                                .keyboardType(.decimalPad)
                                .font(.dmSans(17, weight: .medium))
                                .foregroundStyle(Color.snapEspresso)
                                .focused($guessFocused)
                                .accessibilityLabel("Your guess in dollars, optional")
                        }
                        .padding(16)
                        .background(Color.snapCard)
                        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                    }

                    estimateReveal

                    if let verdict {
                        Text(verdict)
                            .font(.dmSans(16, weight: .semibold))
                            .foregroundStyle(Color.snapEspresso)
                            .multilineTextAlignment(.center)
                            .transition(.opacity)
                    }
                    if revealed {
                        Text("An AI estimate, not a sold price.")
                            .font(.snapCaption)
                            .foregroundStyle(Color.snapWarmGray)
                    }

                    VStack(spacing: 10) {
                        shareButton("Share as a story — question, then reveal", style: "pair")
                        shareButton("Share the question only", style: "guess")
                    }
                    .padding(.top, 6)
                }
                .padding(20)
            }
            .scrollDismissesKeyboard(.interactively)
            .background(Color.snapBackground)
            .navigationTitle("Guess the price")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") { dismiss() }
                        .font(.dmSans(15, weight: .semibold))
                        .foregroundStyle(Color.snapTerracotta)
                }
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") { guessFocused = false }
                        .font(.dmSans(15, weight: .semibold))
                        .foregroundStyle(Color.snapTerracotta)
                }
            }
            .sheet(isPresented: $showShare) {
                if let items = shareItems {
                    ActivityShareSheet(items: items) { _ in
                        Analytics.shared.track(.guessCardShared(style: shareStyle))
                    }
                }
            }
        }
    }

    // MARK: - The reveal

    private var estimateReveal: some View {
        ZStack {
            Text(result.formattedRange)
                .font(.fraunces(44, weight: .bold, relativeTo: .largeTitle))
                .foregroundStyle(Color.snapSage)
                .minimumScaleFactor(0.5)
                .lineLimit(1)
                .blur(radius: revealed ? 0 : 16)
                .opacity(revealed ? 1 : 0.35)
                .scaleEffect(revealed ? 1 : 0.9)
                .padding(.horizontal, 24)
                .accessibilityHidden(!revealed)

            if !revealed {
                Text("Tap to reveal")
                    .font(.dmSans(15, weight: .semibold))
                    .foregroundStyle(Color.snapOnAccent)
                    .padding(.horizontal, 18)
                    .padding(.vertical, 10)
                    .background(Color.snapTerracotta)
                    .clipShape(Capsule())
                    .transition(.scale(scale: 0.7).combined(with: .opacity))
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 28)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .contentShape(Rectangle())
        .onTapGesture { reveal() }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(revealed ? "AI estimate \(result.formattedRange)" : "Tap to reveal the AI estimate")
        .accessibilityAddTraits(revealed ? [] : .isButton)
    }

    private func reveal() {
        guard !revealed else { return }
        guessFocused = false
        // Reduce Motion: the same state change as a plain cross-fade — no
        // spring, no scale — which is what the covered number and the pill's
        // transition fall back to without an explicit animation.
        withAnimation(reduceMotion ? .easeInOut(duration: 0.2)
                                   : .spring(response: 0.45, dampingFraction: 0.62)) {
            revealed = true
        }
        Haptics.success()
        Analytics.shared.track(.guessRevealed(withGuess: guess != nil))
    }

    // MARK: - Sharing

    private func shareButton(_ title: String, style: String) -> some View {
        Button {
            let cards = vm.renderGuessCards(result: result, photo: photo, displayScale: displayScale)
            var items: [UIImage] = []
            if let g = cards.guess { items.append(g) }
            if style == "pair", let r = cards.reveal { items.append(r) }
            guard !items.isEmpty else { return }
            Haptics.light()
            shareStyle = style
            shareItems = items
            showShare = true
        } label: {
            Text(title)
                .font(.dmSans(15, weight: .semibold))
                .foregroundStyle(style == "pair" ? Color.snapOnAccent : Color.snapEspresso)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
                .background(style == "pair" ? Color.snapTerracotta : Color.snapCard)
                .clipShape(Capsule())
        }
        .buttonStyle(.plain)
        .snapHitTarget()
    }

    private func fmtCurrency(_ value: Double) -> String {
        NumberFormatter.snapCurrency.string(from: NSNumber(value: value)) ?? "$\(Int(value))"
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
