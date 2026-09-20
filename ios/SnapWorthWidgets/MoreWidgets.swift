import SwiftUI
import WidgetKit

// ── Shared timeline ──────────────────────────────────────────────────────────
//
// All three widgets below read the same blob, so they share a provider rather
// than each declaring an identical one. The app calls `reloadAllTimelines()`
// after every scan; the hourly policy only covers the case where nothing
// happened.

struct HaulOnlyEntry: TimelineEntry {
    let date: Date
    let haul: WidgetHaulData
}

struct HaulOnlyProvider: TimelineProvider {
    func placeholder(in context: Context) -> HaulOnlyEntry {
        HaulOnlyEntry(date: .now, haul: .placeholder)
    }

    func getSnapshot(in context: Context, completion: @escaping (HaulOnlyEntry) -> Void) {
        completion(HaulOnlyEntry(date: .now,
                                 haul: context.isPreview ? .placeholder : WidgetReader.readHaul()))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<HaulOnlyEntry>) -> Void) {
        // One entry now, plus one at each instant a stored snapshot stops
        // being true — the next UTC midnight, the next local midnight, the
        // start of the next month. The blob is the same in all of them; what
        // changes is the entry's date, which is what the views ask about. With
        // only the hourly policy, every refresh re-read the same frozen number
        // and the correction waited for the app to run.
        let now = Date.now
        let haul = WidgetReader.readHaul()
        let entries = [HaulOnlyEntry(date: now, haul: haul)]
            + WidgetHaulData.refreshBoundaries(after: now)
                .map { HaulOnlyEntry(date: $0, haul: haul) }
        let next = Calendar.current.date(byAdding: .hour, value: 1, to: now)!
        completion(Timeline(entries: entries, policy: .after(next)))
    }
}

// ── Recent finds ─────────────────────────────────────────────────────────────

struct RecentFindsView: View {
    let haul: WidgetHaulData
    @Environment(\.widgetFamily) private var family

    /// Large takes every row the writer stores; medium stays at two.
    ///
    /// Large asked for four, which is what `maxRecentFinds` was, and four 13pt
    /// rows stacked at the top of a 345pt tile filled about a third of it —
    /// the rest was the `Spacer` below them, so the widget ended in a blank
    /// half with nothing in it. The writer now stores six (see
    /// `WidgetBridge.maxRecentFinds`) and the rows share the height rather
    /// than piling up against the header.
    ///
    /// Medium was raised to three in the same change and put back: the height
    /// distribution below already fills that tile at two, so the extra row
    /// changed what a medium widget *shows* to fix something that was not
    /// about medium. It is a separate decision, and it needs its own reason.
    ///
    /// No `min` against the cap. Both arms are already within it, and
    /// `recentRows(limit:)` takes a `prefix`, which is total — asking for more
    /// than is stored returns what exists rather than rendering short.
    private var rowCount: Int {
        family == .systemLarge ? WidgetBridge.maxRecentFinds : 2
    }

