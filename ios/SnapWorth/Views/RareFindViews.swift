import SwiftData
import SwiftUI
import UIKit

// The screens of the rare-find easter egg — see `RareFind` for what it is and
// the rules it keeps. Every word they show or speak comes from `RareFindCopy`
// (a few of those keys borrowed from the analysing overlay and the result
// card), except the tagline, which is printed as the shirt prints it.

// ═══════════════════════════════════════════════════════════════════
// MARK: - Appraisal (stands in for the analysing overlay)
// ═══════════════════════════════════════════════════════════════════

/// A sweep over the frozen photo and status lines that escalate, one per
/// fifth of the appraisal. Only ever on screen while the scan is analysing, and
/// never under Reduce Motion — `ScanViewModel` does not start one then.
struct RareFindAppraisalView: View {
    let lines: [String]
    var duration: Duration = RareFind.appraisalDuration

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var shown = 1

    private var visible: Int { min(shown, lines.count) }
    private var current: String { visible > 0 ? lines[visible - 1] : "" }

    var body: some View {
        ZStack {
            Color.snapCharcoal.opacity(0.55)
                .ignoresSafeArea()

            if !reduceMotion {
                RareFindSweep()
                    .ignoresSafeArea()
            }

            VStack(spacing: 20) {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(0..<visible, id: \.self) { index in
                        line(index)
                            .transition(.opacity)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(20)
                .background(Color.snapCharcoal.opacity(0.85),
                            in: RoundedRectangle(cornerRadius: 20, style: .continuous))

                // The overlay's reassurance in the overlay's ink, but on a
                // ground of its own. The overlay's 0.8 cream is sized for its
                // 0.72 scrim (4.93:1 over a white photo); this scrim is 0.55,
                // so the photo shows through the sweep, and the sweep's amber
                // band passes behind this line — over a white product shot,
                // the likeliest library pick, the bare scrim measured 2.99:1
                // and 2.38:1 under the band. On the panel's charcoal it is
                // 8.4:1 at worst, with the sweep's bright line behind it.
                Label(RareFindCopy.photoCaptured, systemImage: "checkmark.circle.fill")
                    .font(.dmSans(13, weight: .medium))
                    .foregroundStyle(Color.snapOnCharcoal.opacity(0.8))
                    .labelStyle(.titleAndIcon)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 8)
                    // Not a capsule: in German, or at a large text size, the
                    // line wraps, and a two-line capsule is a lozenge.
                    .background(Color.snapCharcoal.opacity(0.85),
                                in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            }
            // Sized from the screen, not from what the parent proposes. In
            // ScanView this sits beside the frozen photo, and a `scaledToFill`
            // image widens the ZStack around it to the photo's filled width —
            // a portrait capture proposes ~640pt on a 393pt phone, and the
            // panel ran off both edges.
            .containerRelativeFrame(.horizontal) { width, _ in min(width - 48, 400) }
            // One element that reads the newest line, like the overlay it
            // stands in for: the lines are progress, not content to browse.
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(RareFindCopy.spokenAppraisal(current))
            .accessibilityAddTraits(.updatesFrequently)
        }
        .task { await escalate() }
    }

    private func line(_ index: Int) -> some View {
        let isPeak = index == lines.count - 1
        let isNewest = index == visible - 1
        return HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: isPeak ? "exclamationmark.triangle.fill"
                                     : isNewest ? "magnifyingglass" : "checkmark")
                .snapSymbol(14, weight: .bold)
            Text(lines[index])
                .fixedSize(horizontal: false, vertical: true)
        }
        .font(isPeak ? .dmSans(18, weight: .bold, relativeTo: .headline)
                     : .dmSans(15, weight: .medium, relativeTo: .subheadline))
        // Amber on the charcoal panel is 9:1; cream at 0.8 clears 4.5:1 even
        // where the panel sits over a white photo.
        .foregroundStyle(isPeak ? Color(hex: Color.SnapLightHex.amber)
                                : Color.snapOnCharcoal.opacity(isNewest ? 1 : 0.8))
    }

    private func escalate() async {
        guard lines.count > 1 else { return }
        let interval = duration / lines.count
        while shown < lines.count {
            try? await Task.sleep(for: interval)
            guard !Task.isCancelled else { return }
            withAnimation(reduceMotion ? nil : .easeOut(duration: 0.25)) { shown += 1 }
        }
    }
}

