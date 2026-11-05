import SwiftUI
import WidgetKit

// ── Daily guess ──────────────────────────────────────────────────────────────

/// The guess-the-price game's streak, on the Home Screen: whether today's
/// round is still waiting, and how many days in a row have been played.
/// Tapping opens the game.
///
/// No item and no price: the rounds are bundled in the app, not here, and a
/// widget that named today's item would give part of the answer away.

struct DailyGuessEntry: TimelineEntry {
    let date: Date
    let guess: WidgetGuessData
}

struct DailyGuessProvider: TimelineProvider {
    func placeholder(in context: Context) -> DailyGuessEntry {
        DailyGuessEntry(date: .now, guess: WidgetGuessData(lastDay: 0, streak: 4))
    }

    func getSnapshot(in context: Context, completion: @escaping (DailyGuessEntry) -> Void) {
        completion(context.isPreview ? placeholder(in: context)
                                     : DailyGuessEntry(date: .now, guess: Self.read()))
    }

    /// Now, and each of the next two local midnights: the day the entry
    /// stands for is what turns "played" back into "waiting", and a missed
    /// day into a lost streak, with no app launch in between.
    func getTimeline(in context: Context, completion: @escaping (Timeline<DailyGuessEntry>) -> Void) {
        let now = Date.now
        let guess = Self.read()
        let calendar = Calendar.current
        let today = calendar.startOfDay(for: now)
        let midnights = (1...2).compactMap { calendar.date(byAdding: .day, value: $0, to: today) }
        let entries = [DailyGuessEntry(date: now, guess: guess)]
            + midnights.map { DailyGuessEntry(date: $0, guess: guess) }
        completion(Timeline(entries: entries, policy: .after(midnights.last ?? now.addingTimeInterval(3600))))
    }

    static func read() -> WidgetGuessData {
        guard
            let suite = UserDefaults(suiteName: WidgetBridge.appGroupID),
            let data  = suite.data(forKey: WidgetBridge.guessKey),
            let guess = try? JSONDecoder().decode(WidgetGuessData.self, from: data)
        else { return .empty }
        return guess
    }
}

struct DailyGuessWidgetView: View {
    let entry: DailyGuessEntry

    private var played: Bool { entry.guess.hasPlayed(on: entry.date) }
    private var streak: Int { entry.guess.liveStreak(on: entry.date) }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 4) {
                Image(systemName: "flame.fill").snapWidgetIcon()
                Text("Daily guess")
                    .wFont(11, weight: .semibold, design: .serif)
                    .foregroundStyle(Color.wBackground.opacity(0.7))
            }
            Spacer()
            if streak > 0 {
                Text(verbatim: "\(streak)")
                    .wFont(30, weight: .bold, design: .rounded, minScale: 0.5)
                    .foregroundStyle(played ? Color.wSage : Color.wTerracotta)
                    .widgetAccentable()
                    .lineLimit(1)
                Text("day streak")
                    .wFont(11, weight: .semibold)
                    .foregroundStyle(Color.wBackground.opacity(0.85))
            } else {
                Image(systemName: "tag.fill")
                    .font(.system(size: 26, weight: .bold))
                    .foregroundStyle(Color.wTerracotta)
                    .widgetAccentable()
            }
            Text(caption)
                .wFont(11, weight: .medium)
                .foregroundStyle(Color.wWarmGray)
                .lineLimit(2)
                .padding(.top, 2)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .accessibilityElement(children: .combine)
    }

    private var caption: LocalizedStringKey {
        if played { return "Done for today. Back tomorrow." }
        return streak > 0 ? "Guess today to keep it going" : "Guess today's price"
    }
}

struct DailyGuessWidget: Widget {
    let kind = "DailyGuessWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: DailyGuessProvider()) { entry in
            DailyGuessWidgetView(entry: entry)
                .containerBackground(Color.wCharcoal, for: .widget)
                .widgetURL(URL(string: "snapworth://guess?src=daily_guess"))
        }
        .configurationDisplayName("Daily guess")
        .description("Guess one item's resale price a day, and keep your streak.")
        .supportedFamilies([.systemSmall])
    }
}
