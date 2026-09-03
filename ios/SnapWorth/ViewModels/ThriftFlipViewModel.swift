import SwiftUI
import SwiftData

/// Drives Thrift Flip: scan an item (reusing the valuation core), read its shelf
/// price (OCR or manual), and get an instant buy/skip profit verdict. Money math
/// is done in `Decimal` via `FlipMath`.
@MainActor
@Observable
final class ThriftFlipViewModel {
    // ── Item scan ──────────────────────────────────────────────────────────────
    var itemImage: UIImage?
    var scanResult: ScanResult?
    var isScanningItem = false
    var scanError: String?
    var showPaywall = false

    // ── Inputs ───────────────────────────────────────────────────────────────
    var selectedMarketplace: Marketplace = .ebay
    /// What the item costs in the shop — the purchase price. From OCR or manual.
    var shelfPriceText = ""
    /// Expected resale price; defaults to the scan's condition-adjusted "likely".
    var resalePriceText = ""
    /// Expected shipping cost the seller eats; blank == 0.
    var shippingText = ""

    // ── OCR ────────────────────────────────────────────────────────────────────
    var isReadingTag = false
    var ocrNote: String?
    var didSaveToLedger = false

    // ── Scan the item (respects the shared daily free-scan cap) ─────────────────
    func scanItem(image: UIImage, purchaseService: any PurchaseService) async {
        guard !isScanningItem else { return }
        // Base valuation stays open, but a scan is a scan — enforce the same daily
        // cap here so Thrift Flip can't be used to bypass it.
        guard purchaseService.isSubscribed || FreeScanCounter.hasRemaining else {
            Analytics.shared.track(.freeScanLimitHit)
            showPaywall = true
            return
        }

        isScanningItem = true
        scanError = nil
        itemImage = image
        defer { isScanningItem = false }

        do {
            let response = try await ScanAPIClient.shared.scan(image: image)
            // Encoded off the main actor — see ScanAPIClient.encodeForStorage.
            let storedImage = await ScanAPIClient.encodeForStorage(image)
            let result = ScanResult(
                itemName: response.itemName,
                brand: response.brand,
                category: response.category,
                conditionNotes: response.conditionNotes,
                valueLow: response.estValueLowUsd,
                valueHigh: response.estValueHighUsd,
                confidence: response.confidence,
                soldListingsCount: response.soldListingsCount,
                listingTitle: response.listingTitle,
                listingDescription: response.listingDescription,
                imageData: storedImage
            )
            scanResult = result
            // Seed the resale field with the condition-adjusted likely value.
            resalePriceText = Self.moneyField(result.priceRange(for: result.condition).likely)

            if !purchaseService.isSubscribed { FreeScanCounter.increment() }
            Analytics.shared.track(
                .scanCompleted(success: true, category: ItemCategory(normalizing: response.category))
            )
            ScanViewModel.noteScanForStreakAndReminder(isPro: purchaseService.isSubscribed)
        } catch {
            scanError = AppError.from(error).errorDescription
            Analytics.shared.track(.scanFailed(reason: ScanFailureReason(AppError.from(error))))
        }
    }

    // ── Read the price tag (on-device OCR, manual fallback) ─────────────────────
    func readPriceTag(image: UIImage) async {
        isReadingTag = true
        ocrNote = nil
        defer { isReadingTag = false }
        do {
            let price = try await PriceTagOCR.detectPrice(in: image)
            shelfPriceText = Self.moneyField(price)
            ocrNote = "Read \(Self.money(price)) — tap to correct if it's off."
        } catch {
            ocrNote = "Couldn't read the tag — enter the price manually."
        }
    }

    // ── Verdict ─────────────────────────────────────────────────────────────────

    /// Live profit calc. Nil until both a resale price (>0) and a shelf price are
    /// present — we never show a verdict from a missing input.
    var calculation: FlipCalculation? {
        guard let resale = Self.decimal(resalePriceText), resale > 0,
              let purchase = Self.decimal(shelfPriceText) else { return nil }
        let shipping = Self.decimal(shippingText) ?? 0
        return FlipMath.calculate(
            resalePrice: resale,
            purchasePrice: purchase,
            shippingCost: shipping,
            fee: MarketplaceFees.fee(for: selectedMarketplace)
        )
    }

    func trackVerdict() {
        guard let calculation else { return }
        Analytics.shared.track(.thriftFlipCalculated(verdict: calculation.isProfitable ? "profit" : "loss"))
    }

    /// Saves the flip into the "My Flips" ledger as an owned item, carrying the
    /// paid price forward so realized profit can be tracked when it sells.
    func saveToLedger(repository: ScanRepository) {
        guard let result = scanResult, let purchase = Self.decimal(shelfPriceText) else { return }
        result.paidPrice = NSDecimalNumber(decimal: purchase).doubleValue
        result.status = .owned
        try? repository.save(result)
        didSaveToLedger = true
    }

    func reset() {
        itemImage = nil
        scanResult = nil
        scanError = nil
        shelfPriceText = ""
        resalePriceText = ""
        shippingText = ""
        ocrNote = nil
        didSaveToLedger = false
    }

    // ── Formatting helpers ──────────────────────────────────────────────────────

    /// A stored amount formatted for an editable text field (no currency symbol).
    static func moneyField(_ value: Decimal) -> String {
        let d = NSDecimalNumber(decimal: value).doubleValue
        let fmt = d.truncatingRemainder(dividingBy: 1) == 0 ? "%.0f" : "%.2f"
        return String(format: fmt, d)
    }

    static func money(_ value: Decimal) -> String {
        NumberFormatter.snapCurrency.string(from: NSDecimalNumber(decimal: value)) ?? "$0"
    }

    static func signedMoney(_ value: Decimal) -> String {
        let base = money(abs(value))
        return value < 0 ? "−\(base)" : base
    }

    static func percent(_ fraction: Decimal) -> String {
        let value = Int((NSDecimalNumber(decimal: fraction).doubleValue * 100).rounded())
        return "\(value)%"
    }

    private static func decimal(_ text: String) -> Decimal? {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return nil }
        return Decimal(string: trimmed.replacingOccurrences(of: ",", with: "."))
    }
}
