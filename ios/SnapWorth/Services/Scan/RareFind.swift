import Foundation
import ImageIO
import UIKit

/// A hidden "rare find" appraisal for one shirt, added with the brand owner's
/// permission: a sun-faded red long-sleeve with a dark-red dragon print and the
/// line "You met me at a very Chinese time in my life." Photograph it and,
/// while the real estimate is being fetched, the app plays an over-the-top
/// appraisal and declares it absurdly rare — above the real estimate, never in
/// place of it.
///
/// Three rules hold it in place, and everything below serves one of them:
///
/// * **It can never cost a scan anything.** Detection is on-device, runs
///   beside the request rather than before it, and fails closed; a match that
///   lands after the estimate is dropped. See `RareFindWatch`.
/// * **The joke never touches the data.** Nothing here is persisted. The saved
///   `ScanResult`, My Finds, the widgets and the normal share card are the
///   same whether it fired or not.
/// * **The humour is hype and rarity.** Nothing in `RareFindCopy`, in any
///   language, is about anything but the shirt's supposed value.
///
/// Off switch: `Config.rareFindEasterEggEnabled`, which is compile-time. On
/// for users from 1.5.3 (23).
enum RareFind {
    /// The shirt's line, verbatim, in every language: it is a quote printed on
    /// a garment, not interface copy — see `DELIBERATELY_ENGLISH` in
    /// tools/check_localization.py.
    static let tagline = "You met me at a very Chinese time in my life."

    /// The tagline's language, for VoiceOver. On the card it is an element of
    /// its own in this locale, so a Romanian or Chinese voice hands the one
    /// English line to an English one. It has to be a separate element: SwiftUI
    /// drops a speech language set on a run inside a label, and joins a
    /// `.combine`d group's labels without it.
    static let taglineLocale = Locale(identifier: "en")

    /// The label's name, printed under the tagline. `nil` until the owner
    /// supplies it; the card then has no brand line rather than a guessed one.
    static let brandName: String? = nil

    /// How long the appraisal plays. A matching scan's result waits for it.
    static let appraisalDuration: Duration = .seconds(5)

    // ── Matching (pure + testable) ───────────────────────────────────────────

    /// Whether OCR lines read as the shirt's tagline.
    ///
    /// Lowercases, folds diacritics, turns punctuation into spaces, joins the
    /// lines and splits on whitespace, then looks for the ordered sequence
    /// "me at a very" · one word · "time in my life", where the word reads as
    /// "chinese": within two edits of it, or ending in "inese" — which is what
    /// the swash capital C tends to come back as ("hinese", "cninese"). "You
    /// met" is not required, because its swash Y is the likeliest part of the
    /// print to misread.
    ///
    /// It must not fire on "You met me at a very strange time in my life", the
    /// Fight Club line that shirts are also printed with — "strange" is refused
    /// by name as well as by distance — nor on a generic "best time of my
    /// life", which has neither anchor.
    ///
    /// Two OCR habits are forgiven, and nothing wider:
    /// * spaces inside an anchor, so "meat a very" or "time in my li fe" still
    ///   anchor — an anchor's words are compared with their spaces taken out;
    /// * the middle word split in two — a swash capital read as a letter of its
    ///   own ("c hinese"), or a line break inside the word — rejoined only when
    ///   the whole is no longer than "chinese" and one letter more.
    static func matches(_ lines: [String]) -> Bool {
        let words = tokens(lines)
        for start in words.indices {
            guard let slot = consume(lead, in: words, from: start) else { continue }
            for candidate in middleWords(words, at: slot)
            where readsAsChinese(candidate.word) && consume(tail, in: words, from: candidate.next) != nil {
                return true
            }
        }
        return false
    }

