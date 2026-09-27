import Foundation
import SwiftData

@Model
final class ScanResult {
    var id: UUID
    var timestamp: Date
    var itemName: String
    var brand: String
    var category: String
    var conditionNotes: String
    var valueLow: Double
    var valueHigh: Double
    var confidence: String
    var soldListingsCount: Int
    var listingTitle: String
    var listingDescription: String
    /// JPEG-compressed image for display in history
    @Attribute(.externalStorage) var imageData: Data?
    /// What the user paid — nil when not entered; 0 = free find.
    var paidPrice: Double?

    // ── My Flips ledger ───────────────────────────────────────────────────────
    // All optional → additive lightweight migration. Records saved before the
    // ledger existed decode with these nil and behave exactly as `.scanned`.
    /// Backing store for `status`; nil ⇒ `.scanned` (legacy-safe).
    var statusRaw: String?
    /// When the item was marked `.listed` — anchors the 14-day ledger follow-up
    /// reminder and the "needs an update" fallback badge. Legacy-safe (optional).
    var listedDate: Date?
    /// What the item eventually sold for.
    var soldPrice: Double?
    var soldDate: Date?
    /// Selling/shipping fees the user expects — treated as 0 in profit math.
    var feesEstimate: Double?
    var notes: String?

    /// User-selected resale condition. Optional → additive lightweight migration:
    /// legacy records decode with nil and fall back to the condition inferred
    /// from `conditionNotes`, so the estimate they show is unchanged.
    var conditionRaw: String?

    // ── Portfolio (1.2.2) ──────────────────────────────────────────────────────
    // Both optional, both additive: no renames and no non-optional field without
    // a default, so this stays inside SwiftData's automatic lightweight
    // migration class. `MigrationTests` covers a 1.1.x store; the same shape
    // rules apply here.

    /// The condition-adjusted "likely" value at the time this row was last
    /// priced. Written with every value-history snapshot; no longer read for
    /// pricing — see `portfolioValue` for why a stored price went stale.
    ///
    /// Nil on every row written before 1.2.2, which is harmless for the same
    /// reason: `portfolioValue` computes from `valueLow`/`valueHigh` for every
    /// row, so an old library prices correctly with no backfill pass.
    var portfolioValueRaw: Double?

    /// Append-only `[(date, value)]` history, JSON-encoded.
    ///
    /// Stored as Data rather than a related @Model: it is only ever read as a
    /// whole for one item's sparkline, never queried across rows, and a second
    /// entity would add a relationship to migrate for no query benefit.
    var valueHistoryData: Data?

    // ── Why this price (#87) ───────────────────────────────────────────────────
    /// `ValuationDetail`, JSON-encoded. Optional and additive, like the two
    /// above. Nil for every row written before this shipped; the panel that
    /// reads it is simply absent for those.
    var valuationDetailData: Data?

    /// Decoded on every read — a `JSONDecoder` pass over the blob, each time.
    ///
    /// This said "cheap, and only the result sheet asks for it". The second
    /// half stopped being true when `baselineCondition` began reading the
    /// grade out of it, which put a decode under every price read in the app —
    /// see `storedFacts`, which is what the price path uses instead.
    var valuationDetail: ValuationDetail? { ValuationDetail.decode(valuationDetailData) }

    /// The two things the price path needs from the detail blob — the grade
    /// the model returned and its likely price — memoised by the bytes they
    /// came from.
    ///
    /// `baselineCondition` is under every price read — the range on a card,
    /// the portfolio total, a Most Valuable comparison, the widget sums — and
    /// it decoded the whole `valuationDetailData` blob to find one field. A
    /// Most Valuable comparison read it eight times, and sorting 500 finds
    /// took 1.6 seconds on the main thread, again on every search keystroke.
    /// The likely price is under the same reads, so it rides the same memo
    /// rather than bringing the decode back.
    ///
    /// Keyed by content rather than by row, so there is nothing to invalidate:
    /// the same bytes always hold the same values, and a re-read
    /// (`applySharpened`) writes new bytes, which simply miss. Held outside
    /// the model on purpose — a memo stored on the `@Model` would be observed
    /// state written from inside a getter that SwiftUI calls while rendering.
    private var storedFacts: StoredFacts {
        guard let data = valuationDetailData else { return .none }
        let key = data as NSData
        if let hit = Self.factsCache.object(forKey: key) { return hit }
        let detail = valuationDetail
        let facts = StoredFacts(
            grade: detail?.conditionGrade.flatMap(Condition.init(serverGrade:)),
            // `expected` for a find saved before `likely` existed: the same
            // number, under its ladder name. Every Pro find has it, and so
            // does a free one scanned before the ladder was withheld from
            // free responses (44a107e). One with neither prices from the
            // midpoint, as it always did.
            likely: detail?.likely ?? detail?.expected)
        Self.factsCache.setObject(facts, forKey: key)
        return facts
    }

