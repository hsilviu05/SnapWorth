import SwiftUI

// ═══════════════════════════════════════════════════════════════════
// MARK: - Color Palette
// ═══════════════════════════════════════════════════════════════════

extension Color {
    // Backgrounds
    static let snapBackground  = Color(hex: "FBF7F2")  // warm cream
    static let snapCard        = Color.white

    // Accents
    static let snapTerracotta  = Color(hex: "D96C47")  // primary CTA
    static let snapSage        = Color(hex: "6F8F6B")  // money / positive values
    static let snapAmber       = Color(hex: "EBB868")  // badges / highlights

    // Text
    static let snapEspresso    = Color(hex: "2B211C")  // primary text
    static let snapWarmGray    = Color(hex: "8B7D71")  // secondary text

    // Borders / dividers
    static let snapBorder      = Color(hex: "EFE6DC")

    // Camera screen background
    static let snapCharcoal    = Color(hex: "1C1714")

    // Card shadow colour (rgba 120,80,50,0.08)
    static let snapCardShadow  = Color(red: 120/255, green: 80/255, blue: 50/255)

    // ── Hex initialiser ──────────────────────────────────────────────
    init(hex: String) {
        let hex = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var int: UInt64 = 0
        Scanner(string: hex).scanHexInt64(&int)
        let r, g, b: Double
        switch hex.count {
        case 6:
            r = Double((int >> 16) & 0xFF) / 255
            g = Double((int >>  8) & 0xFF) / 255
            b = Double((int      ) & 0xFF) / 255
        default:
            r = 1; g = 1; b = 1
        }
        self.init(red: r, green: g, blue: b)
    }
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - Typography
// ═══════════════════════════════════════════════════════════════════
// Fraunces (serif, for headlines & numbers) and DM Sans (for body/UI)
// are bundled in the Fonts/ directory and registered via UIAppFonts.
// SF fallbacks are used if the font files are missing.

extension Font {
    // ── Fraunces ─────────────────────────────────────────────────────
    static func fraunces(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        let postscriptName: String
        switch weight {
        case .bold, .heavy, .black: postscriptName = "Fraunces-Bold"
        case .semibold:             postscriptName = "Fraunces-SemiBold"
        default:                    postscriptName = "Fraunces-Regular"
        }
        // Fall back to system serif if the font file isn't bundled yet
        if UIFont(name: postscriptName, size: size) != nil {
            return .custom(postscriptName, size: size)
        }
        return .system(size: size, weight: weight, design: .serif)
    }

    // ── DM Sans (variable font — loaded via descriptor, cached) ─────────────
    static func dmSans(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        let uiWeight: UIFont.Weight
        switch weight {
        case .bold, .heavy, .black: uiWeight = .bold
        case .semibold:             uiWeight = .semibold
        case .medium:               uiWeight = .medium
        default:                    uiWeight = .regular
        }
        let key = "\(size)-\(uiWeight.rawValue)" as NSString
        if let cached = _dmSansCache.object(forKey: key) {
            return Font(cached)
        }
        let desc = UIFontDescriptor(fontAttributes: [.family: "DM Sans"])
            .addingAttributes([.traits: [UIFontDescriptor.TraitKey.weight: uiWeight.rawValue]])
        let uiFont = UIFont(descriptor: desc, size: size)
        if uiFont.familyName.lowercased().contains("dm sans") {
            _dmSansCache.setObject(uiFont, forKey: key)
            return Font(uiFont)
        }
        return .system(size: size, weight: weight, design: .default)
    }

    // ── Convenience aliases ───────────────────────────────────────────
    static let snapHeadline   = fraunces(28, weight: .bold)
    static let snapTitle      = fraunces(22, weight: .semibold)
    static let snapValueHero  = fraunces(44, weight: .bold)    // the "$45–$90" moment
    static let snapBody       = dmSans(15)
    static let snapBodyMedium = dmSans(15, weight: .medium)
    static let snapCaption    = dmSans(13)
    static let snapLabel      = dmSans(13, weight: .semibold)
    static let snapButton     = dmSans(17, weight: .semibold)
}

// UIFont cache for DM Sans — avoids descriptor allocation on every render
private let _dmSansCache = NSCache<NSString, UIFont>()

// Shared currency formatter — NumberFormatter is expensive to allocate
extension NumberFormatter {
    static let snapCurrency: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .currency
        f.currencyCode = "USD"
        f.locale = Locale(identifier: "en_US")
        f.maximumFractionDigits = 0
        return f
    }()
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - View Modifiers
// ═══════════════════════════════════════════════════════════════════

struct SnapCardModifier: ViewModifier {
    func body(content: Content) -> some View {
        content
            .background(Color.snapCard)
            .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
            .shadow(
                color: Color.snapCardShadow.opacity(0.08),
                radius: 24, x: 0, y: 8
            )
    }
}

extension View {
    func snapCard() -> some View { modifier(SnapCardModifier()) }

