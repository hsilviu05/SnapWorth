import SwiftUI

// ═══════════════════════════════════════════════════════════════════
// MARK: - Tag's tips (Pro)
// ═══════════════════════════════════════════════════════════════════
//
// Tag, the mascot, gives one piece of resale advice on a result. Each tip is
// picked on the device from facts the find already has: its status, what was
// paid, the price range, the confidence and the number of sold listings. No
// request is made and no model is asked, so this sends nothing anywhere and
// costs nothing to run.

/// One piece of advice Tag can give, and the face it gives it with.
enum TagTip: String, CaseIterable {
    case sold
    case underwater
    case lowConfidence
    case doubled
    case fewSales
    case manySales
    case highValue
    case photos

    /// Settings' "Tag's tips" toggle. On by default. Off hides the card for
    /// Pro and free users alike.
    static let preferenceKey = "snapworth_tag_tips"

    /// A likely value at or above this counts as a higher-value find.
    static let highValueThreshold: Double = 150

    /// What a tip is chosen from. Kept apart from `ScanResult` so the rules
    /// can be tested without a SwiftData store.
    struct Facts: Equatable {
        var status: FlipStatus
        var confidence: String
        var paid: Double?
        var likely: Double
        var high: Double
        var soldListings: Int
    }

    /// Every tip that applies, most useful first. Never empty: `.photos`
    /// applies to anything not yet sold.
    static func tips(for facts: Facts) -> [TagTip] {
        // Once it has sold, listing advice is beside the point.
        if facts.status == .sold { return [.sold] }

        // A paid price of zero is a free find: no cost to compare against.
        let paid = facts.paid.flatMap { $0.isFinite && $0 > 0 ? $0 : nil }
        var tips: [TagTip] = []
        if let paid, paid > facts.high { tips.append(.underwater) }
        if facts.confidence.lowercased() == "low" { tips.append(.lowConfidence) }
        if let paid, facts.likely >= paid * 2 { tips.append(.doubled) }
        // Zero means the scan had no sold listings to report, not that none
        // exist, so it says nothing either way.
        if (1..<5).contains(facts.soldListings) { tips.append(.fewSales) }
        if facts.soldListings >= 20 { tips.append(.manySales) }
        if facts.likely >= highValueThreshold { tips.append(.highValue) }
        tips.append(.photos)
        return tips
    }

    var mood: TagMascot.Mood {
        switch self {
        case .sold, .doubled: .joy
        case .underwater, .lowConfidence, .highValue: .wow
        case .fewSales, .manySales, .photos: .happy
        }
    }

    var message: String {
        switch self {
        case .sold:
            String(localized: "Sold! Nice flip. Scan your next find while you're on a roll.")
        case .underwater:
            String(localized: "You paid more than this usually sells for. Bundling it with something similar can make it worth listing.")
        case .lowConfidence:
            String(localized: "I'm not sure about this one. A close-up of the label or care tag will sharpen the estimate.")
        case .doubled:
            String(localized: "That's at least double what you paid. Worth listing soon.")
        case .fewSales:
            String(localized: "Only a few recent sales to compare. Start near the top of the range; you can always lower it.")
        case .manySales:
            String(localized: "Plenty of these sell. Price near the middle of the range to sell it quickly.")
        case .highValue:
            String(localized: "A higher-value find. Buyers look closely, so photograph the label and every flaw.")
        case .photos:
            String(localized: "Listings with four or more daylight photos tend to sell faster.")
        }
    }
}

extension TagTip.Facts {
    init(_ result: ScanResult) {
        self.init(status: result.status,
                  confidence: result.confidence,
                  paid: result.paidPrice,
                  likely: result.likelyValue,
                  high: result.displayValueHigh,
                  soldListings: result.soldListingsCount)
    }
}

/// "Tag's tips" on a result. Pro sees Tag's advice and can tap Tag for the
/// next tip; a free user sees what the card does and a way to unlock it.
struct TagTipsCard: View {
    let result: ScanResult
    let isPro: Bool
    let onPaywall: (PaywallTrigger) -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var index = 0
    @State private var appeared = false
    @State private var hops = 0
    @State private var showAskTag = false