    private final class StoredFacts {
        let grade: Condition?
        let likely: Double?
        init(grade: Condition?, likely: Double?) {
            self.grade = grade
            self.likely = likely
        }
        static let none = StoredFacts(grade: nil, likely: nil)
    }

    /// `NSCache` is thread-safe and evicts under memory pressure; the bound
    /// is a few thousand small entries, more rows than a library holds.
    private static let factsCache: NSCache<NSData, StoredFacts> = {
        let cache = NSCache<NSData, StoredFacts>()
        cache.countLimit = 5_000
        return cache
    }()

    /// Whether real sales backed this estimate (#40). `.model` for every row
    /// without a detail blob — older scans, and any server that sent none.
    var valuationSource: ValuationSource { valuationDetail?.source ?? .model }

    /// Replace this find's valuation with a re-read of it: the tag re-read,
    /// which had the label photo too (#88), or the full-breakdown re-read of
    /// the stored photo, for a subscriber whose find was saved with the free
    /// panel only.
    ///
    /// Deliberately partial: the photo, what the user paid, the ledger status
    /// and any condition they chose are theirs and survive. Only what the model
    /// produced is replaced — and `conditionRaw` is left alone so a re-read
    /// never silently undoes a correction the user made by hand.
    func applySharpened(_ response: ScanAPIResponse) {
        itemName = response.itemName
        brand = response.brand
        category = response.category
        conditionNotes = response.conditionNotes
        valueLow = response.estValueLowUsd
        valueHigh = response.estValueHighUsd
        confidence = response.confidence
        listingTitle = response.listingTitle
        listingDescription = response.listingDescription
        if let detail = ValuationDetail(response: response) {
            valuationDetailData = detail.encoded()
        }
        // A re-read replaces the estimate, and the estimate is what the
        // portfolio total and the value history are made of. Without this the
        // item showed one number and the portfolio kept the old one — the tag
        // re-read (#88) was the only path that moved a value and did not
        // record it.
        //
        // Must come after `valuationDetailData`: `baselineCondition` reads
        // `conditionGrade` out of that blob, and `priceRange` divides by its
        // multiplier. Refreshing first would price against the previous read's
        // grade. Idempotent and deduplicated at the cent, so a re-read landing
        // on the same number appends nothing.
        refreshPortfolioValue()
    }

    init(
        id: UUID = UUID(),
        timestamp: Date = Date(),
        itemName: String,
        brand: String,
        category: String,
        conditionNotes: String,
        valueLow: Double,
        valueHigh: Double,
        confidence: String,
        soldListingsCount: Int,
        listingTitle: String,
        listingDescription: String,
        imageData: Data? = nil,
        paidPrice: Double? = nil,
        statusRaw: String? = nil,
        listedDate: Date? = nil,
        soldPrice: Double? = nil,
        soldDate: Date? = nil,
        feesEstimate: Double? = nil,
        notes: String? = nil,
        conditionRaw: String? = nil,
        portfolioValueRaw: Double? = nil,
        valueHistoryData: Data? = nil,
        valuationDetailData: Data? = nil
    ) {
        self.id = id
        self.timestamp = timestamp
        self.itemName = itemName
        self.brand = brand
        self.category = category
        self.conditionNotes = conditionNotes
        self.valueLow = valueLow
        self.valueHigh = valueHigh
        self.confidence = confidence
        self.soldListingsCount = soldListingsCount
        self.listingTitle = listingTitle
        self.listingDescription = listingDescription
        self.imageData = imageData
        self.paidPrice = paidPrice
        self.statusRaw = statusRaw
        self.listedDate = listedDate
        self.soldPrice = soldPrice
        self.soldDate = soldDate
        self.feesEstimate = feesEstimate
        self.notes = notes
        self.conditionRaw = conditionRaw
        self.portfolioValueRaw = portfolioValueRaw
        self.valueHistoryData = valueHistoryData
        self.valuationDetailData = valuationDetailData
    }

