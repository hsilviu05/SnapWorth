import SwiftUI
import UIKit

// ═══════════════════════════════════════════════════════════════════
// MARK: - Color Palette
// ═══════════════════════════════════════════════════════════════════
// The palette is defined once as light/dark pairs and resolved per trait
// collection at render time. Call sites keep using the same `Color.snapX`
// names they always have — they simply became theme-aware.
//
// The warm identity is preserved in dark mode by *re-grounding* rather than
// inverting: espresso becomes the surface, cream becomes the ink, and the
// accents are lifted just enough to hold contrast on a dark ground.

extension Color {
    /// Resolves per interface style at render time, so a single token serves
    /// both themes without any view needing to know which one is active.
    /// Also honours Increased Contrast when a higher-contrast pair is given.
    static func snapAdaptive(
        light: Color,
        dark: Color,
        lightHighContrast: Color? = nil,
        darkHighContrast: Color? = nil
    ) -> Color {
        Color(UIColor { traits in
            let increased = traits.accessibilityContrast == .high
            switch (traits.userInterfaceStyle, increased) {
            case (.dark, true):  return UIColor(darkHighContrast ?? dark)
            case (.dark, false): return UIColor(dark)
            case (_, true):      return UIColor(lightHighContrast ?? light)
            default:             return UIColor(light)
            }
        })
    }

    // Backgrounds
    static let snapBackground = snapAdaptive(
        light: Color(hex: "FBF7F2"),          // warm cream
        dark:  Color(hex: "17120F")           // deep espresso ground
    )
    static let snapCard = snapAdaptive(
        light: .white,
        dark:  Color(hex: "221B17")           // raised warm surface
    )

    // Accents — lifted in dark so they stay legible on a dark ground
    static let snapTerracotta = snapAdaptive(
        light: Color(hex: "D96C47"), dark: Color(hex: "E8845F"),
        lightHighContrast: Color(hex: "BE5433")
    )
    static let snapSage = snapAdaptive(     // money / positive values
        light: Color(hex: "6F8F6B"), dark: Color(hex: "8FB08A"),
        lightHighContrast: Color(hex: "4F6E4B")
    )
    static let snapAmber = snapAdaptive(    // badges / highlights
        light: Color(hex: "EBB868"), dark: Color(hex: "E5BE7C")
    )

    // Text — `snapWarmGray` was 3.1:1 on cream (below WCAG AA); darkened to
    // 5.7:1 while keeping the warmth.
    static let snapEspresso = snapAdaptive(
        light: Color(hex: "2B211C"), dark: Color(hex: "F0E9E2"),
        lightHighContrast: Color(hex: "1A120E"), darkHighContrast: .white
    )
    static let snapWarmGray = snapAdaptive(
        light: Color(hex: "6E6055"), dark: Color(hex: "B0A297"),
        lightHighContrast: Color(hex: "544840"), darkHighContrast: Color(hex: "D6CCC3")
    )

    // Borders / dividers
    static let snapBorder = snapAdaptive(
        light: Color(hex: "EFE6DC"), dark: Color(hex: "342A24"),
        lightHighContrast: Color(hex: "D3C4B4"), darkHighContrast: Color(hex: "4C3E35")
    )

    /// Camera screen background — deliberately dark in *both* themes; the
    /// viewfinder is a dark surface by design, not by theme.
    static let snapCharcoal = Color(hex: "1C1714")

    /// Content that always sits on `snapCharcoal` (camera chrome). Fixed cream
    /// so it never inverts to dark-on-dark when the system theme flips.
    static let snapOnCharcoal = Color(hex: "FBF7F2")

    /// Content that always sits on a *filled accent* surface — primary button
    /// labels on terracotta. Fixed cream: the accent is dark enough in both
    /// themes that the label must not follow the theme.
    static let snapOnAccent = Color(hex: "FBF7F2")

