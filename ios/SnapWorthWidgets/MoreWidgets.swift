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

    /// Medium fits two rows at a legible size; large fits the four the writer
    /// stores. Asking for more than `maxRecentFinds` would silently render
    /// short, so the cap is read rather than assumed.
    private var rowCount: Int {
        min(family == .systemLarge ? WidgetBridge.maxRecentFinds : 2,
            WidgetBridge.maxRecentFinds)
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
                    .font(.system(size: 11, weight: .semibold, design: .serif))
                    .foregroundStyle(Color.wBackground.opacity(0.7))
                Spacer()
                if haul.hasScans {
                    Text(haul.formattedRange)
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Color.wSage)
                }
            }
            .padding(.bottom, 8)

            if rows.isEmpty {
                Spacer()
                Text("Nothing scanned yet")
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(Color.wWarmGray)
                Spacer()
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(rows) { find in
                        HStack(alignment: .firstTextBaseline, spacing: 6) {
                            Text(find.name)
                                .font(.system(size: 13, weight: .medium))
                                .foregroundStyle(Color.wBackground)
                                .lineLimit(1)
                                .truncationMode(.tail)
                            Spacer(minLength: 4)
                            Text(find.range)
                                .font(.system(size: 13, weight: .semibold, design: .rounded))
                                .foregroundStyle(Color.wSage)
                                .lineLimit(1)
                                .layoutPriority(1)
                        }
                        .accessibilityElement(children: .combine)
                    }
                }
                Spacer(minLength: 0)
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
                        .font(.system(size: 11, weight: .semibold))
                    Text(state.circularValue)
                        .font(.system(size: 16, weight: .bold, design: .rounded))
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
                        .font(.system(size: 11, weight: .semibold, design: .serif))
                        .foregroundStyle(Color.wBackground.opacity(0.7))
                }
                Spacer()
                Text(state.headline)
                    .font(.system(size: haul.isPro ? 18 : 30, weight: .bold, design: .rounded))
                    .foregroundStyle(accentForRemaining)
                    .minimumScaleFactor(0.5)
                    .lineLimit(1)
                Text(state.subtitle)
                    .font(.system(size: 11, weight: .medium))
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
                    .font(.system(size: 11, weight: .semibold, design: .serif))
                    .foregroundStyle(Color.wBackground.opacity(0.7))
            }
            Spacer()
            Text(value)
                .font(.system(size: 24, weight: .bold, design: .rounded))
                .foregroundStyle(colour)
                .minimumScaleFactor(0.5)
                .lineLimit(1)
            Text(caption)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(Color.wWarmGray)
                .lineLimit(2)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(spoken)
    }

    // `monthProfit` is nil for a free user by construction — the writer never
    // stores it — so this reads as the upsell rather than as zero profit,
    // which would be a lie about their ledger.
    /// Read through the entry's date, so the figure disappears when the month
    /// it belongs to ends. It was a bare `Double` computed against the month
    /// that was current at *write* time, rendered under a header hardcoded to
    /// "This month" — so a Pro user who sold six items in September and did
    /// not open the app saw "$214 · from 6 flips · This month" on 3 October,
    /// while the Flips screen correctly showed October at $0.
    private var profit: Double? { haul.monthProfit(at: now) }
    private var flips: Int { haul.monthFlips(at: now) }

    private var value: String {
        guard let profit else { return haul.isPro ? "—" : "Pro" }
        return WidgetHaulData.compactMoney(profit)
    }

    private var colour: Color {
        guard let profit else { return Color.wWarmGray }
        return profit < 0 ? Color.wTerracotta : Color.wSage
    }

    private var caption: String {
        guard profit != nil else {
            return haul.isPro ? "No flips sold yet this month"
                              : "Track profit with Pro"
        }
        return "from \(flips) flip\(flips == 1 ? "" : "s")"
    }

    private var spoken: String {
        guard let profit else {
            return haul.isPro ? "No flips sold yet this month"
                              : "Profit tracking is a Pro feature"
        }
        return "\(WidgetHaulData.compactMoney(profit)) profit this month "
             + "from \(flips) flip\(flips == 1 ? "" : "s")"
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

private extension Image {
    func snapWidgetIcon() -> some View {
        self.font(.system(size: 11, weight: .semibold))
            .foregroundStyle(Color.wTerracotta)
    }
}