    /// Lowercased, diacritic-folded words, with punctuation and line breaks
    /// read as spaces.
    static func tokens(_ lines: [String]) -> [String] {
        let folded = lines.joined(separator: " ")
            .folding(options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive], locale: nil)
            .lowercased()
        let spaced = String(String.UnicodeScalarView(folded.unicodeScalars.map {
            CharacterSet.alphanumerics.contains($0) ? $0 : " "
        }))
        return spaced.split(whereSeparator: \.isWhitespace).map(String.init)
    }

    /// Whether one word stands for "chinese" in the tagline's middle slot.
    static func readsAsChinese(_ word: String) -> Bool {
        guard !refused.contains(word) else { return false }
        return word.hasSuffix("inese") || editDistance(word, middle) <= 2
    }

    /// Levenshtein distance, by character.
    static func editDistance(_ a: String, _ b: String) -> Int {
        let a = Array(a), b = Array(b)
        guard !a.isEmpty else { return b.count }
        guard !b.isEmpty else { return a.count }
        var previous = Array(0...b.count)
        for (i, x) in a.enumerated() {
            var current = [i + 1] + Array(repeating: 0, count: b.count)
            for (j, y) in b.enumerated() {
                current[j + 1] = Swift.min(previous[j + 1] + 1,
                                           current[j] + 1,
                                           previous[j] + (x == y ? 0 : 1))
            }
            previous = current
        }
        return previous[b.count]
    }

    /// "me at a very" and "time in my life", with their spaces taken out.
    private static let lead = "meatavery"
    private static let tail = "timeinmylife"
    private static let middle = "chinese"

    /// Never the middle word, however near it reads: the Fight Club line.
    private static let refused: Set<String> = ["strange"]

    /// The index just past the words from `start` that spell `phrase` exactly
    /// once their spaces are removed, or nil.
    private static func consume(_ phrase: String, in words: [String], from start: Int) -> Int? {
        var spelled = ""
        var i = start
        while i < words.count, spelled.count < phrase.count {
            spelled += words[i]
            i += 1
            guard phrase.hasPrefix(spelled) else { return nil }
        }
        return spelled == phrase ? i : nil
    }

    /// What may fill the middle slot at `i`: the next word, and that word
    /// joined to the one after it when OCR or a line break split it.
    private static func middleWords(_ words: [String], at i: Int) -> [(word: String, next: Int)] {
        guard i < words.count else { return [] }
        var candidates = [(word: words[i], next: i + 1)]
        if i + 1 < words.count {
            let joined = words[i] + words[i + 1]
            if joined.count <= middle.count + 1 { candidates.append((word: joined, next: i + 2)) }
        }
        return candidates
    }

    // ── Detection ────────────────────────────────────────────────────────────

    /// Whether `image` shows the shirt. See `detect(_:)`.
    static func detect(in image: UIImage) async -> Bool {
        await detect(RareFindPhoto(image))
    }

    /// Whether the photo shows the shirt. Never throws: no bitmap, a Vision
    /// failure or no text all read as "no", and the scan goes on as if this had
    /// never run.
    ///
    /// Detached, at utility priority, for two reasons: it runs while the
    /// analysing overlay animates, so it must stay off the main actor; and it
    /// must not compete with the scan's own upload encode. It reads a copy
    /// downscaled to the upload's 1568px edge — the tagline is large print, and
    /// Vision's accurate pass over a full 12 MP frame costs several times as
    /// much for nothing. The redraw also turns the pixels upright, so a camera
    /// capture reaches Vision the right way up. All of it happens on the
    /// device: the photo goes nowhere it was not already going.
    ///
    /// It honours cancellation, which is how `RareFindWatch.close()` calls it
    /// off once the estimate is in. A detached task does not inherit that, so
    /// it is forwarded; the check sits after the downscale and before Vision,
    /// because Vision's `perform` is synchronous and cannot be stopped once
    /// started. And it holds the full-resolution capture only for as long as
    /// the downscale takes — see `readingCopy(of:)` — so a pass that outlasts
    /// the scan keeps the 1568px copy alive rather than a 12 or 48 MP capture.
    static func detect(_ photo: RareFindPhoto) async -> Bool {
        // A scan that is already over does not start one.
        guard !Task.isCancelled else { return false }
        let work = Task.detached(priority: .utility) { () async -> Bool in
            guard let reading = readingCopy(of: photo), !Task.isCancelled,
                  let cg = reading.cgImage,
                  let lines = try? await OnDeviceText.recognize(
                      cg, orientation: CGImagePropertyOrientation(reading.imageOrientation))
            else { return false }
            return matches(lines.map(\.text))
        }
        return await withTaskCancellationHandler {
            await work.value
        } onCancel: {
            work.cancel()
        }
    }

    /// The photo taken out of `photo` and downscaled. The capture is a local of
    /// this call, released when it returns — a local in `detect` would live on
    /// through the Vision pass that follows it.
    private static func readingCopy(of photo: RareFindPhoto) -> UIImage? {
        guard let capture = photo.take() else { return nil }
        return ScanAPIClient.downscale(capture, maxEdge: ScanAPIClient.maxUploadEdge)
    }

    /// A dollar figure as every other one in the app is printed: `snapCurrency`,
    /// pinned to en_US and USD.
    static func money(_ amount: Int) -> String {
        NumberFormatter.snapCurrency.string(from: NSNumber(value: amount)) ?? "$\(amount)"
    }
}

