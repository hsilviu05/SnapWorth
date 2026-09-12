import SwiftUI
import WidgetKit

// ── Lock Screen ───────────────────────────────────────────────────────────────
//
// A separate widget kind rather than extra families on `HaulWidget`, for two
// reasons. It gets its own gallery entry, so someone browsing Lock Screen
// widgets sees a name that describes what they will get; and the Lock Screen
// renders in `.vibrant`, where the app's palette is flattened to monochrome —
// sharing a configuration would mean every view branching on family to undo a
// `containerBackground` that only makes sense on the Home Screen.

struct LockScreenEntry: TimelineEntry {
    let date: Date
    let haul: WidgetHaulData
}

struct LockScreenProvider: TimelineProvider {
    func placeholder(in context: Context) -> LockScreenEntry {
        LockScreenEntry(date: .now, haul: .placeholder)
    }

    func getSnapshot(in context: Context, completion: @escaping (LockScreenEntry) -> Void) {
        completion(LockScreenEntry(
            date: .now,
            haul: context.isPreview ? .placeholder : WidgetReader.readHaul()))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<LockScreenEntry>) -> Void) {
        // Same cadence as the Home Screen widgets: the app calls
        // `reloadAllTimelines()` after every scan, so this hourly refresh only
        // has to cover the case where nothing happened.
        // Plus an entry at each instant a stored snapshot expires — the
        // streak is the one that matters here, and it lapses at a local
        // midnight the hourly policy knows nothing about.
        let now = Date.now
        let haul = WidgetReader.readHaul()
        let entries = [LockScreenEntry(date: now, haul: haul)]
            + WidgetHaulData.refreshBoundaries(after: now)
                .map { LockScreenEntry(date: $0, haul: haul) }
        let next = Calendar.current.date(byAdding: .hour, value: 1, to: now)!
        completion(Timeline(entries: entries, policy: .after(next)))
    }
}

// ── Inline ────────────────────────────────────────────────────────────────────

/// Sits beside the time. One line, system-styled — a custom font or colour
/// here is ignored, so nothing tries.
struct LockScreenInlineView: View {
    let haul: WidgetHaulData

    var body: some View {
        Group {
            if haul.hasScans {
                Label(haul.compactRange, systemImage: "camera.viewfinder")
            } else {
                Label("No finds yet", systemImage: "camera.viewfinder")
            }
        }
        // `compactRange` is abbreviated display text — "$348–$620" becomes
        // "$348K–$620K" territory in a circular, and the dash is not spoken.
        // The inline had no label at all, so VoiceOver read the symbol name
        // and then the abbreviation as one run-together number.
        .accessibilityLabel(haul.hasScans
                            ? "SnapWorth haul, \(haul.spokenRange)"
                            : "SnapWorth, no finds scanned yet")
    }
}

// ── Circular ──────────────────────────────────────────────────────────────────

struct LockScreenCircularView: View {
    let haul: WidgetHaulData

    var body: some View {
        ZStack {
            // Gives the complication a legible ground on a busy wallpaper.
            AccessoryWidgetBackground()
            VStack(spacing: 0) {
                Image(systemName: "camera.viewfinder")
                    .font(.system(size: 11, weight: .semibold))
                Text(haul.hasScans ? haul.compactTotal : "—")
                    .font(.system(size: 15, weight: .bold, design: .rounded))
                    .minimumScaleFactor(0.5)
                    .lineLimit(1)
            }
            .padding(.horizontal, 2)
        }
        // One accessibility label for the whole complication: read as two
        // fragments it announces an icon and a number with no relationship.
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(haul.hasScans
                            ? "Haul value \(haul.spokenRange), \(haul.findsLabel)"
                            : "No finds scanned yet")
    }
}

// ── Rectangular ───────────────────────────────────────────────────────────────

struct LockScreenRectangularView: View {
    let haul: WidgetHaulData
    /// The timeline entry's date. `streak` is a snapshot the app took, and
    /// `ScanStreak.current()` would return 0 once the last scan is older than
    /// yesterday — a test the widget can only run if it knows when "now" is.
    /// Without it, a 5-day streak kept reading "12 finds · 5-day streak" on
    /// the Lock Screen all weekend.
    let now: Date

    /// Zero once the streak has lapsed, whatever the stored count says.
    private var streak: Int { haul.liveStreak(at: now) }

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Label("SnapWorth", systemImage: "camera.viewfinder")
                .font(.system(size: 12, weight: .semibold))
                // The one element the tint applies to, so the value below stays
                // readable in every wallpaper's accent colour.
                .widgetAccentable()

            if haul.hasScans {
                Text(haul.formattedRange)
                    .font(.system(size: 15, weight: .bold, design: .rounded))
                    .minimumScaleFactor(0.6)
                    .lineLimit(1)

                Text(streak > 1
                     ? "\(haul.findsLabel) · \(streak)-day streak"
                     : haul.findsLabel)
                    .font(.system(size: 12))
            } else {
                Text("No finds yet")
                    .font(.system(size: 15, weight: .semibold, design: .rounded))
                Text("Scan something to start")
                    .font(.system(size: 12))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        // `.combine` alone pulled in the SF Symbol's name and read the middle
        // dot between the find count and the streak as a word, so the
        // complication announced an icon name and two unrelated numbers.
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(spokenLabel)
    }

    private var spokenLabel: String {
        guard haul.hasScans else {
            return "SnapWorth, no finds yet. Scan something to start."
        }
        let body = "SnapWorth haul, \(haul.spokenRange), \(haul.findsLabel)"
        return streak > 1 ? "\(body), \(streak) day streak" : body
    }
}

// ── Widget ────────────────────────────────────────────────────────────────────

struct LockScreenHaulWidget: Widget {
    let kind = "LockScreenHaulWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: LockScreenProvider()) { entry in
            LockScreenHaulEntryView(entry: entry)
                .widgetURL(URL(string: "snapworth://history"))
                // Deliberately clear. An opaque background is what the Home
                // Screen wants; on the Lock Screen it renders as a solid block
                // over the wallpaper.
                .containerBackground(.clear, for: .widget)
        }
        .configurationDisplayName("Haul on the Lock Screen")
        .description("Your total resale value, without unlocking.")
        .supportedFamilies([.accessoryInline, .accessoryCircular, .accessoryRectangular])
    }
}

struct LockScreenHaulEntryView: View {
    let entry: LockScreenEntry
    @Environment(\.widgetFamily) private var family

    var body: some View {
        switch family {
        case .accessoryInline:
            LockScreenInlineView(haul: entry.haul)
        case .accessoryRectangular:
            LockScreenRectangularView(haul: entry.haul, now: entry.date)
        default:
            LockScreenCircularView(haul: entry.haul)
        }
    }
}