    /// Derived in the shared model so a test can reach it — see
    /// `WidgetHaulData.recentRows(limit:)` for why the v1 fallback exists.
    private var rows: [WidgetFind] { haul.recentRows(limit: rowCount) }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 4) {
                Image(systemName: "clock.arrow.circlepath")
                    .snapWidgetIcon()
                Text("Recent finds")
                    .wFont(11, weight: .semibold, design: .serif)
                    .foregroundStyle(Color.wBackground.opacity(0.7))
                Spacer()
                if haul.hasScans {
                    // The figure is the whole library, not these rows. The
                    // list under it shows only the most recent few — see
                    // `rowCount`, which is the authority and has changed
                    // twice — so an unlabelled total sat directly above rows
                    // that visibly do not sum to it and read as an arithmetic
                    // error in the user's own ledger. Starkest on a 1.3.x
                    // blob, where the v1 fallback draws exactly one row. The
                    // spoken label has always said "Haul worth …"; this is the
                    // sighted half of it.
                    HStack(spacing: 3) {
                        Text("Haul")
                            .wFont(11, weight: .medium)
                            .foregroundStyle(Color.wBackground.opacity(0.7))
                        Text(haul.formattedRange)
                            .wFont(11, weight: .semibold)
                            .foregroundStyle(Color.wSage)
                    }
                    .lineLimit(1)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(String(localized: "Haul worth \(haul.spokenRange)"))
                }
            }
            .padding(.bottom, 8)

            if rows.isEmpty {
                Spacer()
                Text("Nothing scanned yet")
                    .wFont(13, weight: .medium)
                    .foregroundStyle(Color.wWarmGray)
                Spacer()
            } else {
                // Each row takes an equal share of what is left instead of a
                // fixed 13pt line, and the list claims the whole remaining
                // height rather than handing it to a trailing `Spacer`. That
                // spacer is what left the bottom of the large tile empty: the
                // rows were laid out at their intrinsic height, and everything
                // under them was blank. Rows separated by a hairline so an
                // evenly distributed list still reads as a list.
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(rows.enumerated()), id: \.element.id) { index, find in
                        if index > 0 {
                            Rectangle()
                                .fill(Color.wBackground.opacity(0.12))
                                .frame(height: 0.5)
                        }
                        HStack(alignment: .firstTextBaseline, spacing: 6) {
                            Text(find.name)
                                .wFont(13, weight: .medium)
                                .foregroundStyle(Color.wBackground)
                                .lineLimit(1)
                                .truncationMode(.tail)
                            Spacer(minLength: 4)
                            Text(find.range)
                                .wFont(13, weight: .semibold, design: .rounded)
                                .foregroundStyle(Color.wSage)
                                .lineLimit(1)
                                .layoutPriority(1)
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity,
                               alignment: .leading)
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel(
                            String(localized: "\(find.name), \(WidgetHaulData.spoken(find.range))"))
                    }
                }
                .frame(maxHeight: .infinity)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

struct RecentFindsWidget: Widget {
    let kind = "RecentFindsWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: HaulOnlyProvider()) { entry in
            RecentFindsView(haul: entry.haul)
                .widgetURL(URL(string: "snapworth://history"))
                .containerBackground(Color.wCharcoal, for: .widget)
        }
        .configurationDisplayName("Recent finds")
        .description("The last few things you scanned, and what they're worth.")
        .supportedFamilies([.systemMedium, .systemLarge])
    }
}

// ── Scans left ───────────────────────────────────────────────────────────────

struct ScansLeftView: View {
    let haul: WidgetHaulData
    /// The timeline entry's date, not `Date.now`: an entry scheduled at the
    /// UTC reset has to render the reset allowance, and it is rendered by the
    /// system at that instant without this code running again.
    let now: Date
    @Environment(\.widgetFamily) private var family

    var body: some View {
        switch family {
        case .accessoryCircular:
            ZStack {
                AccessoryWidgetBackground()
                VStack(spacing: 0) {
                    Image(systemName: "camera.viewfinder")
                        .wFont(11, weight: .semibold)
                    Text(state.circularValue)
                        .wFont(16, weight: .bold, design: .rounded)
                        .minimumScaleFactor(0.5)
                        .lineLimit(1)
                }
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(state.spoken)

        default:
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 4) {
                    Image(systemName: "camera.viewfinder").snapWidgetIcon()
                    Text("SnapWorth")
                        .wFont(11, weight: .semibold, design: .serif)
                        .foregroundStyle(Color.wBackground.opacity(0.7))
                }
                Spacer()
                Text(state.headline)
                    // One size for every state. The Pro branch used to print
                    // "5-day streak" here and had to shrink to 18pt to fit it;
                    // the headline is a single glyph or a single digit now, so
                    // the free tier's size is the right one for all of them.
                    .wFont(30, weight: .bold, design: .rounded)
                    .foregroundStyle(accentForRemaining)
                    .minimumScaleFactor(0.5)
                    .lineLimit(1)
                Text(state.subtitle)
                    .wFont(11, weight: .medium)
                    .foregroundStyle(Color.wWarmGray)
                    .lineLimit(2)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(state.spoken)
        }
    }

    /// Every string comes from the shared model — see `WidgetHaulData.ScansLeft`
    /// for why nil is a third state rather than zero.
    private var state: WidgetHaulData.ScansLeft { haul.scansLeft(at: now) }