/// The photo detection reads, handed over once.
///
/// A closure that captured the `UIImage` itself would keep the full-resolution
/// capture alive until detection finished, however long the Vision pass took,
/// and `ScanView` releases that capture as soon as the scan returns. This box is
/// what the closures hold instead, and `take()` empties it: after the downscale
/// nothing in detection holds the capture.
final class RareFindPhoto: @unchecked Sendable {
    // Unchecked because of the lock: `take()` is the only access.
    private let lock = NSLock()
    private var image: UIImage?

    init(_ image: UIImage) { self.image = image }

    /// The photo, the first time; nil after that.
    func take() -> UIImage? {
        lock.lock()
        defer { lock.unlock() }
        let taken = image
        image = nil
        return taken
    }
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - The race against the estimate
// ═══════════════════════════════════════════════════════════════════

/// A detection that landed while its scan was still analysing.
struct RareFindMatch: Equatable {
    let reveal: RareFindReveal
    /// When the appraisal it started has played out. The result waits until
    /// then; with no appraisal to play (Reduce Motion) that is the moment of
    /// the match, and the result does not wait at all.
    let appraisalEnds: ContinuousClock.Instant
    /// Whether an appraisal replaces the analysing overlay.
    let playsAppraisal: Bool
}

/// One scan's side of the race between the estimate and the detector.
///
/// The window is open from the moment the scan request goes out until its
/// response is back, and `close()` is what the scan calls at that moment. A
/// match inside it becomes `match` (and `onMatch` runs, which is what switches
/// the overlay); a match after it, or no match, or a detector that failed,
/// leaves `match` nil and the scan exactly as it would have been. The detector
/// is never waited for.
///
/// A screen cleared mid-scan calls `abandon()` instead, which also forgets a
/// match that has already landed: the scan then finishes exactly as one that
/// never matched.
@MainActor
final class RareFindWatch {
    private(set) var match: RareFindMatch?
    private(set) var isOpen = true
    /// The detector's run. Internal so a test can wait for it.
    private(set) var detection: Task<Void, Never>?

    /// - Parameters:
    ///   - hold: how long a match's appraisal plays; `.zero` for none.
    ///   - detect: `RareFind.detect` on the scan's photo, or a scripted
    ///     stand-in in a test. Cancelled when the window shuts.
    ///   - draw: the reveal a match gets; a test passes a seeded one.
    ///   - onMatch: runs on the main actor, inside the window, at most once.
    init(hold: Duration,
         detect: @escaping @Sendable () async -> Bool,
         draw: @escaping () -> RareFindReveal = { RareFindReveal.draw() },
         onMatch: @escaping (RareFindMatch) -> Void) {
        detection = Task { [weak self] in
            guard await detect() else { return }
            // Back on the main actor, where `close()` runs: the window cannot
            // shut between this check and the assignment below.
            guard let self, self.isOpen else { return }
            let match = RareFindMatch(reveal: draw(),
                                      appraisalEnds: ContinuousClock.now + hold,
                                      playsAppraisal: hold > .zero)
            self.match = match
            onMatch(match)
        }
    }

