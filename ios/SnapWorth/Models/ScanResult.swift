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
    /// priced, denormalised so the portfolio total can be aggregated without
    /// materialising every object and its externalStorage image.
    ///
    /// Nil on every row written before 1.2.2, and nil is *not* zero: the reader
    /// falls back to computing from `valueLow`/`valueHigh`, so an old library
    /// prices correctly with no backfill pass and no migration write.
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

    /// Decoded on read. Cheap — a few hundred bytes — and only the result
    /// sheet asks for it.
    var valuationDetail: ValuationDetail? { ValuationDetail.decode(valuationDetailData) }

    /// Replace this find's valuation with a re-read that had the label photo
    /// too (#88).
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
    /// One property, used by both `condition` and `priceRange`, deliberately.
    /// They must agree: if the getter defaults to one baseline and the re-scale
    /// divides by another, an untouched record is silently mispriced.
    var baselineCondition: Condition {
        if let grade = valuationDetail?.conditionGrade,
           let graded = Condition(serverGrade: grade) {
            return graded
        }
        return Condition.inferred(from: conditionNotes)
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
        guard valueLow.isFinite, valueHigh.isFinite else { return (0, 0, 0) }
        let baseline = baselineCondition
        let factor = condition.priceMultiplier / baseline.priceMultiplier
        let low = Decimal(valueLow) * factor
        let high = Decimal(valueHigh) * factor
        return (low, (low + high) / 2, high)
    }

    /// Condition-adjusted low/high as `Double`, for the existing range views.
    var displayValueLow: Double { NSDecimalNumber(decimal: priceRange(for: condition).low).doubleValue }
    var displayValueHigh: Double { NSDecimalNumber(decimal: priceRange(for: condition).high).doubleValue }

    var formattedRange: String {
        guard valueLow.isFinite && valueHigh.isFinite else { return "Price unavailable" }
        let range = priceRange(for: condition)
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

    /// The condition-adjusted "likely" value, in Decimal.
    ///
    /// Reads the denormalised column when present and falls back to computing
    /// it otherwise. The fallback is what makes the migration free: rows
    /// written before 1.2.2 have no stored value, and rather than rewriting the
    /// whole store on first launch — a slow, failure-prone operation on exactly
    /// the users with the most to lose — they simply price the way they always
    /// did until something touches them.
    var portfolioValue: Decimal {
        if let raw = portfolioValueRaw, raw.isFinite {
            return Decimal(raw)
        }
        return priceRange(for: condition).likely
    }

    /// Recomputes and stores the denormalised value, and appends a snapshot when
    /// the value actually moved.
    ///
    /// Append-only and deduplicated: re-pricing to the same number does not
    /// grow the history, so opening an item repeatedly cannot inflate it. The
    /// series is capped — an item edited hundreds of times still costs a
    /// bounded amount of storage, and a sparkline cannot usefully render more.
    func refreshPortfolioValue(on date: Date = Date(), limit: Int = 60) {
        let likely = priceRange(for: condition).likely
        let asDouble = NSDecimalNumber(decimal: likely).doubleValue
        guard asDouble.isFinite else { return }

        var history = valueHistory
        // Compare rounded to the cent: Decimal→Double round-trips can differ in
        // the last bit, and a snapshot per open would be noise, not history.
        let changed = history.last.map { abs($0.value - asDouble) >= 0.005 } ?? true
        if changed {
            history.append(ValueSnapshot(date: date, value: asDouble))
            if history.count > limit { history.removeFirst(history.count - limit) }
            valueHistoryData = try? JSONEncoder().encode(history)
        }
        portfolioValueRaw = asDouble
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

    var midpointValue: Double {
        (displayValueLow + displayValueHigh) / 2
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

    var label: String {
        switch self {
        case .new:     return "New"
        case .likeNew: return "Like New"
        case .good:    return "Good"
        case .used:    return "Used"
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

    private static func clauses(of text: String) -> [String] {
        var parts = [text]
        for separator in clauseBreaks {
            parts = parts.flatMap { $0.components(separatedBy: separator) }
        }
        return parts
    }

    /// True when `needle` appears somewhere it is actually being asserted,
    /// rather than denied.
    private static func asserts(_ needle: String, in clauses: [String]) -> Bool {
        for clause in clauses {
            guard let hit = clause.range(of: needle) else { continue }
            let before = clause[clause.startIndex..<hit.lowerBound]
            if !negators.contains(where: { before.contains($0) }) { return true }
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
        if says(["new with tag", "nwt", "brand new", "unused"]) { return .new }
        if says(["like new", "excellent", "mint", "very good"])  { return .likeNew }
        // Substrings, so each term has to be checked against the vocabulary of
        // secondhand clothing before it is added. "torn" is safe. "rip" is not
        // — it matches "striped". "wear" is not — it matches "menswear",
        // "outerwear", "activewear". That trap is the same one that produced
        // this bug in the first place.
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

    var label: String {
        switch self {
        case .scanned: return "Scanned"
        case .owned:   return "Owned"
        case .listed:  return "Listed"
        case .sold:    return "Sold"
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
