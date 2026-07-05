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
        imageData: Data? = nil
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
    }

    var formattedRange: String {
        let fmt = NumberFormatter.snapCurrency
        let lo = fmt.string(from: NSNumber(value: valueLow)) ?? "$\(Int(valueLow))"
        let hi = fmt.string(from: NSNumber(value: valueHigh)) ?? "$\(Int(valueHigh))"
        return "\(lo)–\(hi)"
    }

    var midpointValue: Double {
        (valueLow + valueHigh) / 2
    }
}