    // Card shadow colour (rgba 120,80,50,0.08)
    static let snapCardShadow = Color(red: 120/255, green: 80/255, blue: 50/255)

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
    /// - Parameter style: the text style this size is anchored to. Everything
    ///   scales from it, so a user's Larger Text setting reaches the whole app.
    static func fraunces(
        _ size: CGFloat,
        weight: Font.Weight = .regular,
        relativeTo style: Font.TextStyle = .body
    ) -> Font {
        let effective = UIAccessibility.isBoldTextEnabled ? _bolder(weight) : weight
        let postscriptName: String
        switch effective {
        case .bold, .heavy, .black: postscriptName = "Fraunces-Bold"
        case .semibold:             postscriptName = "Fraunces-SemiBold"
        default:                    postscriptName = "Fraunces-Regular"
        }
        // Fall back to system serif if the font file isn't bundled yet
        if UIFont(name: postscriptName, size: size) != nil {
            return .custom(postscriptName, size: size, relativeTo: style)
        }
        return _scaled(
            UIFont.systemFont(ofSize: size, weight: _uiWeight(weight))
                .withDesign(.serif),
            relativeTo: style
        )
    }

    // ── DM Sans (variable font — resolved to a concrete face, cached) ───────
    static func dmSans(
        _ size: CGFloat,
        weight: Font.Weight = .regular,
        relativeTo style: Font.TextStyle = .body
    ) -> Font {
        // Bold Text accessibility setting: custom faces don't get it for free,
        // so step the requested weight up one notch.
        let effective = UIAccessibility.isBoldTextEnabled ? _bolder(weight) : weight
        if let name = _dmSansFontName(for: effective) {
            // `.custom(_:size:relativeTo:)` scales via the SwiftUI environment,
            // so `.dynamicTypeSize(...)` clamps are honoured. Do not substitute
            // UIFontMetrics here — it reads the global content size category and
            // silently ignores per-view clamps.
            return .custom(name, size: size, relativeTo: style)
        }
        return _scaled(UIFont.systemFont(ofSize: size, weight: _uiWeight(effective)),
                       relativeTo: style)
    }

    // ── Convenience aliases ───────────────────────────────────────────
    // Each is anchored to the text style that matches its role, so Dynamic Type
    // scales headlines and body copy at their correct respective rates.
    static let snapHeadline   = fraunces(28, weight: .bold,     relativeTo: .title)
    static let snapTitle      = fraunces(22, weight: .semibold, relativeTo: .title2)
    static let snapValueHero  = fraunces(44, weight: .bold,     relativeTo: .largeTitle)
    static let snapBody       = dmSans(15,                      relativeTo: .subheadline)
    static let snapBodyMedium = dmSans(15, weight: .medium,     relativeTo: .subheadline)
    static let snapCaption    = dmSans(13,                      relativeTo: .footnote)
    static let snapLabel      = dmSans(13, weight: .semibold,   relativeTo: .caption)
    static let snapButton     = dmSans(17, weight: .semibold,   relativeTo: .headline)
}

/// Resolved PostScript names for DM Sans, keyed by weight. Descriptor lookup is
/// comparatively expensive, so the resolved *name* is cached — not a sized font,
/// which would defeat Dynamic Type.
private let _dmSansNameCache = NSCache<NSString, NSString>()

private func _dmSansFontName(for weight: Font.Weight) -> String? {
    let uiWeight = _uiWeight(weight)
    let key = "\(uiWeight.rawValue)" as NSString
    if let cached = _dmSansNameCache.object(forKey: key) { return cached as String }

    let desc = UIFontDescriptor(fontAttributes: [.family: "DM Sans"])
        .addingAttributes([.traits: [UIFontDescriptor.TraitKey.weight: uiWeight.rawValue]])
    let uiFont = UIFont(descriptor: desc, size: 17)
    guard uiFont.familyName.lowercased().contains("dm sans") else { return nil }
    _dmSansNameCache.setObject(uiFont.fontName as NSString, forKey: key)
    return uiFont.fontName
}

/// Last-resort scaling for the system fallback face, used only when the bundled
/// font is missing. Anchors to the text style so it still tracks Dynamic Type.
private func _scaled(_ base: UIFont, relativeTo style: Font.TextStyle) -> Font {
    Font(UIFontMetrics(forTextStyle: style.uiTextStyle).scaledFont(for: base))
}

/// One step up the weight scale, for the Bold Text accessibility setting.
private func _bolder(_ weight: Font.Weight) -> Font.Weight {
    switch weight {
    case .regular, .light, .thin, .ultraLight: return .medium
    case .medium:                              return .semibold
    default:                                   return .bold
    }
}