/// A band of light running down the photo and back, like a flatbed scanner.
private struct RareFindSweep: View {
    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 60)) { context in
            GeometryReader { geo in
                let t = context.date.timeIntervalSinceReferenceDate
                // Down and back every 2.4 s, easing at each end.
                let phase = (1 - cos(t * 2 * .pi / 2.4)) / 2
                let amber = Color(hex: Color.SnapLightHex.amber)
                LinearGradient(colors: [.clear, amber.opacity(0.32), .clear],
                               startPoint: .top, endPoint: .bottom)
                    .frame(height: 160)
                    .overlay(Rectangle().fill(amber.opacity(0.9)).frame(height: 2))
                    .position(x: geo.size.width / 2, y: phase * geo.size.height)
            }
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - The card
// ═══════════════════════════════════════════════════════════════════

/// The certificate. Fixed colours, like `ShareCardView`, so it is the same
/// object on screen in either theme and in the shared image.
///
/// Labelled as an easter egg at the top, in print and first in its VoiceOver
/// label — which is also what the shared image carries.
///
/// To VoiceOver it is one summary element and then the shirt's own line (and
/// the brand, once there is one). The tagline cannot be the end of the
/// summary's label: in Romanian, Spanish, German or Chinese that label is
/// spoken in that voice, English included — see `RareFind.taglineLocale`.
struct RareFindCard: View {
    let reveal: RareFindReveal
    /// The figure while the value counts up; nil once it has landed — which is
    /// also how the shared image and Reduce Motion draw it.
    var ticker: Int? = nil

    private var landed: Bool { ticker == nil }

    private let paper = Color(hex: Color.SnapLightHex.background)
    private let ink = Color(hex: Color.SnapLightHex.espresso)
    /// 5.7:1 on the paper.
    private let muted = Color(hex: Color.SnapLightHex.warmGray)
    /// 4.9:1 on the paper.
    private let stampInk = Color(hex: Color.SnapLightHex.terracottaText)
    private let amber = Color(hex: Color.SnapLightHex.amber)

    var body: some View {
        VStack(spacing: 18) {
            VStack(spacing: 18) {
                easterEggLabel
                stamp

                VStack(spacing: 4) {
                    eyebrow(RareFindCopy.rarityTier)
                    Text(reveal.tier)
                        .font(.fraunces(24, weight: .semibold, relativeTo: .title2))
                }

                VStack(spacing: 4) {
                    eyebrow(RareFindCopy.estimatedValue)
                    Text(verbatim: ticker.map(RareFind.money) ?? reveal.value.headline)
                        .font(.fraunces(40, weight: .bold, relativeTo: .largeTitle))
                        .monospacedDigit()
                        .lineLimit(1)
                        .minimumScaleFactor(0.5)
                    Text(reveal.value.punchline)
                        .font(.fraunces(17, relativeTo: .body).italic())
                        .foregroundStyle(muted)
                        // Held open while the figure climbs, so the card does
                        // not jump when the punchline lands.
                        .opacity(landed ? 1 : 0)
                }

                rule

                VStack(spacing: 10) {
                    ForEach(Array(reveal.stats.enumerated()), id: \.offset) { _, stat in
                        HStack(alignment: .firstTextBaseline, spacing: 12) {
                            Text(stat.label)
                                .foregroundStyle(muted)
                                .multilineTextAlignment(.leading)
                            Spacer(minLength: 0)
                            Text(verbatim: stat.value)
                                .fontWeight(.bold)
                                .monospacedDigit()
                        }
                        .font(.dmSans(15, weight: .medium, relativeTo: .subheadline))
                    }
                }

                rule

                Text(reveal.verdict)
                    .font(.fraunces(19, weight: .semibold, relativeTo: .title3))
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(reveal.spokenSummary)

            VStack(spacing: 6) {
                // Verbatim: the shirt's own words, in English in every
                // language — and, in this locale, in an English voice.
                Text(verbatim: RareFind.tagline)
                    .font(.fraunces(15, relativeTo: .subheadline).italic())
                    .foregroundStyle(muted)
                    .environment(\.locale, RareFind.taglineLocale)
                if let brand = RareFind.brandName {
                    Text(verbatim: brand.uppercased())
                        .font(.dmSans(12, weight: .bold, relativeTo: .caption))
                        .tracking(2)
                        .foregroundStyle(muted)
                        // As written, not in capitals, which VoiceOver may spell.
                        .accessibilityLabel(Text(verbatim: brand))
                }
            }
        }
        .multilineTextAlignment(.center)
        .foregroundStyle(ink)
        .fixedSize(horizontal: false, vertical: true)
        .padding(.horizontal, 24)
        .padding(.vertical, 26)
        .frame(maxWidth: .infinity)
        .background(paper)
        // A certificate's inner rule.
        .overlay(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .strokeBorder(stampInk.opacity(0.45), lineWidth: 1.5)
                .padding(8)
        )
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .accessibilityElement(children: .contain)
    }

    private var easterEggLabel: some View {
        VStack(spacing: 6) {
            Label {
                Text(RareFindCopy.easterEgg)
            } icon: {
                Image(systemName: "sparkles")
            }
            .font(.dmSans(12, weight: .bold, relativeTo: .caption))
            .textCase(.uppercase)
            .foregroundStyle(Color.snapOnAmber)
            .padding(.horizontal, 12)
            .padding(.vertical, 5)
            .background(amber, in: Capsule())

            Text(RareFindCopy.notARealValuation)
                .font(.dmSans(13, weight: .medium, relativeTo: .footnote))
                .foregroundStyle(muted)
        }
    }

    /// Lands with the value: slams down from oversize, unless nothing moves.
    private var stamp: some View {
        Text(RareFindCopy.stamp)
            .font(.fraunces(20, weight: .bold, relativeTo: .title3))
            .tracking(1.5)
            .foregroundStyle(stampInk)
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .strokeBorder(stampInk, lineWidth: 3)
            )
            .rotationEffect(.degrees(-4))
            .scaleEffect(landed ? 1 : 1.8)
            .opacity(landed ? 1 : 0)
    }

    private var rule: some View {
        Rectangle()
            .fill(muted.opacity(0.25))
            .frame(height: 1)
    }

    private func eyebrow(_ text: String) -> some View {
        Text(text)
            .font(.dmSans(12, weight: .semibold, relativeTo: .caption))
            .textCase(.uppercase)
            .tracking(1)
            .foregroundStyle(muted)
    }
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - Reveal (the first page of the result sheet)
// ═══════════════════════════════════════════════════════════════════

/// The card, then "Okay, seriously:" and the real estimate — the same range,
/// confidence and "AI estimate" wording as the result card — then the way on
/// to the full result. The estimate here is read straight from the saved
/// `ScanResult`; nothing on this screen writes to it.
struct RareFindRevealView: View {
    let reveal: RareFindReveal
    let result: ScanResult
    let onContinue: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var ticker: Int? = 0
    @State private var cardVisible = false
    @State private var hasAppeared = false
    @State private var shareItems: [Any] = []
    @State private var showShareSheet = false

    var body: some View {
        ScrollView {
            VStack(spacing: 28) {
                RareFindCard(reveal: reveal, ticker: ticker)
                    .shadow(color: Color.snapCardShadow.opacity(0.12), radius: 24, x: 0, y: 8)
                    .opacity(cardVisible ? 1 : 0)

                VStack(alignment: .leading, spacing: 12) {
                    Text(RareFindCopy.seriously)
                        .font(.snapTitle)
                        .foregroundStyle(Color.snapEspresso)
                        .accessibilityAddTraits(.isHeader)
                    Text(result.itemName)
                        .font(.snapBodyMedium)
                        .foregroundStyle(Color.snapEspresso)
                        .fixedSize(horizontal: false, vertical: true)
                    realEstimate
                }

                VStack(spacing: 12) {
                    Button(action: onContinue) {
                        Text(RareFindCopy.seeFullResult)
                            .font(.snapButton)
                            .foregroundStyle(Color.snapOnAccent)
                            .frame(maxWidth: .infinity)
                            .frame(minHeight: 56)
                            .background(Color.snapTerracottaFill)
                            .clipShape(Capsule())
                    }
                    .buttonStyle(PressableButtonStyle())
                    .accessibilityHint(RareFindCopy.seeFullResultHint)

                    Button(action: share) {
                        Label(RareFindCopy.share, systemImage: "square.and.arrow.up")
                            .font(.snapButton)
                            .foregroundStyle(Color.snapTerracottaText)
                            .frame(maxWidth: .infinity)
                            .frame(minHeight: 56)
                            .overlay(Capsule().strokeBorder(Color.snapTerracotta, lineWidth: 1.5))
                    }
                    .buttonStyle(PressableButtonStyle())
                    .accessibilityHint(RareFindCopy.shareHint)
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, 28)
            .padding(.bottom, 40)
        }
        .scrollIndicators(.hidden)
        .background(Color.snapBackground.ignoresSafeArea())
        .task { await play() }
        .sheet(isPresented: $showShareSheet) {
            if !shareItems.isEmpty {
                ActivityShareSheet(items: shareItems)
            }
        }
    }

    /// The result card's value block, as ResultView draws it once revealed.
    private var realEstimate: some View {
        VStack(spacing: 16) {
            VStack(spacing: 6) {
                Text(RareFindCopy.estimatedResaleValue)
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)
                ValueRangeView(low: result.displayValueLow, high: result.displayValueHigh)
            }

            Divider()

            HStack(spacing: 10) {
                ConfidenceBadge(confidence: result.confidence)
                // "AI estimate" unless real sales backed the number (#40).
                Text(result.valuationSource.caption)
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)
                    .lineLimit(1)
                Spacer()
            }
        }
        .padding(20)
        .frame(maxWidth: .infinity)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .shadow(color: Color.snapCardShadow.opacity(0.12), radius: 24, x: 0, y: 8)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(RareFindCopy.spokenEstimatedResaleValue)
        .accessibilityValue(result.valuationSource.spokenSummary(
            range: result.formattedRange, confidence: snapConfidencePhrase(result.confidence)))
    }

    /// Once per reveal: the events, the haptic and the announcement, then the
    /// fade and — unless Reduce Motion is on — the count-up and the stamp.
    private func play() async {
        guard !hasAppeared else { return }
        hasAppeared = true

        // `scan_result_shown` means a valuation was put in front of the user,
        // and this is where a rare find's first is: the real range and
        // confidence sit under "Okay, seriously:". So it fires here, with the
        // count ResultView would have read, and ResultView does not fire it
        // again after a reveal (ScanView passes it `priceAlreadyShown`). Left
        // on the full result, it went unsent by anyone who swiped the sheet
        // away from here, and late for everyone else.
        Analytics.shared.track(.scanResultShown(isFirst: ScanTally.isFirstRun()))
        // The one event the easter egg adds.
        Analytics.shared.track(.rareFindEasterEggShown)
        Haptics.success()
        // Queued, not interrupting: presenting the sheet has just moved
        // VoiceOver onto the card, whose label starts with the same news.
        UIAccessibility.post(
            notification: .announcement,
            argument: NSAttributedString(
                string: RareFindCopy.revealAnnouncement,
                attributes: [.accessibilitySpeechQueueAnnouncement: true]))

        if reduceMotion { ticker = nil }
        withAnimation(.easeInOut(duration: 0.4)) { cardVisible = true }
        guard !reduceMotion else { return }

        // Fast, and slowing toward the figure: about 1.2 s in all.
        let target = Double(reveal.value.countTo)
        let steps = 30
        for step in 1...steps {
            try? await Task.sleep(for: .milliseconds(40))
            guard !Task.isCancelled else { break }
            let progress = Double(step) / Double(steps)
            ticker = Int(target * (1 - pow(1 - progress, 3)))
        }
        withAnimation(.spring(response: 0.4, dampingFraction: 0.55)) { ticker = nil }
    }

    private func share() {
        guard let image = RareFindShareCard.render(reveal) else { return }
        shareItems = [image]
        showShareSheet = true
    }
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - The shared image
// ═══════════════════════════════════════════════════════════════════

/// The card on the share cards' 540×960pt canvas, landed, with the wordmark
/// under it. It carries the easter-egg label because the card does; it
/// carries no photo and no real estimate.
struct RareFindShareCard: View {
    let reveal: RareFindReveal

    var body: some View {
        VStack(spacing: 0) {
            Spacer(minLength: 28)
            RareFindCard(reveal: reveal)
                .frame(width: ShareCardView.cardWidth - 72)
            Spacer(minLength: 28)
            Text(verbatim: "SnapWorth")
                .font(.fraunces(22, weight: .bold))
                .foregroundStyle(Color(hex: Color.SnapLightHex.background))
                .padding(.bottom, 40)
        }
        .frame(width: ShareCardView.cardWidth, height: ShareCardView.cardHeight)
        .background(Color(hex: Color.SnapLightHex.espresso))
    }

    /// Rendered as the result card is — see `ResultViewModel.shareCardScale`.
    @MainActor
    static func render(_ reveal: RareFindReveal) -> UIImage? {
        let renderer = ImageRenderer(content: RareFindShareCard(reveal: reveal))
        renderer.scale = ResultViewModel.shareCardScale
        return renderer.uiImage
    }
}

// MARK: - Previews

#Preview("Rare find — card") {
    ScrollView {
        RareFindCard(reveal: .draw())
            .padding(20)
    }
    .background(Color.snapBackground)
}

#Preview("Rare find — reveal") {
    let config = ModelConfiguration(isStoredInMemoryOnly: true)
    // swiftlint:disable:next force_try — preview-only in-memory container, never ships
    let container = try! ModelContainer(for: ScanResult.self, configurations: config)
    let result = ScanResult(
        itemName: "Red Dragon Print Long-Sleeve Tee",
        brand: "Unknown", category: "Clothing",
        conditionNotes: "Good — sun-faded, as intended", valueLow: 25, valueHigh: 45,
        confidence: "medium", soldListingsCount: 0,
        listingTitle: "", listingDescription: ""
    )
    container.mainContext.insert(result)
    return RareFindRevealView(reveal: .draw(), result: result, onContinue: {})
        .modelContainer(container)
}

#Preview("Rare find — shared image") {
    RareFindShareCard(reveal: .draw())
        .scaleEffect(0.5, anchor: .top)
        .frame(width: 270, height: 480)
}

#Preview("Rare find — appraisal") {
    RareFindAppraisalView(lines: RareFindReveal.draw().statusLines)
        .background(Color(hex: "B8323A"))
}
