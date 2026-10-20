import SwiftUI

// The top of the result sheet, out of `ResultView` (#229, stage 5): the hero
// photo and the value card, with the guess cover and its reveal. The sheet
// keeps the reveal state, because the cover also hides the cards below this
// one, holds back the rating request, and is lifted by both re-reads; it
// keeps the guess field's text and focus too, like the ledger fields. These
// views take bindings to all of it and read nothing else of the sheet's.

/// The scan's photo, with the item name, brand and grade over it.
struct ResultHeroPhoto: View {
    let result: ScanResult
    /// Nil until the sheet has decoded `result.imageData`; shows a placeholder.
    let photo: UIImage?
    let width: CGFloat

    var body: some View {
        ZStack(alignment: .bottomLeading) {
            Group {
                if let img = photo {
                    Image(uiImage: img)
                        .resizable()
                        .scaledToFill()
                        .frame(width: width, height: 360)
                        .clipped()
                } else {
                    Rectangle()
                        .fill(Color.snapBorder)
                        .frame(width: width, height: 360)
                        .overlay(
                            Image(systemName: "photo")
                                .snapSymbol(48)
                                .foregroundStyle(Color.snapWarmGray)
                        )
                }
            }

            LinearGradient(
                stops: [
                    .init(color: .clear, location: 0.4),
                    .init(color: Color.black.opacity(0.65), location: 1.0)
                ],
                startPoint: .top,
                endPoint: .bottom
            )
            .frame(width: width, height: 360)

            VStack(alignment: .leading, spacing: 8) {
                Text(result.itemName)
                    .font(.fraunces(24, weight: .bold))
                    .foregroundStyle(.white)
                    .shadow(color: .black.opacity(0.3), radius: 4, x: 0, y: 2)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(width: max(0, width - 40), alignment: .leading)

                HStack(spacing: 8) {
                    if !result.brand.isEmpty && result.brand != "Unknown" {
                        photoChip(result.brand)
                    }
                    // The grade itself, as the Condition chips below word it.
                    // This used to be cut out of `conditionNotes` — split on
                    // dashes and full stops, first 22 characters — which fit
                    // v1's "Good — light pilling" notes. v2 writes prose, so
                    // the chip read "Well", "Pre" or "Moderate fading throug",
                    // in English in every language, and could contradict the
                    // grade the user had picked. The notes stay whole in the
                    // Condition card further down.
                    photoChip(result.condition.label)
                }
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 44)
            .frame(width: width, alignment: .leading)
        }
        .frame(width: width, height: 360)
        .ignoresSafeArea(edges: .top)
        // The photo is decoration; the item name and its chips are the content.
        // Combining them gives one clear stop instead of an image plus three
        // orphaned fragments.
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(heroAccessibilityLabel)
        .accessibilityAddTraits(.isHeader)
        .accessibilitySortPriority(90)
    }

    /// What the hero's chips say, spoken — so the grade, as on the chip. The
    /// full notes are read in the Condition card below.
    private var heroAccessibilityLabel: String {
        var parts = [result.itemName]
        if !result.brand.isEmpty, result.brand != "Unknown" {
            parts.append(String(localized: "Brand \(result.brand)"))
        }
        parts.append(String(localized: "Condition \(result.condition.label)"))
        return parts.joined(separator: ". ")
    }

    private func photoChip(_ label: String) -> some View {
        Text(label)
            .font(.snapLabel)
            .foregroundStyle(.white)
            .lineLimit(1)
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(Color.white.opacity(0.2))
            .background(.ultraThinMaterial)
            .clipShape(Capsule())
    }
}

