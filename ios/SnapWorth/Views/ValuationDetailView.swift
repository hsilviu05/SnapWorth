import PhotosUI
import SwiftData
import SwiftUI

// MARK: - Valuation detail panel (#87)

/// Renders a `ValuationDetail`. Every section is conditional on its data, so a
/// thin response shows a thin panel rather than empty headings. Copy rule:
/// "estimate", never "worth" or "sells for" — the same line marketing holds.
/// And never a server token as text: an enum field is shown through its
/// client label (`ValuationDetail.facts`, `authenticityRead`, `marketRead`),
/// and demand and supply only as the AI's read, never as market fact.
struct ValuationDetailView: View {
    let detail: ValuationDetail

    /// The condition scaling to apply to the ladder, so it agrees with the
    /// headline range this panel exists to explain. 1 when the user has not
    /// corrected the AI's grade, which is the common case.
    var priceFactor: Decimal = 1

    /// Whether the AI's grade has been overridden, so the facts row stops
    /// asserting it as the item's current condition.
    var gradeWasOverridden: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if !detail.ladder.isEmpty { ladder }
            if detail.confidenceScore != nil || !detail.confidenceReasons.isEmpty { confidence }
            bullets("What drives the value", detail.valueDrivers, icon: "arrow.up.right")
            bullets("What we assumed", detail.assumptions, icon: "questionmark.circle")
            bullets("Sharpen this estimate", detail.improveEstimate, icon: "camera.viewfinder")
            if let read = detail.authenticityRead { authenticity(read) }
            factsRow
        }
    }

    // ── Price ladder ──

    private var ladder: some View {
        HStack(alignment: .top, spacing: 0) {
            ForEach(Array(detail.ladder.enumerated()), id: \.offset) { _, row in
                VStack(spacing: 3) {
                    Text(Self.money(scaled(row.value)))
                        .font(.fraunces(20, weight: .bold, relativeTo: .title3))
                        .foregroundStyle(row.isExpected ? Color.snapSageText : Color.snapEspresso)
                        .lineLimit(1)
                        .minimumScaleFactor(0.6)
                    Text(row.label)
                        .font(.snapCaption)
                        .foregroundStyle(Color.snapWarmGray)
                }
                .frame(maxWidth: .infinity)
                .accessibilityElement(children: .combine)
                .accessibilityLabel(String(localized: "\(row.label) \(Self.money(scaled(row.value)))"))
            }
        }
        .padding(.vertical, 12)
        .background(Color.snapBackground)
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Price points")
    }

    // ── Confidence ──

    private var confidence: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                if let score = detail.confidenceScore {
                    Text("\(score)")
                        .font(.fraunces(22, weight: .bold, relativeTo: .title2))
                        .foregroundStyle(Color.snapEspresso)
                    Text("/ 100 confidence")
                        .font(.snapCaption)
                        .foregroundStyle(Color.snapWarmGray)
                } else {
                    Text("Confidence")
                        .snapSectionHeader()
                }
            }
            if let summary = detail.confidenceSummary, !summary.isEmpty {
                Text(summary)
                    .font(.snapBody)
                    .foregroundStyle(Color.snapEspresso)
                    .fixedSize(horizontal: false, vertical: true)
            }
            // A neutral mark, not a checkmark: the server sends the *weakest*
            // signals here (`confidence.py`), so a tick beside "the brand
            // could not be identified" endorsed the problem it names.
            ForEach(Array(detail.shownConfidenceReasons().prefix(3).enumerated()), id: \.offset) { _, reason in
                bullet(reason, icon: "info.circle")
            }
        }
    }

    // ── Generic bullet sections ──

    @ViewBuilder
    private func bullets(_ title: LocalizedStringKey, _ items: [String], icon: String) -> some View {
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                Text(title)
                    .snapSectionHeader()
                ForEach(Array(items.prefix(4).enumerated()), id: \.offset) { _, item in
                    bullet(item, icon: icon)
                }
            }
        }
    }

    private func bullet(_ text: String, icon: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: icon)
                .snapSymbol(13, weight: .semibold)
                .foregroundStyle(Color.snapTerracottaText)
                .accessibilityHidden(true)
            Text(text)
                .font(.snapBody)
                .foregroundStyle(Color.snapEspresso)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // ── Authenticity: an observation about the photo, never a verdict ──

    private func authenticity(_ read: AuthenticityRead) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("What the photo suggests about authenticity")
                .snapSectionHeader()
            Text(read.label)
                .font(.dmSans(15, weight: .semibold))
                .foregroundStyle(Color.snapEspresso)
            if let why = detail.authenticityReasoning, !why.isEmpty {
                Text(why)
                    .font(.snapBody)
                    .foregroundStyle(Color.snapWarmGray)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text("From this photo only — not a certification.")
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)
        }
    }

    // ── Facts ──

    /// The server's price point, re-scaled to the condition on screen.
    ///
    /// `Decimal` throughout, like every other money path in the app, then back
    /// to `Double` only for the formatter this view already uses.
    private func scaled(_ value: Double) -> Double {
        guard priceFactor != 1, value.isFinite else { return value }
        return NSDecimalNumber(decimal: Decimal(value) * priceFactor).doubleValue
    }

    // `@ViewBuilder` belongs to `factsRow`: its body is a bare `if` with no
    // `else`, so without the builder there is nothing to return. Inserting
    // `scaled` above without noticing the attribute is what broke the build.
    @ViewBuilder
    private var factsRow: some View {
        // The grade is the AI's read. Once the user has corrected it, printing
        // it bare claims it as the item's condition — while the chips directly
        // below say otherwise. Labelled rather than dropped: what the model
        // thought it was looking at is the most useful fact in the row, and it
        // is why the estimate started where it did.
        let facts = gradeWasOverridden ? detail.factsWithReadGrade : detail.facts
        let market = detail.marketRead
        if !facts.isEmpty || market != nil {
            VStack(alignment: .leading, spacing: 4) {
                if !facts.isEmpty {
                    Text(facts.joined(separator: " · "))
                        .font(.snapCaption)
                        .foregroundStyle(Color.snapWarmGray)
                }
                if let market {
                    Text(market)
                        .font(.snapCaption)
                        .foregroundStyle(Color.snapWarmGray)
                }
            }
        }
    }

    static func money(_ value: Double) -> String {
        NumberFormatter.snapCurrency.string(from: NSNumber(value: value)) ?? "$\(Int(value))"
    }
}
