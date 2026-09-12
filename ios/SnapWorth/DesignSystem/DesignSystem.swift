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

    // ── The light-side hexes, so a test can read them ───────────────────────
    //
    // The dark ones live in `SnapDarkHex`, in the shared widget model, because
    // the extension needs them too. These are app-only, but they are pulled
    // out for the same reason: a `Color` cannot be measured, and every
    // contrast failure this palette has had — `snapWarmGray` at 3.1:1 on
    // cream, cream at 3.18:1 on a terracotta fill, cream ink at 1.46:1 on a
    // dark-mode amber badge — was invisible to the compiler and to every test.
    // As strings, `WidgetPaletteTests`-style assertions can compute the ratios
    // and fail the build.
    enum SnapLightHex {
        static let background = "FBF7F2"
        static let card = "FFFFFF"
        static let terracotta = "D96C47"
        static let terracottaHC = "BE5433"
        static let terracottaText = "B34D2E"
        static let terracottaTextHC = "94381D"
        static let sage = "6F8F6B"
        static let sageHC = "4F6E4B"
        static let sageText = "4F6E4B"
        static let sageTextHC = "3C5539"
        static let amber = "EBB868"
        static let espresso = "2B211C"
        static let espressoHC = "1A120E"
        static let warmGray = "6E6055"
        static let warmGrayHC = "544840"
        static let border = "EFE6DC"
    }

    // Backgrounds
    static let snapBackground = snapAdaptive(
        light: Color(hex: SnapLightHex.background),   // warm cream
        dark:  Color(hex: SnapDarkHex.ground)  // deep espresso ground
    )
    static let snapCard = snapAdaptive(
        light: Color(hex: SnapLightHex.card),
        dark:  Color(hex: SnapDarkHex.card)   // raised warm surface
    )

    // Accents — lifted in dark so they stay legible on a dark ground
    static let snapTerracotta = snapAdaptive(
        light: Color(hex: SnapLightHex.terracotta), dark: Color(hex: SnapDarkHex.terracotta),
        lightHighContrast: Color(hex: SnapLightHex.terracottaHC)
    )
    static let snapSage = snapAdaptive(     // money / positive values
        light: Color(hex: SnapLightHex.sage), dark: Color(hex: SnapDarkHex.sage),
        lightHighContrast: Color(hex: SnapLightHex.sageHC)
    )
    static let snapAmber = snapAdaptive(    // badges / highlights
        light: Color(hex: SnapLightHex.amber), dark: Color(hex: SnapDarkHex.amber)
    )

    // ── Terracotta has three jobs and they want three different values ──────
    //
    // `snapTerracotta` is the brand colour, and as a *foreground* it measures
    // 3.39:1 on a white card and 3.18:1 on the cream ground — fine for a
    // border or a stroke, which WCAG holds to 3:1, and under AA's 4.5:1 for
    // every one of the ~30 labels drawn in it: the keyboard toolbar's "Done"
    // (the only way off the money keypad), every error message, "Edit",
    // "Regenerate".
    //
    // As a *fill* under the fixed cream of `snapOnAccent` it is worse: 3.18:1
    // in light and 2.49:1 in dark, and 4.37:1 even under Increased Contrast.
    // That is every primary button in the app.
    //
    // A fill and a foreground pull in opposite directions — a foreground on a
    // light ground wants darkening, a fill under cream wants darkening too but
    // much further, and on a dark ground the foreground wants *lifting*. One
    // token cannot do all three, so it does not: the brand value stays put for
    // borders, strokes, icons and tints, and text and fills get their own.

    /// Terracotta as text. 5.23:1 on a white card, 4.90:1 on the cream ground.
    ///
    /// The dark-mode value is unchanged — `snapTerracotta` already measures
    /// 6.38:1 on a dark card, because the adaptive pair was only ever wrong on
    /// the light side.
    static let snapTerracottaText = snapAdaptive(
        light: Color(hex: SnapLightHex.terracottaText), dark: Color(hex: SnapDarkHex.terracotta),
        lightHighContrast: Color(hex: SnapLightHex.terracottaTextHC)
    )

    /// Terracotta as a filled surface with `snapOnAccent` on top. 5.43:1.
    ///
    /// Fixed rather than adaptive, and deliberately: the ink on it is fixed
    /// cream in both themes, so the fill has to clear AA against cream in both
    /// themes too, and there is exactly one value that does. Shared with the
    /// widget extension via `SnapDarkHex`, so a Home Screen tile and a button
    /// in the app are the same terracotta.
    static let snapTerracottaFill = Color(hex: SnapDarkHex.terracottaFill)

    /// Sage as text. 5.73:1 on a white card, 5.37:1 on the cream ground.
    ///
    /// Sage is the money colour — every estimate, every profit figure, every
    /// total — and as a foreground the brand value measures **3.38:1** on the
    /// ground and 3.61:1 on a card. So in light mode every number the app
    /// exists to show was under AA. A contrast test caught this; no finder
    /// did, because the reported symptom was the *diluted* sage in the
    /// History and Flips captions at 2.09:1, and the undiluted case looked
    /// fine by comparison.
    ///
    /// Same split as terracotta, for the same reason: the brand value stays
    /// for the 10% tints, the strokes and the progress bars, all of which are
    /// non-text and clear 3:1 comfortably.
    static let snapSageText = snapAdaptive(
        light: Color(hex: SnapLightHex.sageText), dark: Color(hex: SnapDarkHex.sage),
        lightHighContrast: Color(hex: SnapLightHex.sageTextHC)
    )

    /// Ink for anything drawn on `snapAmber`. Fixed dark: amber stays light in
    /// both themes, so ink that follows the theme inverts to light-on-light.
    /// `snapEspresso` on `snapAmber` measured 1.46:1 in dark mode — the plan
    /// card's "SAVE 33%" badge, functionally invisible.
    static let snapOnAmber = Color(hex: SnapLightHex.espresso)

    // Text — `snapWarmGray` was 3.1:1 on cream (below WCAG AA); darkened to
    // 5.7:1 while keeping the warmth.
    static let snapEspresso = snapAdaptive(
        light: Color(hex: SnapLightHex.espresso), dark: Color(hex: SnapDarkHex.espresso),
        lightHighContrast: Color(hex: SnapLightHex.espressoHC), darkHighContrast: .white
    )
    static let snapWarmGray = snapAdaptive(
        light: Color(hex: SnapLightHex.warmGray), dark: Color(hex: SnapDarkHex.warmGray),
        lightHighContrast: Color(hex: SnapLightHex.warmGrayHC), darkHighContrast: Color(hex: "D6CCC3")
    )

    /// Border hexes, named because the contrast tests need them.
    ///
    /// `SnapLightHex.border` already existed and `snapBorder` did not use it,
    /// which is a drift waiting to happen. The dark values cannot go in
    /// `SnapDarkHex`: that enum is inside the byte-identical shared widget
    /// block, so a member added there has to be added to the widget extension
    /// in the same commit.
    enum SnapBorderHex {
        static let light = SnapLightHex.border          // EFE6DC
        static let dark = "342A24"
        static let lightHighContrast = "D3C4B4"
        static let darkHighContrast = "4C3E35"
    }

    // Borders / dividers
    static let snapBorder = snapAdaptive(
        light: Color(hex: SnapBorderHex.light), dark: Color(hex: SnapBorderHex.dark),
        lightHighContrast: Color(hex: SnapBorderHex.lightHighContrast),
        darkHighContrast: Color(hex: SnapBorderHex.darkHighContrast)
    )

    /// Camera screen background — deliberately dark in *both* themes; the
    /// viewfinder is a dark surface by design, not by theme.
    static let snapCharcoal = Color(hex: SnapDarkHex.charcoal)

    /// Content that always sits on `snapCharcoal` (camera chrome). Fixed cream
    /// so it never inverts to dark-on-dark when the system theme flips.
    static let snapOnCharcoal = Color(hex: SnapDarkHex.cream)

    /// Content that always sits on a *filled accent* surface — primary button
    /// labels on terracotta. Fixed cream: the accent is dark enough in both
    /// themes that the label must not follow the theme.
    ///
    /// Only while the accent is at full opacity. A *dimmed* accent composites
    /// toward whatever is behind it, so on a light ground it stops being a dark
    /// surface and cream stops reading on it — see `PrimaryButton`, which
    /// switches to `snapEspresso` when disabled.
    static let snapOnAccent = Color(hex: SnapDarkHex.cream)

    /// The travelling highlight on a loading skeleton.
    ///
    /// A darkening sweep on light surfaces, a warm cream sweep on dark ones.
    /// It was `Color.white` in both, which is two separate failures: invisible
    /// on the light skeleton (a 1.09:1 peak, so the placeholder was a static
    /// block and a slow decode looked identical to a missing image), and a
    /// cold pure-white flare at 7.23:1 on the warm dark card — a neutral white
    /// introduced into a palette whose stated principle is that the warm
    /// identity is preserved by re-grounding rather than by neutral whites.
    ///
    /// Routed through `snapEspresso` rather than given its own hexes, because
    /// "ink that follows the theme" is exactly what a sweep needs to be, and a
    /// second copy of the same pair would be a second thing to keep in step.
    static let snapShimmer = snapEspresso

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

    // `.disabled()` applied by a caller lands here, and nothing used to read
    // it — so a button SwiftUI had already made inert still rendered at full
    // terracotta and animated on press. On the paywall that meant a CTA that
    // looked buyable and did nothing when StoreKit failed to return products.
    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        Button(action: action) {
            ZStack {
                if isLoading {
                    ProgressView()
                        .tint(Color.snapOnAccent)
                } else {
                    Text(title)
                        // `snapOnAccent` is fixed cream because "the accent is
                        // dark enough in both themes that the label must not
                        // follow the theme" — true at full opacity (5.43:1).
                        // At 40% over a light ground the accent is no longer
                        // dark: it composites to #DAB1A3, and cream on that is
                        // 1.82:1. The label did not read as disabled, it
                        // vanished, leaving a blank pale-peach capsule — and
                        // that is the paywall CTA when StoreKit returns no
                        // products, so the user could not tell what the button
                        // would have done, only that something was missing.
                        //
                        // The premise fails with the surface, so the ink has to
                        // follow the theme once the surface does: 8.07:1 light,
                        // 10.50:1 dark, and ≥ 9.49:1 in both high-contrast
                        // variants.
                        //
                        // Dimming the whole button instead — the obvious fix —
                        // is worse in both directions: 2.16:1 in light mode at
                        // 0.5, still a failure, and it drags dark mode down
                        // from 10.50:1 to 3.03:1 by dimming a cream label
                        // toward a dark ground. The fill keeps the dim; only
                        // the ink changes.
                        .foregroundStyle(isEnabled ? Color.snapOnAccent
                                                   : Color.snapEspresso)
                }
            }
            .frame(maxWidth: .infinity)
            .frame(minHeight: 56)          // min, not fixed: grows with Dynamic Type
            .background(Color.snapTerracottaFill.opacity(isEnabled ? 1 : 0.4))
            .clipShape(Capsule())
        }
        .buttonStyle(PressableButtonStyle(scale: isEnabled ? 0.97 : 1))
        .disabled(isLoading)
        .animation(.easeInOut(duration: 0.2), value: isEnabled)
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
                        .foregroundStyle(Color.snapTerracottaText)
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
            .foregroundStyle(Color.snapSageText)
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

    /// Shape reinforces the level so it isn't carried by colour alone.
    private var indicator: String {
        switch confidence.lowercased() {
        case "high":   return "checkmark.circle.fill"
        case "medium": return "minus.circle.fill"
        default:       return "questionmark.circle.fill"
        }
    }

    var body: some View {
        Label {
            Text("\(confidence) confidence")
        } icon: {
            Image(systemName: indicator).snapSymbol(12, weight: .semibold)
        }
        .labelStyle(.titleAndIcon)
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
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(confidence) confidence")
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
                    //
                    // At `Color.white.opacity(0.18)` the claim that "the
                    // surface still reads as pending" was not true: the
                    // skeleton base is `snapBorder.opacity(0.6)` over the card,
                    // which composites to #F5F0EA in light mode, and a white
                    // wash on that is a 1.02:1 change — below the threshold of
                    // visible difference. 0.5 of the palette sweep is 3.06:1
                    // light and 4.27:1 dark, which clears the 3:1 that WCAG
                    // 1.4.11 asks of a meaningful non-text boundary. A
                    // placeholder whose job is to say "pending" has to be
                    // visible to say it.
                    if reduceMotion {
                        Color.snapShimmer.opacity(0.5)
                    } else {
                        GeometryReader { geo in
                            LinearGradient(
                                stops: [
                                    .init(color: .clear, location: 0),
                                    .init(color: Color.snapShimmer.opacity(0.45), location: 0.4),
                                    .init(color: Color.snapShimmer.opacity(0.65), location: 0.5),
                                    .init(color: Color.snapShimmer.opacity(0.45), location: 0.6),
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

    /// Sizes an SF Symbol so it scales with Dynamic Type.
    ///
    /// `.font(.system(size:))` pins a symbol at a fixed point size, so it stays
    /// small while surrounding text grows — the icon ends up visually detached
    /// from its label. `@ScaledMetric` tracks the text style *and* honours
    /// per-view `dynamicTypeSize` clamps.
    func snapSymbol(
        _ size: CGFloat,
        weight: Font.Weight = .regular,
        relativeTo style: Font.TextStyle = .body
    ) -> some View {
        modifier(ScaledSymbolModifier(size: size, weight: weight, style: style))
    }

    /// Minimum 44×44pt hit target (Apple HIG). Expands the tappable area
    /// without changing the visual size.
    func snapHitTarget(_ minimum: CGFloat = 44) -> some View {
        frame(minWidth: minimum, minHeight: minimum)
            .contentShape(Rectangle())
    }

    /// Applies an animation unless Reduce Motion is enabled, in which case the
    /// state change still happens — just without the movement. Use this instead
    /// of `.animation(_:value:)` for anything decorative or continuous.
    func snapAnimation<V: Equatable>(_ animation: Animation?, value: V) -> some View {
        modifier(MotionAwareAnimation(animation: animation, value: value))
    }
}

private struct ScaledSymbolModifier: ViewModifier {
    @ScaledMetric private var scaledSize: CGFloat
    private let weight: Font.Weight

    init(size: CGFloat, weight: Font.Weight, style: Font.TextStyle) {
        _scaledSize = ScaledMetric(wrappedValue: size, relativeTo: style)
        self.weight = weight
    }

    func body(content: Content) -> some View {
        content.font(.system(size: scaledSize, weight: weight))
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
                            .snapSymbol(28, weight: .light)
                            .foregroundStyle(Color.snapTerracottaText)
                            .shimmering()
                    )

                Text(messages[messageIndex])
                    .font(.dmSans(17, weight: .medium))
                    .foregroundStyle(Color.snapOnCharcoal)
                    .opacity(opacity)
                    .animation(.easeInOut(duration: 0.35), value: opacity)

                Label("Photo captured — you can lower your phone", systemImage: "checkmark.circle.fill")
                    .font(.dmSans(13, weight: .medium))
                    // 0.8, not 0.7. This sits on `snapCharcoal.opacity(0.72)`
                    // over the *captured photo*, so the ground is only as dark
                    // as the scrim makes it: against a bright photo — a white
                    // shelf, a lit shop wall, the common case for a phone held
                    // over an item — the scrim composites to #5C5856 and cream
                    // at 0.7 measured 4.21:1, under the 4.5:1 that 13pt text
                    // needs. At 0.8 the worst case is 4.93:1.
                    //
                    // Raising the ink rather than deepening the scrim: the
                    // scrim is the whole overlay's look, and the failure is one
                    // caption. 0.88 on the scrim would also have worked and
                    // would have darkened a screen nothing was wrong with.
                    .foregroundStyle(Color.snapOnCharcoal.opacity(0.8))
                    .labelStyle(.titleAndIcon)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 24)
            }
            // Progress is conveyed by rotating copy; without a live region a
            // VoiceOver user gets silence for the whole 3–6s analysis.
            .accessibilityElement(children: .combine)
            .accessibilityLabel("\(messages[messageIndex]) Photo captured — you can lower your phone.")
            .accessibilityAddTraits(.updatesFrequently)
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
                            // Not `snapEspresso`: it inverts to a light cream
                            // in dark mode while amber stays light, which put
                            // the "SAVE 33%" badge at 1.46:1 — the saving is
                            // the reason to pick the yearly plan, and it was
                            // functionally invisible to anyone in dark mode.
                            .foregroundStyle(Color.snapOnAmber)
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
        .snapAnimation(.spring(duration: 0.2), value: isSelected)
        // Radio semantics: one stop reading plan, price and detail, with
        // `.isSelected` carrying the state that the border colour shows.
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(title)
        .accessibilityValue("\(price). \(priceDetail)\(badge.map { ". \($0)" } ?? "")")
        .accessibilityHint("Selects the \(title.lowercased()) plan")
        .accessibilityAddTraits(isSelected ? [.isButton, .isSelected] : .isButton)
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
                // `UIImage(data:)` alone left the decode to the first draw, on
                // the main thread inside `body` — see `decodedThumbnail`.
                thumbnail = await ScanAPIClient.decodedThumbnail(
                    from: data, side: max(width - 24, 120))
                imageLoadAttempted = true
            }

            Text(result.itemName)
                .font(.dmSans(13, weight: .medium))
                .foregroundStyle(Color.snapEspresso)
                .lineLimit(2)
                .frame(width: max(0, width - 24), alignment: .leading)

            Text(result.formattedRange)
                .font(.fraunces(16, weight: .bold))
                .foregroundStyle(Color.snapSageText)
        }
        .padding(12)
        .frame(width: max(0, width))
        .snapCard()
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(result.itemName), estimated \(result.formattedRange)")
        .accessibilityAddTraits(.isButton)
    }
}