    /// Shuts the window and returns what landed inside it. Idempotent.
    ///
    /// Also cancels the detector. A detection that has not reached Vision yet
    /// stops there and lets go of its photo; one already inside Vision
    /// finishes, and its answer is never read.
    @discardableResult
    func close() -> RareFindMatch? {
        isOpen = false
        detection?.cancel()
        return match
    }

    /// Shuts the window and forgets whatever landed in it, so that `close()`
    /// returns nil from now on. For a scan whose screen has been cleared — see
    /// `ScanViewModel.reset()`.
    func abandon() {
        close()
        match = nil
    }
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - Copy (all of it, in one place)
// ═══════════════════════════════════════════════════════════════════

/// Every word the easter egg shows or speaks, except the tagline. Translated in
/// ios/Localization/App.json like the rest of the app; the views read from here
/// and carry no copy of their own.
///
/// Four of these are not the easter egg's own: `photoCaptured`,
/// `spokenAppraisal(_:)`, `estimatedResaleValue` and
/// `spokenEstimatedResaleValue` are the analysing overlay's and the result
/// card's keys, reused so the appraisal and the real estimate under the card
/// say what those screens say. Rewording one here rewords it there.
///
/// The tone rule holds in every language: the joke is hype and rarity —
/// auction houses, historians, broken scales. Nothing about who wears the
/// shirt or where anyone is from, and no imagery beyond the shirt's own (its
/// dragon print, its sun fade).
///
/// Computed rather than stored, so each read resolves in the current language.
enum RareFindCopy {
    /// A figure the ticker counts up to, and the punchline it lands on.
    struct Value: Equatable {
        /// What the ticker climbs toward.
        let countTo: Int
        /// What it lands on when that is a word rather than the figure.
        let word: String?
        let punchline: String

        /// The landing: the dollar figure, or the word that replaces it.
        var headline: String { word ?? RareFind.money(countTo) }
    }

    struct Stat: Equatable {
        let label: String
        /// Already formatted — a score, a system-formatted percentage, a count.
        let value: String
        /// The same for VoiceOver, which reads "11/10" as a date.
        let spoken: String
    }

    // ── The card ────────────────────────────────────────────────────────────
    static var easterEgg: String { String(localized: "Easter egg") }
    static var notARealValuation: String { String(localized: "Just for fun — not a real valuation") }
    static var stamp: String { String(localized: "EXTREMELY RARE FIND") }
    static var rarityTier: String { String(localized: "Rarity tier") }
    static var estimatedValue: String { String(localized: "Est. value") }

    // ── Around it ───────────────────────────────────────────────────────────
    static var seriously: String { String(localized: "Okay, seriously:") }
    static var seeFullResult: String { String(localized: "See the full result") }
    static var seeFullResultHint: String { String(localized: "Opens the full result for this item") }
    static var share: String { String(localized: "Share the card") }
    static var shareHint: String { String(localized: "Shares the easter egg card as an image") }
    static var revealAnnouncement: String {
        String(localized: "Easter egg: an extremely rare find. Your real estimate is below.")
    }

    // ── Spoken ──────────────────────────────────────────────────────────────
    // The card read as sentences. Separate from the printed labels because a
    // stamp in capitals and "Est." are for the eye.
    static var spokenLabel: String { String(localized: "Easter egg, just for fun. Not a real valuation.") }
    static var spokenStamp: String { String(localized: "Extremely rare find.") }

    static func spokenTier(_ tier: String) -> String {
        String(localized: "Rarity tier: \(tier).")
    }

    /// The landing and its punchline as one sentence, each without the full
    /// stop it may end on in print ("Priceless.") — the sentence brings its own.
    static func spokenValue(_ value: Value) -> String {
        String(localized: "Estimated value: \(bare(value.headline)), \(bare(value.punchline)).")
    }

    static func spokenStat(_ stat: Stat) -> String {
        String(localized: "\(stat.label): \(stat.spoken).")
    }