    /// A field-for-field copy that belongs to no `ModelContext`.
    ///
    /// Exists for one caller: `ScanRepository.save`, which now rolls the shared
    /// context back when a save fails. Rollback un-registers a
    /// never-persisted insert, and the result sheet is already on screen
    /// holding that object — `ScanViewModel` assigns `scanResult` *before*
    /// attempting the save, deliberately, so a storage failure can never take
    /// the user's result away from them. Reading a model SwiftData has
    /// discarded is not something to gamble a crash on, so the copy is taken
    /// before the insert and handed back with the error.
    ///
    /// Every stored property is listed. `test_detachedCopyCarriesEveryStoredProperty`
    /// fails if one is added to the model and not to here, because a copy that
    /// silently drops a field would show the user a result missing their photo
    /// or their paid price.
    func detachedCopy() -> ScanResult {
        ScanResult(
            id: id,
            timestamp: timestamp,
            itemName: itemName,
            brand: brand,
            category: category,
            conditionNotes: conditionNotes,
            valueLow: valueLow,
            valueHigh: valueHigh,
            confidence: confidence,
            soldListingsCount: soldListingsCount,
            listingTitle: listingTitle,
            listingDescription: listingDescription,
            imageData: imageData,
            paidPrice: paidPrice,
            statusRaw: statusRaw,
            listedDate: listedDate,
            soldPrice: soldPrice,
            soldDate: soldDate,
            feesEstimate: feesEstimate,
            notes: notes,
            conditionRaw: conditionRaw,
            portfolioValueRaw: portfolioValueRaw,
            valueHistoryData: valueHistoryData,
            valuationDetailData: valuationDetailData
        )
    }

    // ── Condition & re-pricing ─────────────────────────────────────────────────

    /// The condition the AI's `valueLow`/`valueHigh` were priced for.
    ///
    /// Prefers `condition_grade` — the grade the model actually returned,
    /// validated server-side against exactly these four values — and reads the
    /// prose notes only when there is none: an older server, or a record stored
    /// while the field was still gated to Pro. Parsing English to recover a
    /// value the model already handed us in a closed vocabulary was always the
    /// weaker path; it stays only because old records still need it.
    ///
    /// One rule, used by both `condition` and `priceRange`, deliberately.
    /// They must agree: if the getter defaults to one baseline and the re-scale
    /// divides by another, an untouched record is silently mispriced.
    var baselineCondition: Condition { baseline(from: storedFacts) }

    /// `baselineCondition` from facts already read, for the price path, which
    /// needs the likely price out of the same memo lookup.
    private func baseline(from facts: StoredFacts) -> Condition {
        facts.grade ?? Condition.inferred(from: conditionNotes)
    }

    /// The resale condition driving the estimate. Reads the user's explicit
    /// choice when set; otherwise the baseline above, so an untouched record
    /// prices exactly as the AI returned it.
    var condition: Condition {
        get { conditionRaw.flatMap(Condition.init(rawValue:)) ?? baselineCondition }
        set { conditionRaw = newValue.rawValue }
    }

    /// The AI's resale range re-scaled from the condition it inferred to
    /// `condition`. `valueLow`/`valueHigh` stay the immutable AI baseline so
    /// repeated selector changes never compound. Exact `Decimal` money; both
    /// features (listing price, flip resale) read from here.
    func priceRange(for condition: Condition) -> (low: Decimal, likely: Decimal, high: Decimal) {
        let facts = storedFacts
        return priceRange(for: condition, baseline: baseline(from: facts),
                          storedLikely: facts.likely)
    }

    /// `priceRange(for: condition)` with the blob read once, not twice —
    /// `condition` falls back to `baselineCondition` itself. What every
    /// surface showing *this* find's current estimate should call.
    var currentPriceRange: (low: Decimal, likely: Decimal, high: Decimal) {
        let facts = storedFacts
        let baseline = self.baseline(from: facts)
        return priceRange(for: conditionRaw.flatMap(Condition.init(rawValue:)) ?? baseline,
                          baseline: baseline, storedLikely: facts.likely)
    }