    func snapSectionHeader() -> some View {
        self
            .font(.snapLabel)
            .foregroundStyle(Color.snapWarmGray)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - Primary Button
// ═══════════════════════════════════════════════════════════════════

struct PrimaryButton: View {
    let title: String
    var isLoading: Bool = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ZStack {
                if isLoading {
                    ProgressView()
                        .tint(Color.snapBackground)
                } else {
                    Text(title)
                        .font(.snapButton)
                        .foregroundStyle(Color.snapBackground)
                }
            }
            .frame(maxWidth: .infinity)
            .frame(height: 56)
            .background(Color.snapTerracotta)
            .clipShape(Capsule())
        }
        .disabled(isLoading)
        .animation(.easeInOut(duration: 0.2), value: isLoading)
    }
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - Ghost Button
// ═══════════════════════════════════════════════════════════════════

struct GhostButton: View {
    let title: String
    var isLoading: Bool = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ZStack {
                if isLoading {
                    ProgressView()
                        .tint(Color.snapTerracotta)
                } else {
                    Text(title)
                        .font(.snapButton)
                        .foregroundStyle(Color.snapTerracotta)
                }
            }
            .frame(maxWidth: .infinity)
            .frame(height: 56)
            .overlay(
                Capsule()
                    .strokeBorder(Color.snapTerracotta, lineWidth: 1.5)
            )
        }
        .disabled(isLoading)
    }
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - Chip
// ═══════════════════════════════════════════════════════════════════

struct ChipView: View {
    let label: String
    var color: Color = Color.snapBorder
    var textColor: Color = Color.snapEspresso

    var body: some View {
        Text(label)
            .font(.snapLabel)
            .foregroundStyle(textColor)
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(color)
            .clipShape(Capsule())
    }
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - Value Range Hero
// ═══════════════════════════════════════════════════════════════════

struct ValueRangeView: View {
    let low: Double
    let high: Double

    private var formatted: String {
        guard low.isFinite && high.isFinite else { return "Price unavailable" }
        let fmt = NumberFormatter.snapCurrency
        let lo = fmt.string(from: NSNumber(value: low))  ?? "$\(Int(low))"
        let hi = fmt.string(from: NSNumber(value: high)) ?? "$\(Int(high))"
        return "\(lo)–\(hi)"
    }

    var body: some View {
        Text(formatted)
            .font(.snapValueHero)
            .foregroundStyle(Color.snapSage)
            .minimumScaleFactor(0.6)
            .lineLimit(1)
    }
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - Confidence Badge
// ═══════════════════════════════════════════════════════════════════

struct ConfidenceBadge: View {
    let confidence: String

    private var accentColor: Color {
        switch confidence.lowercased() {
        case "high":   return Color.snapSage
        case "medium": return Color.snapAmber
        default:       return Color.snapTerracotta
        }
    }

    var body: some View {
        Text("\(confidence) confidence")
            .font(.snapLabel)
            .foregroundStyle(Color.snapEspresso)
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(accentColor.opacity(0.18))
            .clipShape(Capsule())
            .overlay(
                Capsule()
                    .strokeBorder(accentColor, lineWidth: 1)
            )
    }
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - Shimmer Effect
// ═══════════════════════════════════════════════════════════════════

struct ShimmerModifier: ViewModifier {
    @State private var phase: CGFloat = -1

    func body(content: Content) -> some View {
        content
            .overlay(
                GeometryReader { geo in
                    LinearGradient(
                        stops: [
                            .init(color: .clear, location: 0),
                            .init(color: Color.white.opacity(0.45), location: 0.4),
                            .init(color: Color.white.opacity(0.65), location: 0.5),
                            .init(color: Color.white.opacity(0.45), location: 0.6),
                            .init(color: .clear, location: 1),
                        ],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                    .frame(width: geo.size.width * 2)
                    .offset(x: phase * geo.size.width * 2)
                }
                .clipped()
            )
            .onAppear {
                withAnimation(.linear(duration: 1.4).repeatForever(autoreverses: false)) {
                    phase = 1
                }
            }
    }
}

extension View {
    func shimmering() -> some View { modifier(ShimmerModifier()) }
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - Analyzing Overlay  (shimmer + rotating copy)
// ═══════════════════════════════════════════════════════════════════

struct AnalyzingOverlay: View {
    @State private var messageIndex = 0
    @State private var opacity: Double = 1
    @State private var rotationTask: Task<Void, Never>?

