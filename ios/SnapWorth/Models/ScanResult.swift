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
    /// What the item eventually sold for.
    var soldPrice: Double?
    var soldDate: Date?
    /// Selling/shipping fees the user expects — treated as 0 in profit math.
    var feesEstimate: Double?
    var notes: String?

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
        soldPrice: Double? = nil,
        soldDate: Date? = nil,
        feesEstimate: Double? = nil,
        notes: String? = nil
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
        self.soldPrice = soldPrice
        self.soldDate = soldDate
        self.feesEstimate = feesEstimate
        self.notes = notes
    }

    var formattedRange: String {
        guard valueLow.isFinite && valueHigh.isFinite else { return "Price unavailable" }
        let fmt = NumberFormatter.snapCurrency
        let lo = fmt.string(from: NSNumber(value: valueLow)) ?? "$\(Int(valueLow))"
        let hi = fmt.string(from: NSNumber(value: valueHigh)) ?? "$\(Int(valueHigh))"
        return "\(lo)–\(hi)"
    }

    var midpointValue: Double {
        (valueLow + valueHigh) / 2
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