    private func priceRange(for condition: Condition, baseline: Condition,
                            storedLikely: Double?) -> (low: Decimal, likely: Decimal, high: Decimal) {
        guard valueLow.isFinite, valueHigh.isFinite else { return (0, 0, 0) }
        let factor = condition.priceMultiplier / baseline.priceMultiplier
        let low = Decimal(valueLow) * factor
        let high = Decimal(valueHigh) * factor
        let likely = Self.baselineLikely(low: valueLow, high: valueHigh,
                                         stored: storedLikely).map { $0 * factor }
        return (low, likely ?? (low + high) / 2, high)
    }

    /// The model's likely price at the baseline condition, when there is one
    /// this row can use — nil means "take the midpoint".
    ///
    /// `likely` was the midpoint of the range, written when low and high were
    /// a typical spread. A day later v2 made them the worst and best case and
    /// began asking for an expected price that is explicitly not their
    /// midpoint (`prompts.py`). A resale range has a long upper tail, so the
    /// midpoint sits above what the item most likely fetches: the flip
    /// verdict, the listing ask, the portfolio total, the widgets and the
    /// Most Valuable sort all inherited that bias, while a subscriber saw the
    /// real expected price on the ladder of the same find.
    ///
    /// Scaled by the same condition factor as the range, so a chip change
    /// moves all three together and never compounds. Refused unless it lies
    /// inside `[low, high]`: the server pins it there, so a figure outside is
    /// one this row's range was not built with — a blob kept across a re-read
    /// that sent no detail — and a number from another estimate is worse
    /// than the midpoint of this one.
    static func baselineLikely(low: Double, high: Double, stored: Double?) -> Decimal? {
        guard let stored, stored.isFinite, stored > 0,
              stored >= min(low, high) - 0.005, stored <= max(low, high) + 0.005
        else { return nil }
        return Decimal(stored)
    }

    /// The factor `priceRange` applies, exposed so a view that renders the
    /// model's *own* price points can scale them the same way.
    ///
    /// `ValuationDetailView` prints the server's four-point ladder raw, and it
    /// sits directly above the condition chips explaining the headline range —
    /// which is scaled. Correcting a `good` item to `used` therefore left a
    /// "Best case" 43% above the headline's own high end, on the one panel
    /// whose job is to explain the price.
    var conditionPriceFactor: Decimal {
        condition.priceMultiplier / baselineCondition.priceMultiplier
    }

    /// True when the user has picked a condition other than the one the AI
    /// read, so a surface printing the AI's grade must not present it as
    /// current.
    var conditionWasOverridden: Bool { condition != baselineCondition }

    /// Condition-adjusted low/high as `Double`, for the existing range views.
    var displayValueLow: Double { NSDecimalNumber(decimal: currentPriceRange.low).doubleValue }
    var displayValueHigh: Double { NSDecimalNumber(decimal: currentPriceRange.high).doubleValue }

    var formattedRange: String {
        guard valueLow.isFinite && valueHigh.isFinite else {
            return String(localized: "Price unavailable")
        }
        let range = currentPriceRange
        let fmt = NumberFormatter.snapCurrency
        let lo = fmt.string(from: NSDecimalNumber(decimal: range.low)) ?? "$\(Int(displayValueLow))"
        let hi = fmt.string(from: NSDecimalNumber(decimal: range.high)) ?? "$\(Int(displayValueHigh))"
        return "\(lo)–\(hi)"
    }

    // ── Portfolio value ────────────────────────────────────────────────────────

    /// One dated point in an item's value history.
    struct ValueSnapshot: Codable, Equatable {
        let date: Date
        let value: Double
    }

    /// The condition-adjusted "likely" value, in Decimal — always the live
    /// estimate, the same number the card, the widget and the sort read. The
    /// model's expected price where the find has one (`baselineLikely`), the
    /// midpoint of its range where it does not.
    ///
    /// It used to prefer the denormalised `portfolioValueRaw`, which is written
    /// only when a value is re-priced by hand (`refreshPortfolioValue`), and so
    /// kept whatever the pricing rules said on the day of that edit. The rules
    /// for `baselineCondition` have changed since, so a find whose condition
    /// chip was touched on 1.2.2–1.3.6 counted one figure in "Your finds are
    /// worth" and in the Sunday digest while its own card and the widget —
    /// which read `priceRange` — said another. The column cannot know the
    /// rules moved; reading `priceRange` can, and with the grade memoised it
    /// costs about what the column did.
    ///
    /// `portfolioValueRaw` is still written, as the point the value history
    /// is appended from; nothing prices from it.
    var portfolioValue: Decimal {
        currentPriceRange.likely
    }