    private var accentForRemaining: Color {
        // Neutral unless the count is known and spent: terracotta here reads
        // as "you are out", which is wrong for an allowance nobody has touched.
        state.isSpent ? Color.wTerracotta : Color.wSage
    }
}

struct ScansLeftWidget: Widget {
    let kind = "ScansLeftWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: HaulOnlyProvider()) { entry in
            ScansLeftEntryView(entry: entry)
                .widgetURL(URL(string: "snapworth://scan"))
        }
        .configurationDisplayName("Scans left")
        .description("How many free scans you have today.")
        .supportedFamilies([.systemSmall, .accessoryCircular])
    }
}

/// The container background differs by family: opaque on the Home Screen,
/// clear on the Lock Screen, where a filled rectangle renders as a block over
/// the wallpaper. Split out because a `Widget` body cannot read the
/// environment.
struct ScansLeftEntryView: View {
    let entry: HaulOnlyEntry
    @Environment(\.widgetFamily) private var family

    var body: some View {
        if family == .accessoryCircular {
            ScansLeftView(haul: entry.haul, now: entry.date)
                .containerBackground(.clear, for: .widget)
        } else {
            ScansLeftView(haul: entry.haul, now: entry.date)
                .containerBackground(Color.wCharcoal, for: .widget)
        }
    }
}

// ── Profit, month to date ────────────────────────────────────────────────────

struct MonthProfitView: View {
    let haul: WidgetHaulData
    /// The timeline entry's date. The header says "This month"; this is how the
    /// view knows which month that is.
    let now: Date

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 4) {
                Image(systemName: "chart.line.uptrend.xyaxis").snapWidgetIcon()
                Text("This month")
                    .wFont(11, weight: .semibold, design: .serif)
                    .foregroundStyle(Color.wBackground.opacity(0.7))
            }
            Spacer()
            Text(value)
                .wFont(24, weight: .bold, design: .rounded)
                .foregroundStyle(colour)
                .minimumScaleFactor(0.5)
                .lineLimit(1)
            Text(caption)
                .wFont(11, weight: .medium)
                .foregroundStyle(Color.wWarmGray)
                .lineLimit(2)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(spoken)
    }

    // No tier check anywhere in this view. `monthProfit` is nil when nothing
    // has been sold this month or when nothing sold has a paid price, and it
    // means those two things for everyone: the app gives free users this same
    // month-scoped figure on the Flips tab, so a widget that answered "Pro"
    // was upselling a feature they already had — and saying the same thing to
    // a lapsed subscriber, whose ledger had not changed at all.
    /// Read through the entry's date, so the figure disappears when the month
    /// it belongs to ends. It was a bare `Double` computed against the month
    /// that was current at *write* time, rendered under a header hardcoded to
    /// "This month" — so a Pro user who sold six items in September and did
    /// not open the app saw "$214 · from 6 flips · This month" on 3 October,
    /// while the Flips screen correctly showed October at $0.
    private var profit: Double? { haul.monthProfit(at: now) }
    private var flips: Int { haul.monthFlips(at: now) }
    /// Everything sold, cost basis or not. `flips` counts only what could be
    /// priced, so these differ exactly when a sale has no paid price.
    private var sold: Int { haul.monthSold(at: now) }

    private var value: String {
        guard let profit else { return "—" }
        return WidgetHaulData.compactMoney(profit)
    }

    private var colour: Color {
        guard let profit else { return Color.wWarmGray }
        return profit < 0 ? Color.wTerracotta : Color.wSage
    }

    /// Three states, not two. A nil profit used to mean only one thing here —
    /// "nothing sold" — and it means two: nothing sold, or things sold that
    /// nobody entered a paid price for. `paidPrice` is optional and the app
    /// advertises one benefit for filling it in, so the second is the common
    /// case, and the widget was flatly contradicting the Flips screen on the
    /// same data: "No flips sold yet this month" beside "2 items sold".
    private var caption: String {
        guard profit != nil else {
            return sold > 0
                ? String(localized: "\(sold) sold · add what you paid")
                : String(localized: "No flips sold yet this month")
        }
        return String(localized: "from \(flips) flips")
    }

    private var spoken: String {
        guard let profit else {
            guard sold > 0 else { return String(localized: "No flips sold yet this month") }
            let count = String(localized: "\(sold) flips sold this month")
            return String(localized: "\(count), profit unknown until you add what you paid")
        }
        let from = String(localized: "from \(flips) flips")
        return String(localized:
            "\(WidgetHaulData.compactMoney(profit)) profit this month \(from)")
    }
}