    private var tips: [TagTip] { TagTip.tips(for: TagTip.Facts(result)) }

    /// Modulo because the list can shrink under the index: marking the find
    /// sold while the card is on screen leaves one tip.
    private var tip: TagTip {
        let tips = tips
        return tips[index % tips.count]
    }

    private var message: String {
        isPro ? tip.message
              : String(localized: "Get Tag's advice on pricing and listing for every find, and ask Tag your own questions.")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Tag's tips")
                    .snapSectionHeader()
                Spacer()
                if !isPro { ProBadge() }
            }

            HStack(alignment: .center, spacing: 12) {
                Button(action: nextTip) {
                    TagMascot(mood: isPro ? tip.mood : .happy, height: 64)
                        .modifier(TagHop(trigger: hops))
                        .scaleEffect(appeared ? 1 : 0.4, anchor: .bottom)
                        .opacity(appeared ? 1 : 0)
                }
                .buttonStyle(.plain)
                .disabled(!isPro)
                // Tag is decorative; the button is for the next tip, and is
                // only offered to VoiceOver when there is one.
                .accessibilityLabel("Next tip")
                .accessibilityHidden(!isPro || tips.count < 2)

                Text(message)
                    .font(.snapBody)
                    .foregroundStyle(Color.snapEspresso)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .id(isPro ? tip.rawValue : "teaser")
                    .transition(reduceMotion ? AnyTransition.opacity
                                : AnyTransition.opacity.combined(with: .move(edge: .trailing)))
                    .padding(14)
                    .background(Color.snapBackground,
                                in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                    .opacity(appeared ? 1 : 0)
                    .offset(x: appeared || reduceMotion ? 0 : -12)
            }

            if isPro {
                if tips.count > 1 {
                    Text("Tap Tag for another tip")
                        .font(.snapCaption)
                        .foregroundStyle(Color.snapWarmGray)
                        .accessibilityHidden(true)
                }
                PrimaryButton(title: result.askTagData == nil ? "Ask Tag a question" : "Continue with Tag") {
                    showAskTag = true
                }
            } else {
                PrimaryButton(title: "Unlock Tag's tips") { onPaywall(.tagTips) }
            }
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .shadow(color: Color.snapCardShadow.opacity(0.08), radius: 24, x: 0, y: 8)
        .sheet(isPresented: $showAskTag) {
            AskTagView(result: result, onClose: { showAskTag = false })
        }
        .onAppear {
            guard !appeared else { return }
            if reduceMotion {
                appeared = true
            } else {
                withAnimation(.spring(response: 0.5, dampingFraction: 0.6).delay(0.2)) {
                    appeared = true
                }
            }
        }
    }

    private func nextTip() {
        guard isPro else { return }
        Haptics.selection()
        hops += 1
        guard tips.count > 1 else { return }
        withAnimation(reduceMotion ? nil : .snappy) {
            index = (index + 1) % tips.count
        }
    }
}

/// Tag hops and wobbles each time `trigger` changes. Nothing under Reduce
/// Motion.
private struct TagHop: ViewModifier {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let trigger: Int

    private struct Frame {
        var lift: CGFloat = 0
        var tilt: Double = 0
    }

    func body(content: Content) -> some View {
        if reduceMotion {
            content
        } else {
            content.keyframeAnimator(initialValue: Frame(), trigger: trigger) { view, frame in
                view
                    .rotationEffect(.degrees(frame.tilt), anchor: .bottom)
                    .offset(y: frame.lift)
            } keyframes: { _ in
                KeyframeTrack(\.lift) {
                    SpringKeyframe(-16, duration: 0.18)
                    SpringKeyframe(0, duration: 0.35, spring: .bouncy)
                }
                KeyframeTrack(\.tilt) {
                    LinearKeyframe(-8, duration: 0.1)
                    LinearKeyframe(8, duration: 0.15)
                    SpringKeyframe(0, duration: 0.3, spring: .bouncy)
                }
            }
        }
    }
}