    /// Recomputes and stores the denormalised value, and appends a snapshot when
    /// the value actually moved.
    ///
    /// Append-only and deduplicated: re-pricing to the same number does not
    /// grow the history, so opening an item repeatedly cannot inflate it. The
    /// series is capped — an item edited hundreds of times still costs a
    /// bounded amount of storage, and a sparkline cannot usefully render more.
    func refreshPortfolioValue(on date: Date = Date(), limit: Int = 60) {
        let likely = currentPriceRange.likely
        let asDouble = NSDecimalNumber(decimal: likely).doubleValue
        guard asDouble.isFinite else { return }

        var history = valueHistory
        // Compare rounded to the cent: Decimal→Double round-trips can differ in
        // the last bit, and a snapshot per open would be noise, not history.
        let changed = history.last.map { abs($0.value - asDouble) >= 0.005 } ?? true
        if changed {
            history.append(ValueSnapshot(date: date, value: asDouble))
            // Trimmed out of the middle, never off the front.
            // `valueChangeSinceAdded` reads `history.first` as the value this
            // item entered the portfolio at, so dropping the oldest point
            // re-anchors that figure to whichever re-price happened to survive
            // the cap — and it keeps its old label while quietly meaning
            // something else. The entry point is the one snapshot that is not
            // interchangeable; the interior ones are.
            if history.count > limit {
                history = [history[0]] + history.suffix(limit - 1)
            }
            valueHistoryData = try? JSONEncoder().encode(history)
        }
        portfolioValueRaw = asDouble
    }

    /// Re-expresses the stored value under the pricing rules now in force,
    /// for `ScanRepository.applyPricingRulesIfNeeded`.
    ///
    /// `portfolioValueRaw` is set to today's figure, and the history is
    /// scaled by one ratio so that it ends there: each point keeps its size
    /// relative to its neighbours, which is the only thing a history of the
    /// user's own re-pricings can honestly say. Appending a point instead, as
    /// `refreshPortfolioValue` would, records a change in how the app prices
    /// as a change in what the item is worth — the signal `WeeklyDigest` is
    /// careful never to invent.
    ///
    /// A row never priced into the portfolio (nil value, empty history) is
    /// left alone: it prices live like every other, and a first snapshot
    /// written now would date its entry to today.
    func rebaseStoredValue() {
        guard portfolioValueRaw != nil || valueHistoryData != nil else { return }
        let current = NSDecimalNumber(decimal: currentPriceRange.likely).doubleValue
        guard current.isFinite else { return }
        let history = valueHistory
        if let last = history.last, last.value > 0, abs(last.value - current) >= 0.005 {
            let ratio = current / last.value
            let rebased = history.map { ValueSnapshot(date: $0.date, value: $0.value * ratio) }
            valueHistoryData = try? JSONEncoder().encode(rebased)
        }
        portfolioValueRaw = current
    }

    /// Decoded value history, oldest first. Never throws: a corrupt or
    /// truncated blob reads as "no history" rather than taking down the view.
    var valueHistory: [ValueSnapshot] {
        guard let data = valueHistoryData,
              let decoded = try? JSONDecoder().decode([ValueSnapshot].self, from: data)
        else { return [] }
        return decoded
    }

    /// Change since the item first entered the portfolio, or nil when there is
    /// not yet a second point to compare against.
    var valueChangeSinceAdded: Decimal? {
        let history = valueHistory
        guard let first = history.first, history.count >= 2 else { return nil }
        return portfolioValue - Decimal(first.value)
    }

    /// `likely` as a `Double`, for the Most Valuable sort — the figure the
    /// portfolio and the widgets sum, so the order and the totals agree.
    ///
    /// Renamed from `midpointValue` when `likely` stopped being the middle of
    /// the range (`baselineLikely`): the old name told the next reader it
    /// still was. It once went through `displayValueLow` and
    /// `displayValueHigh`, two full price reads for one number, twice per
    /// Most Valuable comparison.
    var likelyValue: Double {
        NSDecimalNumber(decimal: currentPriceRange.likely).doubleValue
    }

    // ── Ledger computed values ────────────────────────────────────────────────