private func _uiWeight(_ weight: Font.Weight) -> UIFont.Weight {
    switch weight {
    case .bold, .heavy, .black: return .bold
    case .semibold:             return .semibold
    case .medium:               return .medium
    default:                    return .regular
    }
}

private extension UIFont {
    /// Best-effort design variant; returns self when the design is unavailable.
    func withDesign(_ design: UIFontDescriptor.SystemDesign) -> UIFont {
        guard let desc = fontDescriptor.withDesign(design) else { return self }
        return UIFont(descriptor: desc, size: pointSize)
    }
}

extension Font.TextStyle {
    /// Bridge to the UIKit text style, needed for `UIFontMetrics` scaling.
    var uiTextStyle: UIFont.TextStyle {
        switch self {
        case .largeTitle:  return .largeTitle
        case .title:       return .title1
        case .title2:      return .title2
        case .title3:      return .title3
        case .headline:    return .headline
        case .subheadline: return .subheadline
        case .callout:     return .callout
        case .footnote:    return .footnote
        case .caption:     return .caption1
        case .caption2:    return .caption2
        case .body:        return .body
        @unknown default:  return .body
        }
    }
}

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
// MARK: - Pressable Button Style
// ═══════════════════════════════════════════════════════════════════
// A tactile press response used app-wide: a subtle scale + dim on press,
// with a spring back. Gives every primary/ghost/chip button the same feel.

struct PressableButtonStyle: ButtonStyle {
    var scale: CGFloat = 0.97

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? scale : 1)
            .opacity(configuration.isPressed ? 0.9 : 1)
            .animation(.spring(response: 0.3, dampingFraction: 0.6), value: configuration.isPressed)
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
                        .tint(Color.snapOnAccent)
                } else {
                    Text(title)
                        .font(.snapButton)
                        .foregroundStyle(Color.snapOnAccent)
                }
            }
            .frame(maxWidth: .infinity)
            .frame(minHeight: 56)          // min, not fixed: grows with Dynamic Type
            .background(Color.snapTerracotta)
            .clipShape(Capsule())
        }
        .buttonStyle(PressableButtonStyle())
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
        .buttonStyle(PressableButtonStyle())
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
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var phase: CGFloat = -1

    func body(content: Content) -> some View {
        content
            .overlay(
                Group {
                    // Reduce Motion: a static wash instead of a travelling one.
                    // The surface still reads as "pending" without the movement.
                    if reduceMotion {
                        Color.white.opacity(0.18)
                    } else {
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
                    }
                }
                .clipped()
            )
            .onAppear {
                guard !reduceMotion else { return }
                withAnimation(.linear(duration: 1.4).repeatForever(autoreverses: false)) {
                    phase = 1
                }
            }
    }
}

extension View {
    func shimmering() -> some View { modifier(ShimmerModifier()) }

    /// Applies an animation unless Reduce Motion is enabled, in which case the
    /// state change still happens — just without the movement. Use this instead
    /// of `.animation(_:value:)` for anything decorative or continuous.
    func snapAnimation<V: Equatable>(_ animation: Animation?, value: V) -> some View {
        modifier(MotionAwareAnimation(animation: animation, value: value))
    }
}

private struct MotionAwareAnimation<V: Equatable>: ViewModifier {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let animation: Animation?
    let value: V

    func body(content: Content) -> some View {
        content.animation(reduceMotion ? nil : animation, value: value)
    }
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
        "Analyzing the item…",
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
                    .foregroundStyle(Color.snapOnCharcoal)
                    .opacity(opacity)
                    .animation(.easeInOut(duration: 0.35), value: opacity)

                Label("Photo captured — you can lower your phone", systemImage: "checkmark.circle.fill")
                    .font(.dmSans(13, weight: .medium))
                    .foregroundStyle(Color.snapOnCharcoal.opacity(0.7))
                    .labelStyle(.titleAndIcon)
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
            .frame(width: max(0, width - 24), height: 120)
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
                .frame(width: max(0, width - 24), alignment: .leading)

            Text(result.formattedRange)
                .font(.fraunces(16, weight: .bold))
                .foregroundStyle(Color.snapSage)
        }
        .padding(12)
        .frame(width: max(0, width))
        .snapCard()
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(result.itemName), estimated \(result.formattedRange)")
        .accessibilityAddTraits(.isButton)
    }
}