    private let messages = [
        "Reading the label…",
        "Checking sold listings…",
        "Estimating resale value…",
        "Almost there…",
    ]

    var body: some View {
        ZStack {
            Color.snapCharcoal.opacity(0.72)
                .ignoresSafeArea()

            VStack(spacing: 20) {
                Circle()
                    .strokeBorder(Color.snapTerracotta, lineWidth: 2)
                    .frame(width: 72, height: 72)
                    .overlay(
                        Image(systemName: "sparkle")
                            .font(.system(size: 28, weight: .light))
                            .foregroundStyle(Color.snapTerracotta)
                            .shimmering()
                    )

                Text(messages[messageIndex])
                    .font(.dmSans(17, weight: .medium))
                    .foregroundStyle(Color.snapBackground)
                    .opacity(opacity)
                    .animation(.easeInOut(duration: 0.35), value: opacity)
            }
        }
        .onAppear {
            rotationTask = Task { @MainActor in
                while !Task.isCancelled {
                    try? await Task.sleep(for: .seconds(1.8))
                    guard !Task.isCancelled else { break }
                    withAnimation { opacity = 0 }
                    try? await Task.sleep(for: .seconds(0.4))
                    guard !Task.isCancelled else { break }
                    messageIndex = (messageIndex + 1) % messages.count
                    withAnimation { opacity = 1 }
                }
            }
        }
        .onDisappear { rotationTask?.cancel() }
    }
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - Plan Card  (for paywall)
// ═══════════════════════════════════════════════════════════════════

struct PlanCard: View {
    let title: String
    let price: String
    let priceDetail: String
    let badge: String?
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 14) {
                // Radio indicator
                ZStack {
                    Circle()
                        .strokeBorder(isSelected ? Color.snapTerracotta : Color.snapBorder, lineWidth: 2)
                        .frame(width: 22, height: 22)
                    if isSelected {
                        Circle()
                            .fill(Color.snapTerracotta)
                            .frame(width: 12, height: 12)
                    }
                }

                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(.dmSans(15, weight: .semibold))
                        .foregroundStyle(Color.snapEspresso)
                    Text(priceDetail)
                        .font(.snapCaption)
                        .foregroundStyle(Color.snapWarmGray)
                }

                Spacer()

                VStack(alignment: .trailing, spacing: 2) {
                    Text(price)
                        .font(.dmSans(17, weight: .bold))
                        .foregroundStyle(Color.snapEspresso)
                    if let badge {
                        Text(badge)
                            .font(.dmSans(10, weight: .semibold))
                            .foregroundStyle(Color.snapEspresso)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 3)
                            .background(Color.snapAmber)
                            .clipShape(Capsule())
                    }
                }
            }
            .padding(16)
            .background(Color.snapCard)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .strokeBorder(
                        isSelected ? Color.snapTerracotta : Color.snapBorder,
                        lineWidth: isSelected ? 2 : 1
                    )
            )
        }
        .buttonStyle(.plain)
        .animation(.spring(duration: 0.2), value: isSelected)
    }
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - Scan History Card  (2-col grid)
// ═══════════════════════════════════════════════════════════════════

struct ScanHistoryCard: View {
    let result: ScanResult
    var width: CGFloat = 160
    @State private var thumbnail: UIImage?
    @State private var imageLoadAttempted = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // Thumbnail
            Group {
                if let img = thumbnail {
                    Image(uiImage: img)
                        .resizable()
                        .scaledToFill()
                } else if imageLoadAttempted {
                    Rectangle()
                        .fill(Color.snapBorder.opacity(0.6))
                } else {
                    Rectangle()
                        .fill(Color.snapBorder.opacity(0.6))
                        .shimmering()
                }
            }
            .frame(width: width - 24, height: 120)
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .task(id: result.id) {
                guard let data = result.imageData else {
                    imageLoadAttempted = true
                    return
                }
                thumbnail = await Task.detached(priority: .utility) {
                    UIImage(data: data)
                }.value
                imageLoadAttempted = true
            }

            Text(result.itemName)
                .font(.dmSans(13, weight: .medium))
                .foregroundStyle(Color.snapEspresso)
                .lineLimit(2)
                .frame(width: width - 24, alignment: .leading)

            Text(result.formattedRange)
                .font(.fraunces(16, weight: .bold))
                .foregroundStyle(Color.snapSage)
        }
        .padding(12)
        .frame(width: width)
        .snapCard()
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(result.itemName), estimated \(result.formattedRange)")
        .accessibilityAddTraits(.isButton)
    }
}