    /// Lifecycle status. Legacy records (nil raw) read as `.scanned`.
    var status: FlipStatus {
        get { statusRaw.flatMap(FlipStatus.init(rawValue:)) ?? .scanned }
        set { statusRaw = newValue.rawValue }
    }

    /// Realized profit: soldPrice − pricePaid − fees. Uses `Decimal` so money
    /// math is exact. Nil unless the item is sold **and** a paid price is known
    /// — we never guess profit from a missing cost basis.
    var realizedProfit: Decimal? {
        guard status == .sold, let sold = soldPrice, let paid = paidPrice else { return nil }
        return Decimal(sold) - Decimal(paid) - Decimal(feesEstimate ?? 0)
    }

    /// ROI as a fraction (0.5 == +50%). Nil when the paid price is unknown or
    /// zero (division undefined / ROI meaningless on a free find).
    var roi: Decimal? {
        guard let profit = realizedProfit, let paid = paidPrice, paid > 0 else { return nil }
        return profit / Decimal(paid)
    }
}

// MARK: - Pricing rules

/// Which rules `ScanResult.priceRange` prices by, so that what was stored
/// under older ones is re-expressed once rather than compared against
/// (`ScanRepository.applyPricingRulesIfNeeded`).
///
/// Bump `current` for a change that moves the figure an *existing* find
/// prices at, and only for that — a change to new scans alone stores nothing
/// stale.
enum PricingRules {
    /// 1 — `likely` is the midpoint of the range, for every find.
    /// 2 — the model's expected price, where the find has one
    ///     (`ScanResult.baselineLikely`); the midpoint where it does not.
    static let current = 2

    /// Absent reads as 0, which is rules 1: nothing was recorded before 2.
    static let defaultsKey = "snapworth_pricing_rules_version"
}

// MARK: - Resale condition

/// User-selectable resale condition. Condition is the single biggest lever on
/// secondhand price, so the selector re-scales the AI's estimate against these
/// multipliers (anchored to the `.good` thrift baseline). Raw values are
/// persisted in `ScanResult.conditionRaw`; never renamed (would orphan records).
enum Condition: String, CaseIterable, Identifiable {
    case new
    case likeNew
    case good
    case used

    var id: String { rawValue }

    /// Shown on the condition picker. Translated; `rawValue` is what is
    /// stored and sent to the backend and stays English.
    var label: String {
        switch self {
        case .new:     return String(localized: "New", comment: "Condition grade")
        case .likeNew: return String(localized: "Like New", comment: "Condition grade")
        case .good:    return String(localized: "Good", comment: "Condition grade")
        case .used:    return String(localized: "Used", comment: "Condition grade")
        }
    }

    /// Price multiplier relative to the `.good` secondhand baseline (== 1.0).
    var priceMultiplier: Decimal {
        switch self {
        case .new:     return 1.35
        case .likeNew: return 1.15
        case .good:    return 1.0
        case .used:    return 0.78
        }
    }

    /// The same grade as `listingPhrase`, for the screen rather than for the
    /// generator. Separate because `listingPhrase` is an input to listing text
    /// the backend writes in English — translating it would put a Romanian
    /// clause inside an English listing.
    var displayPhrase: String {
        switch self {
        case .new:     return String(localized: "brand new, unused")
        case .likeNew: return String(localized: "like new, barely used")
        case .good:    return String(localized: "good used condition")
        case .used:    return String(localized: "used with visible wear")
        }
    }

    /// The grade in German, for a Kleinanzeigen listing. Same reasoning as
    /// `xianyuPhrase` below: a single-country marketplace's ad is read by that
    /// country's buyers, whatever language the seller's phone is in.
    var kleinanzeigenPhrase: String {
        switch self {
        case .new:     return "neu und unbenutzt"
        case .likeNew: return "neuwertig, kaum benutzt"
        case .good:    return "gebraucht, guter Zustand"
        case .used:    return "gebraucht, mit sichtbaren Gebrauchsspuren"
        }
    }

    /// The grade in Chinese, for a Xianyu listing.
    ///
    /// Its own member rather than a translation of `listingPhrase`: that one is
    /// an input to listing text the backend writes in English, and this one is
    /// listing text itself. Not `String(localized:)` either — a Xianyu listing
    /// is written in Chinese whatever language the seller's phone is in, and
    /// looking it up would make it follow the interface instead.
    var xianyuPhrase: String {
        switch self {
        case .new:     return "全新未使用"
        case .likeNew: return "几乎全新，仅试用过"
        case .good:    return "二手，成色良好"
        case .used:    return "二手，有使用痕迹"
        }
    }