/// The estimate: under its cover with an optional guess, or revealed with the
/// guess's verdict, the confidence and where the number came from.
struct ResultValueCard: View {
    let result: ScanResult
    /// Whether the value is still under its cover — the sheet's
    /// `priceCovered`, which also decides what else it shows.
    let covered: Bool
    /// The sheet's `priceRevealed`. Reveal sets it; so do both re-reads.
    @Binding var revealed: Bool
    @Binding var guessText: String
    /// The range the guess was scored against, captured at the reveal.
    ///
    /// The estimate goes on moving afterwards — the condition chips re-price
    /// it, the tag re-read replaces it outright — but the guess was entered
    /// once, against the number as it stood then, and the field it was typed
    /// into goes away with the cover. Scoring the live range meant a condition
    /// correction silently re-graded a verdict the user had already been given
    /// and could no longer answer: "spot on" could become "$12 under the low
    /// end" because they told the app the jacket was more worn than it looked.
    @Binding var revealedRange: (low: Double, high: Double)?
    let focus: FocusState<ResultView.Field?>.Binding

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        if covered {
            coveredValueCard
        } else {
            revealedValueCard
        }
    }

    /// The result's confidence as a phrase, for the two places that speak it.
    private var confidencePhrase: String { snapConfidencePhrase(result.confidence) }

    private var quickGuess: Double? { GuessScoring.parse(guessText) }

    private var quickVerdict: String? {
        guard revealed, let quickGuess else { return nil }
        // Scored against the range as it stood at the reveal — see
        // `revealedRange`. The fallback covers the reveal itself, where the
        // frozen range and the live one are the same number anyway.
        let scored = revealedRange
            ?? (low: result.displayValueLow, high: result.displayValueHigh)
        return GuessScoring.verdict(guess: quickGuess, low: scored.low,
                                    high: scored.high)
    }

    /// The moment before the number. The range is under a solid cover with an
    /// optional guess; one tap on Reveal springs it in with a haptic.
    private var coveredValueCard: some View {
        VStack(spacing: 14) {
            Text("What do you think it could resell for?")
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)

            ZStack {
                ValueRangeView(low: result.displayValueLow, high: result.displayValueHigh)
                    .blur(radius: 18)
                    .opacity(0.25)
                    .accessibilityHidden(true)
                Text("$ ? ? ?")
                    .font(.fraunces(34, weight: .bold, relativeTo: .largeTitle))
                    .foregroundStyle(Color.snapWarmGray)
                    .accessibilityHidden(true)
            }
            .frame(maxWidth: .infinity)

            HStack(spacing: 6) {
                Text("$")
                    .font(.dmSans(17, weight: .medium))
                    .foregroundStyle(Color.snapWarmGray)
                    .accessibilityHidden(true)
                TextField("Your guess (optional)", text: $guessText)
                    .keyboardType(.decimalPad)
                    .font(.dmSans(17, weight: .medium))
                    .foregroundStyle(Color.snapEspresso)
                    .focused(focus, equals: .guess)
                    .accessibilityLabel("Your guess in dollars, optional")
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(Color.snapBackground)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))

            PrimaryButton(title: "Reveal the estimate") { revealPrice() }
        }
        .padding(20)
        .frame(maxWidth: .infinity)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .shadow(color: Color.snapCardShadow.opacity(0.12), radius: 24, x: 0, y: 8)
        .accessibilitySortPriority(100)
    }

    private var revealedValueCard: some View {
        VStack(spacing: 16) {
            VStack(spacing: 6) {
                Text("Estimated Resale Value")
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)

                ValueRangeView(low: result.displayValueLow, high: result.displayValueHigh)
            }

            if let quickVerdict {
                Text(quickVerdict)
                    .font(.dmSans(15, weight: .semibold))
                    .foregroundStyle(Color.snapEspresso)
                    .multilineTextAlignment(.center)
                    .transition(.opacity)
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
        // The headline result: one VoiceOver stop that states the value, its
        // confidence, and that it's an estimate — rather than four fragments.
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Estimated resale value")
        .accessibilityValue(
            result.valuationSource.spokenSummary(
                range: result.formattedRange, confidence: confidencePhrase)
            + (quickVerdict.map { " \($0)" } ?? "")
        )
        // Read first when the sheet opens — it is why the user is here.
        .accessibilitySortPriority(100)
        .accessibilityAddTraits(.isSummaryElement)
    }

    // `@MainActor` explicitly: this mutates view state, runs an animation and
    // posts an accessibility announcement, and the SDK's isolation on
    // `UIAccessibility.post` has moved between Xcode versions. The only caller
    // is the reveal button's action, formed in `body`, so it is already on the
    // main actor — the annotation just says so where the compiler can check it.
    @MainActor
    private func revealPrice() {
        guard !revealed else { return }
        focus.wrappedValue = nil
        // Freeze what the verdict is scored against before the range is free
        // to move again — see `revealedRange`.
        revealedRange = (low: result.displayValueLow, high: result.displayValueHigh)
        withAnimation(reduceMotion ? .easeInOut(duration: 0.2)
                                   : .spring(response: 0.45, dampingFraction: 0.62)) {
            revealed = true
        }
        Haptics.success()
        // The number the whole flow exists for, spoken.
        //
        // The button the user just activated lives inside the card that
        // disappears, so VoiceOver focus is destroyed and nothing is
        // announced. `.isSummaryElement` and `.accessibilitySortPriority`
        // above affect ordering and screen summaries, not announcements — the
        // card reads correctly if you navigate to it, and a VoiceOver user is
        // given no reason to think there is anything to navigate to.
        //
        // `GuessFirst.defaultOn` is true, so this is the default path on every
        // fresh scan: a VoiceOver user meets it on their first result. Every
        // other state change on this sheet already announces — the condition
        // chip, the status chip, and the tag re-read, that last one added
        // because "a haptic is the whole of the feedback... and said nothing
        // at all to VoiceOver". The same wording as the card's own
        // `accessibilityValue`, so the announcement and the element agree.
        UIAccessibility.post(
            notification: .announcement,
            argument: result.valuationSource.revealAnnouncement(
                range: result.formattedRange, confidence: confidencePhrase)
                + (quickVerdict.map { " \($0)" } ?? "")
        )
        Analytics.shared.track(.guessRevealed(withGuess: quickGuess != nil))
    }
}
