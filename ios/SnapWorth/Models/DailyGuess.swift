import Foundation

/// The daily guess-the-price game (#411): one item a day, guess what it
/// resells for, then see the typical range.
///
/// No backend and no scan: the rounds are bundled (`DailyGuessRounds.swift`,
/// generated from the website's /worth data by `tools/build_daily_guess.py`)
/// and the day picks one. The answers are typical secondhand ranges, framed as
/// such everywhere they are shown, never as sold-listing data.
///
/// The round is the same for everyone on the same local day, and the rounds
/// play in order, starting again after the last. The "Day N" a player sees is
/// their own, counted from the first day they played, so the game starts at
/// day 1 for everyone whenever the build that carries it goes live.
enum DailyGuess {

    struct Round: Equatable {
        /// A brand and product name; stays in English, like money.
        let item: String
        let category: ScanCategory
        let condition: String
        /// US dollars, like every money figure in the app.
        let low: Int
        let high: Int
        /// What moves this item's price.
        let why: String
        /// The website guide for this item, `snapworth.app/worth/<slug>`.
        let slug: String
        /// An openly licensed photo of the item, where one fits; the icon
        /// stands in otherwise.
        let photo: Photo?
    }

    struct Photo: Equatable {
        /// The image in the asset catalog.
        let asset: String
        /// "Author, License", as the license asks to be credited. Names and
        /// license names stay as written.
        let credit: String
        /// The photo's page, which carries the full license.
        let source: String
    }

    /// Where the rotation is counted from. It only sets which round falls on
    /// which date, not when the game starts, so it is in the past: a date
    /// before it would play the first round every day.
    static let epoch = DateComponents(year: 2026, month: 10, day: 1)

    /// Which day of the rotation `date` falls on, counting from 1, in the
    /// calendar's time zone. Pure, for the tests.
    static func dayNumber(for date: Date, calendar: Calendar = .current) -> Int {
        guard let start = calendar.date(from: epoch) else { return 1 }
        let days = calendar.dateComponents([.day], from: calendar.startOfDay(for: start),
                                           to: calendar.startOfDay(for: date)).day ?? 0
        return max(1, days + 1)
    }

    /// The round a day plays.
    static func round(forDay day: Int, in rounds: [Round] = rounds) -> Round? {
        guard !rounds.isEmpty else { return nil }
        let index = (max(1, day) - 1) % rounds.count
        return rounds[index]
    }

    // ── Scoring ──────────────────────────────────────────────────────

    enum Verdict: String {
        /// Inside the range.
        case inRange = "in_range"
        /// Outside it, but within `closeMargin` of the nearer end.
        case close
        /// Further out than that.
        case off
    }

    /// How far outside the range still counts as close, as a fraction of the
    /// nearer end.
    static let closeMargin = 0.25

    static func verdict(guess: Double, low: Int, high: Int) -> Verdict {
        let (lo, hi) = (Double(min(low, high)), Double(max(low, high)))
        if guess >= lo && guess <= hi { return .inRange }
        let nearer = guess < lo ? lo : hi
        guard nearer > 0 else { return .off }
        return abs(guess - nearer) / nearer <= closeMargin ? .close : .off
    }

    /// How far the guess was from the middle of the range, in whole percent,
    /// for "within 8%". Never below 0; nil for a range with no middle.
    static func percentOff(guess: Double, low: Int, high: Int) -> Int? {
        let middle = Double(low + high) / 2
        guard middle > 0, guess.isFinite else { return nil }
        return Int((abs(guess - middle) / middle * 100).rounded())
    }

    /// The streak bucket sent with `daily_guess_played`: a closed set, so the
    /// event cannot carry an arbitrary number.
    static func streakBucket(_ streak: Int) -> String {
        switch streak {
        case ..<2:    return "1"
        case 2...3:   return "2-3"
        case 4...6:   return "4-6"
        case 7...13:  return "7-13"
        default:      return "14+"
        }
    }
}

/// What the player did, kept on the phone: today's guess and the streak.
///
/// A play streak, not a winning one: it counts days played in a row, because
/// coming back is what the game is for, and a miss ending it would punish the
/// people the reveal teaches most.
struct DailyGuessRecord: Equatable {
    /// The first day this phone played, as `DailyGuess.dayNumber`; 0 before.
    var firstDay: Int
    /// The last day played, as `DailyGuess.dayNumber`.
    var lastDay: Int
    /// The guess on `lastDay`.
    var lastGuess: Double
    var streak: Int
    var best: Int

    static let empty = DailyGuessRecord(firstDay: 0, lastDay: 0, lastGuess: 0, streak: 0, best: 0)

    /// The record after a guess on `day`. Pure, for the tests.
    ///
    /// A second guess on the same day changes nothing: the day is played.
    func playing(day: Int, guess: Double) -> DailyGuessRecord {
        guard day != lastDay else { return self }
        let streak = (day == lastDay + 1) ? self.streak + 1 : 1
        return DailyGuessRecord(firstDay: firstDay > 0 ? firstDay : day,
                                lastDay: day, lastGuess: guess, streak: streak,
                                best: max(best, streak))
    }

    /// The player's own day number on `day`: 1 on the first day they play
    /// (or before they ever have), counting up from there.
    func playerDay(on day: Int) -> Int {
        firstDay > 0 ? max(1, day - firstDay + 1) : 1
    }

    /// The streak as of `day`: unbroken if the last play was today or
    /// yesterday, else 0.
    func streak(on day: Int) -> Int {
        (day == lastDay || day == lastDay + 1) ? streak : 0
    }

    func hasPlayed(day: Int) -> Bool { day == lastDay }

    // ── Storage ──────────────────────────────────────────────────────

    private static let key = "snapworth_daily_guess"

    static func load(defaults: UserDefaults = .standard) -> DailyGuessRecord {
        guard let raw = defaults.dictionary(forKey: key),
              let lastDay = raw["lastDay"] as? Int,
              let lastGuess = raw["lastGuess"] as? Double,
              let streak = raw["streak"] as? Int,
              let best = raw["best"] as? Int
        else { return .empty }
        let firstDay = raw["firstDay"] as? Int ?? lastDay
        return DailyGuessRecord(firstDay: firstDay, lastDay: lastDay, lastGuess: lastGuess, streak: streak, best: best)
    }

    func save(defaults: UserDefaults = .standard) {
        defaults.set(["firstDay": firstDay, "lastDay": lastDay, "lastGuess": lastGuess, "streak": streak, "best": best],
                     forKey: Self.key)
    }
}
