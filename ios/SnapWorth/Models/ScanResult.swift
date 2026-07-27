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
        conditionRaw: String? = nil
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
    }

    // ── Condition & re-pricing ─────────────────────────────────────────────────

    /// The resale condition driving the estimate. Reads the user's explicit
    /// choice when set; otherwise the condition inferred from the model's notes,
    /// so an untouched record prices exactly as the AI returned it.
    var condition: Condition {
        get { conditionRaw.flatMap(Condition.init(rawValue:)) ?? Condition.inferred(from: conditionNotes) }
        set { conditionRaw = newValue.rawValue }
    }

    /// The AI's resale range re-scaled from the condition it inferred to
    /// `condition`. `valueLow`/`valueHigh` stay the immutable AI baseline so
    /// repeated selector changes never compound. Exact `Decimal` money; both
    /// features (listing price, flip resale) read from here.
    func priceRange(for condition: Condition) -> (low: Decimal, likely: Decimal, high: Decimal) {
        guard valueLow.isFinite, valueHigh.isFinite else { return (0, 0, 0) }
        let baseline = Condition.inferred(from: conditionNotes)
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

    /// Best-effort starting condition parsed from the model's free-text notes.
    /// Used only until the user makes an explicit choice. Order matters: the
    /// strongest signals ("new with tags", "like new") are checked before the
    /// weaker "good"/"used" fallbacks.
    static func inferred(from notes: String) -> Condition {
        let n = notes.lowercased()
        if n.contains("new with tag") || n.contains("nwt") || n.contains("brand new") || n.contains("unused") {
            return .new
        }
        if n.contains("like new") || n.contains("excellent") || n.contains("mint") || n.contains("very good") {
            return .likeNew
        }
        if n.contains("fair") || n.contains("poor") || n.contains("worn") || n.contains("heavy")
            || n.contains("damage") || n.contains("flaw") || n.contains("stain") || n.contains("tear") {
            return .used
        }
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