    /// Ends the card's summary. The tagline it introduces is the next element,
    /// in English — see `RareFind.taglineLocale`.
    static var spokenTaglineLead: String { String(localized: "The shirt reads:") }

    // ── Borrowed from the analysing overlay and the result card ─────────────
    static var photoCaptured: String { String(localized: "Photo captured — you can lower your phone") }
    static func spokenAppraisal(_ line: String) -> String {
        String(localized: "\(line) Photo captured — you can lower your phone.")
    }
    static var estimatedResaleValue: String { String(localized: "Estimated Resale Value") }
    static var spokenEstimatedResaleValue: String { String(localized: "Estimated resale value") }

    // ── The appraisal ───────────────────────────────────────────────────────
    // Fifteen lines in three stages, so that whatever is drawn escalates. The
    // owner's five are the first two of the first two stages and the first
    // peak.

    static var openingLines: [String] {
        [
            String(localized: "Authenticating stitching…"),
            String(localized: "Cross-referencing auction records…"),
            String(localized: "Measuring the sun fade…"),
            String(localized: "Counting threads. Lost count…"),
            String(localized: "Checking the collar for provenance…"),
        ]
    }

    static var risingLines: [String] {
        [
            String(localized: "Consulting three fashion historians…"),
            String(localized: "Dragon detected. Recalibrating…"),
            String(localized: "The historians are arguing…"),
            String(localized: "Calling a museum curator…"),
            String(localized: "Rarity index climbing…"),
        ]
    }

    static var peakLines: [String] {
        [
            String(localized: "WARNING: value exceeds scale"),
            String(localized: "New scale ordered. Also exceeded."),
            String(localized: "The appraiser needs a moment…"),
            String(localized: "Alerting every auction house…"),
            String(localized: "This has never happened before…"),
        ]
    }

    // ── The reveal ──────────────────────────────────────────────────────────

    static var tiers: [String] {
        [
            String(localized: "Mythic"),
            String(localized: "Legendary+"),
            String(localized: "1 of 1 (allegedly)"),
            String(localized: "Grail"),
            String(localized: "Beyond legendary"),
        ]
    }

    static var values: [Value] {
        [
            Value(countTo: 48_000, word: nil, punchline: String(localized: "market rate: unhinged")),
            Value(countTo: 1_000_000, word: nil, punchline: String(localized: "before the bidding war")),
            Value(countTo: 250_000, word: nil, punchline: String(localized: "per sleeve")),
            Value(countTo: 3_500_000, word: nil, punchline: String(localized: "conservatively")),
            Value(countTo: 777_777, word: nil, punchline: String(localized: "suspiciously specific")),
            Value(countTo: 12_000_000, word: nil, punchline: String(localized: "auction houses are calling")),
            Value(countTo: 1_000_000_000, word: nil, punchline: String(localized: "give or take")),
            Value(countTo: 64_000, word: nil, punchline: String(localized: "and climbing")),
            Value(countTo: 500_000, word: nil, punchline: String(localized: "vibes not included")),
            Value(countTo: 150_000, word: nil, punchline: String(localized: "shipping not included")),
            Value(countTo: 9_999_999, word: nil, punchline: String(localized: "the scale maxed out")),
            Value(countTo: 2_000_000, word: nil, punchline: String(localized: "museums are circling")),
            Value(countTo: 99_999_999, word: String(localized: "Priceless."),
                  punchline: String(localized: "The calculator quit.")),
            Value(countTo: 5_000_000, word: String(localized: "Classified."),
                  punchline: String(localized: "Need-to-know basis.")),
            Value(countTo: 999_999_999, word: String(localized: "Off the charts."),
                  punchline: String(localized: "We checked. Twice.")),
        ]
    }