    /// Phrase fed to the listing generator so wording matches the chosen grade.
    var listingPhrase: String {
        switch self {
        case .new:     return "brand new, unused"
        case .likeNew: return "like new, barely used"
        case .good:    return "good used condition"
        case .used:    return "used with visible wear"
        }
    }

    /// The closed vocabulary `condition_grade` is validated against server-side
    /// (`valuation.py` `_CONDITION_GRADES`). Case- and spacing-tolerant because
    /// the value is model-generated and older records carry "Good" and
    /// "like new" as well as the canonical forms.
    init?(serverGrade raw: String) {
        switch raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "new":                            self = .new
        case "likenew", "like new", "like-new": self = .likeNew
        case "good":                           self = .good
        case "used":                           self = .used
        default:                               return nil
        }
    }

    /// Where a negation stops applying: punctuation, and the contrastive
    /// conjunctions that end a negated span — "no stains **but** heavy wear at
    /// the cuffs" is a worn item, and scoping the "no" to the whole sentence
    /// would read it as a clean one.
    private static let clauseBreaks = [";", ",", ".", " but ", " though ",
                                       " however ", " although ", " apart from ",
                                       " other than ", " except "]

    private static let negators = ["no ", "not ", "without ", "free of ",
                                   "free from ", "none ", "n't "]

    /// Splits on " and ", but only where it starts a new predicate.
    ///
    /// " and " is a weaker break than the contrastive conjunctions above, and
    /// it goes both ways. In "no stains **and** heavy wear at the cuffs" it
    /// ends the negated span — that is a worn item, and reading the "no"
    /// across the whole sentence graded it `.good`, understating the estimate
    /// by the 0.78 `.used` multiplier. In "no rips **and** tears" it does not:
    /// that is one negated list, and splitting it blindly grades a clean item
    /// `.used`, which is the same defect pointing the other way.
    ///
    /// Measured, rather than argued: adding " and " to `clauseBreaks`
    /// unconditionally fixes the first sentence and breaks the second, an
    /// even trade. Adding " with " as well additionally breaks "New with
    /// tags". A proximity window on the negator cannot separate them either —
    /// "free of stains or damage" and "no stains and heavy wear" put the same
    /// two words between the negator and the term, and want opposite answers.
    ///
    /// What does separate them is the shape of what follows: a bare noun is
    /// the tail of a list the negation still covers, while two or more words
    /// are a fresh claim. That rule passes all thirty-two cases the suite
    /// already had plus six new ones, including "no stains and no heavy wear",
    /// where the negation is simply restated after the break.
    private static func splitOnConjoinedPredicates(_ part: String) -> [String] {
        let chunks = part.components(separatedBy: " and ")
        guard chunks.count > 1 else { return [part] }
        var out: [String] = []
        var current = chunks[0]
        for next in chunks.dropFirst() {
            if next.split(separator: " ").count > 1 {
                out.append(current)
                current = next
            } else {
                current += " and " + next
            }
        }
        out.append(current)
        return out
    }

    private static func clauses(of text: String) -> [String] {
        var parts = [text]
        for separator in clauseBreaks {
            parts = parts.flatMap { $0.components(separatedBy: separator) }
        }
        return parts.flatMap { splitOnConjoinedPredicates($0) }
    }

    /// Endings a matched term may carry and still be the same word, so
    /// "stains", "damaged" and "tearing" count while "stainless",
    /// "undamaged" and "teardrop" do not.
    private static let inflections: Set<String> = ["", "s", "es", "ed", "d", "ing"]

    /// Whether a substring hit is its own word rather than the middle of a
    /// longer one.
    ///
    /// This is the half that was missing. Negation was handled carefully and
    /// substring containment was not, and the comment on the term list — which
    /// warns that "rip" matches "striped" and "wear" matches "menswear" — was
    /// a list of the traps that had been *noticed*. It was incomplete:
    ///
    ///   stain   → stainless      flaw  → flawless
    ///   damage  → undamaged      heavy → heavyweight
    ///   tear    → teardrop       fair  → fairisle
    ///   worn    → unworn         ← this one means the opposite
    ///
    /// A stainless steel watch, a flawless jacket and an unworn dress were all
    /// graded `.used`, which carries a 0.78 multiplier — so the estimate came
    /// back 22% under, and correcting the chip by hand then jumped it 28%.
    ///
    /// Structural rather than another vocabulary patch: a term can now be
    /// added without auditing the rest of the English language for it.
    private static func isWholeWord(_ hit: Range<String.Index>,
                                    in clause: String) -> Bool {
        if hit.lowerBound > clause.startIndex {
            let preceding = clause[clause.index(before: hit.lowerBound)]
            if preceding.isLetter { return false }
        }
        let remainder = clause[hit.upperBound...].prefix { $0.isLetter }
        return inflections.contains(String(remainder))
    }

    /// True when `needle` appears somewhere it is actually being asserted,
    /// rather than denied — and as a word rather than inside a longer one.
    ///
    /// Every occurrence is considered, not just the first: "stainless steel
    /// with a stain on the strap" has to reach the second one.
    private static func asserts(_ needle: String, in clauses: [String]) -> Bool {
        for clause in clauses {
            var searchFrom = clause.startIndex
            while let hit = clause.range(of: needle,
                                         range: searchFrom..<clause.endIndex) {
                searchFrom = hit.upperBound
                guard isWholeWord(hit, in: clause) else { continue }
                let before = clause[clause.startIndex..<hit.lowerBound]
                if !negators.contains(where: { before.contains($0) }) { return true }
            }
        }
        return false
    }

    /// Best-effort starting condition parsed from the model's free-text notes.
    /// The fallback for records with no `condition_grade`; see
    /// `ScanResult.baselineCondition`.
    ///
    /// Negation-aware, and it has to be. `prompts.py` tells the model to write
    /// notes like "Light pilling at cuffs and collar; no stains or holes
    /// visible" — its own canonical example — and a plain `contains("stain")`
    /// grades that clean item `.used`. Since `.used` carries a 0.78 multiplier
    /// and `priceRange` divides by this baseline, correcting the wrong chip
    /// then jumped the estimate 28% for a correction that should have moved
    /// nothing at all.
    ///
    /// Order matters: the strongest signals ("new with tags", "like new") are
    /// checked before the weaker "good"/"used" fallbacks.
    static func inferred(from notes: String) -> Condition {
        let parts = clauses(of: notes.lowercased())
        func says(_ terms: [String]) -> Bool {
            terms.contains { asserts($0, in: parts) }
        }
        // "unworn" belongs here rather than nowhere: with whole-word matching
        // it no longer trips the `.used` branch, and grading an unworn item
        // `.good` understates it by the same 0.78 the old bug applied.
        if says(["new with tag", "nwt", "brand new", "unused", "unworn"]) { return .new }
        if says(["like new", "excellent", "mint", "very good"])  { return .likeNew }
        // Whole words now, not substrings — see `isWholeWord`. That is what
        // makes this list safe to extend: "rip" no longer matches "striped"
        // and "wear" no longer matches "menswear", structurally, rather than
        // because someone remembered to check.
        if says(["fair", "poor", "worn", "torn", "heavy",
                 "damage", "flaw", "stain", "tear"])             { return .used }
        return .good
    }
}

// MARK: - Flip lifecycle status

/// Where an item is in the resale journey. Raw values are persisted in
/// `ScanResult.statusRaw`; never renamed (would orphan saved records).
enum FlipStatus: String, CaseIterable, Identifiable {
    case scanned
    case owned
    case listed
    case sold

    var id: String { rawValue }

    /// Shown on the flip-status picker and in My Flips' filter. Translated;
    /// `rawValue` is the stored value and the SwiftData predicate's operand,
    /// so it stays English.
    var label: String {
        switch self {
        case .scanned: return String(localized: "Scanned", comment: "Flip status")
        case .owned:   return String(localized: "Owned", comment: "Flip status")
        case .listed:  return String(localized: "Listed", comment: "Flip status")
        case .sold:    return String(localized: "Sold", comment: "Flip status")
        }
    }

    var systemImage: String {
        switch self {
        case .scanned: return "magnifyingglass"
        case .owned:   return "bag.fill"
        case .listed:  return "tag.fill"
        case .sold:    return "checkmark.seal.fill"
        }
    }
}