struct MonthProfitWidget: Widget {
    let kind = "MonthProfitWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: HaulOnlyProvider()) { entry in
            MonthProfitView(haul: entry.haul, now: entry.date)
                .widgetURL(URL(string: "snapworth://flips"))
                .containerBackground(Color.wCharcoal, for: .widget)
        }
        .configurationDisplayName("Profit this month")
        .description("What your sold flips actually made.")
        .supportedFamilies([.systemSmall])
    }
}

// ── Shared bits ──────────────────────────────────────────────────────────────

/// A fixed point size that follows the user's text-size setting.
///
/// `Font.system(size:weight:design:)` is a fixed point size and does not
/// respond to Larger Text, and every `Text` and `Image` in all eight widget
/// views used it — so at AX1-AX5 every other element on the Lock Screen and
/// Home Screen grew and SnapWorth's rendered byte-identically to the default,
/// leaving 10pt and 11pt captions. The accessory families are the clearest
/// case, since the system sizes those for the user and this code overrode it.
/// The app itself does the opposite deliberately: its type ramp anchors every
/// alias to a `TextStyle` and `snapSymbol` uses `@ScaledMetric` so icons track
/// growing labels. The extension was the one surface that opted out.
///
/// There is no `Font.system(size:relativeTo:)` — `relativeTo` exists only on
/// `.custom`, for a named face. `@ScaledMetric` is the supported way to scale
/// a point size against a text style, and it is what `snapSymbol` already
/// uses. At the default text size it returns the base value unchanged, so
/// every widget renders exactly as it did.
struct WidgetScaledFont: ViewModifier {
    @ScaledMetric private var size: CGFloat
    private let weight: Font.Weight
    private let design: Font.Design

    init(size: CGFloat, weight: Font.Weight, design: Font.Design) {
        _size = ScaledMetric(wrappedValue: size, relativeTo: Self.style(for: size))
        self.weight = weight
        self.design = design
    }

    /// The text style whose own default size is nearest the requested one, so
    /// a 24pt figure grows at a headline's rate and an 11pt caption at a
    /// caption's — which are different rates, and the reason this is a lookup
    /// rather than one style for everything.
    ///
    /// `if` rather than a `switch` over ranges: a range pattern here would be
    /// matching `CGFloat` against `Double` literals and leaning on the 64-bit
    /// typealias to make them the same type.
    static func style(for size: CGFloat) -> Font.TextStyle {
        if size < 11.5 { return .caption2 }      // 11
        if size < 12.5 { return .caption }       // 12
        if size < 14   { return .footnote }      // 13
        if size < 15.5 { return .subheadline }   // 15
        if size < 18   { return .callout }       // 16
        if size < 21   { return .title3 }        // 20
        if size < 25   { return .title2 }        // 22
        return .title                            // 28
    }

    func body(content: Content) -> some View {
        content
            .font(.system(size: size, weight: weight, design: design))
            // Widgets have a hard size budget, so growth has to be allowed to
            // give way rather than clip. Inert at the default size — nothing
            // is constrained there — and it is the difference between a
            // caption that shrinks to fit and one that truncates mid-word.
            .minimumScaleFactor(0.7)
    }
}

extension View {
    /// Use instead of `.font(.system(size:weight:design:))` anywhere in the
    /// widget extension. See `WidgetScaledFont`.
    func wFont(_ size: CGFloat, weight: Font.Weight = .regular,
               design: Font.Design = .default) -> some View {
        modifier(WidgetScaledFont(size: size, weight: weight, design: design))
    }
}

private extension Image {
    func snapWidgetIcon() -> some View {
        self.wFont(11, weight: .semibold)
            .foregroundStyle(Color.wTerracotta)
    }
}