    /// Percentages and counts come from the system, never "\(n)%": ro, es and
    /// de put a non-breaking space before the sign, and group digits their own
    /// way.
    static var stats: [Stat] {
        [
            score(String(localized: "Drip"), 11, outOf: 10),
            plain(String(localized: "Authenticity"), 104.formatted(.percent)),
            plain(String(localized: "Sightings in the wild"), 3.formatted()),
            plain(String(localized: "Hype level"), 9_001.formatted()),
            plain(String(localized: "Collectors alerted"), 12.formatted()),
            score(String(localized: "Sun-fade rating"), 10, outOf: 10),
            plain(String(localized: "Historians consulted"), 3.formatted()),
        ]
    }

    static var verdicts: [String] {
        [
            String(localized: "Insure it before you wear it."),
            String(localized: "Do not machine wash. Do not even look at it too hard."),
            String(localized: "Grail status: confirmed."),
            String(localized: "Keep it out of the sun. It has been through enough."),
            String(localized: "Collectors will write songs about this."),
            String(localized: "Not a shirt. An heirloom."),
            String(localized: "Museums will be jealous."),
            String(localized: "The rarest thing we have ever scanned. Allegedly."),
            String(localized: "Wear it once. Then straight to the vault."),
            String(localized: "Legends have been made of less."),
        ]
    }

    private static func plain(_ label: String, _ value: String) -> Stat {
        Stat(label: label, value: value, spoken: value)
    }

    private static func bare(_ part: String) -> String {
        guard let last = part.last, ".。".contains(last) else { return part }
        return String(part.dropLast())
    }

    /// "11/10" on the card — digits and a slash read the same in every language
    /// the app ships, so not a catalog key — and "11 out of 10" aloud.
    private static func score(_ label: String, _ points: Int, outOf total: Int) -> Stat {
        Stat(label: label,
             value: "\(points.formatted())/\(total.formatted())",
             spoken: String(localized: "\(points) out of \(total)"))
    }
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - One draw
// ═══════════════════════════════════════════════════════════════════

/// Everything one appraisal and its reveal show, drawn from `RareFindCopy`
/// once per match. Held in memory for as long as it is on screen and never
/// written anywhere.
struct RareFindReveal: Equatable {
    /// Five lines, escalating: two openers, two risers, one peak.
    let statusLines: [String]
    let tier: String
    let value: RareFindCopy.Value
    /// Three, all different.
    let stats: [RareFindCopy.Stat]
    let verdict: String

    static func draw() -> RareFindReveal {
        var generator = SystemRandomNumberGenerator()
        return draw(using: &generator)
    }

    /// The generator is a parameter so a test can seed it. The order of the
    /// draws below is part of what a seed reproduces.
    static func draw<G: RandomNumberGenerator>(using generator: inout G) -> RareFindReveal {
        let lines = Array(RareFindCopy.openingLines.shuffled(using: &generator).prefix(2))
            + Array(RareFindCopy.risingLines.shuffled(using: &generator).prefix(2))
            + Array(RareFindCopy.peakLines.shuffled(using: &generator).prefix(1))
        return RareFindReveal(
            statusLines: lines,
            tier: pick(RareFindCopy.tiers, using: &generator),
            value: pick(RareFindCopy.values, using: &generator),
            stats: Array(RareFindCopy.stats.shuffled(using: &generator).prefix(3)),
            verdict: pick(RareFindCopy.verdicts, using: &generator)
        )
    }

    /// The card as VoiceOver reads it, label first: whoever hears it hears that
    /// it is an easter egg before they hear a number. Everything but the
    /// tagline and the brand, which follow as elements of their own — the
    /// tagline so it can be spoken in English.
    var spokenSummary: String {
        var parts = [
            RareFindCopy.spokenLabel,
            RareFindCopy.spokenStamp,
            RareFindCopy.spokenTier(tier),
            RareFindCopy.spokenValue(value),
        ]
        parts += stats.map(RareFindCopy.spokenStat)
        parts.append(verdict)
        parts.append(RareFindCopy.spokenTaglineLead)
        return parts.joined(separator: " ")
    }

    /// The lists are constant and never empty, so an index into one is always
    /// in range.
    private static func pick<T, G: RandomNumberGenerator>(_ items: [T], using generator: inout G) -> T {
        items[Int.random(in: items.indices, using: &generator)]
    }
}
