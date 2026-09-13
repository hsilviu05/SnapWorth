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
    /// Set when the ledger write itself failed — see `saveToLedger`.
    var saveError: String?

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
            // Drop the original now the encodes are done: the only surface that
            // shows it is a 64pt header thumbnail, and holding the picker's
            // untouched image for the rest of the session was the largest
            // allocation in this flow.
            itemImage = await ScanAPIClient.thumbnail(image, side: 64)
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
                imageData: storedImage,
                valuationDetailData: ValuationDetail(response: response)?.encoded()
            )
            scanResult = result
            // Seed the resale field with the condition-adjusted likely value.
            resalePriceText = Self.moneyField(result.priceRange(for: result.condition).likely)

            if !purchaseService.isSubscribed {
                FreeScanCounter.increment()
                // The server's count, not just the local one — `remaining`
                // prefers the server value, so incrementing alone left the Scan
                // tab showing the pre-scan number. On a FREE_SCANS_FIRST_DAY=3
                // welcome day that meant three Thrift Flip scans still read
                // "3 left", and the fourth 402'd. This is the daily trigger for
                // the dead-end alert I-23 fixes; both were needed.
                if let remaining = response.freeScansRemaining {
                    FreeScanCounter.serverRemaining = remaining
                }
            }
            Analytics.shared.track(
                .scanCompleted(success: true, category: ItemCategory(normalizing: response.category))
            )
            ScanViewModel.noteScanForStreakAndReminder(isPro: purchaseService.isSubscribed)
        } catch {
            let appError = AppError.from(error)

            // Same 402-is-the-paywall rule as ScanViewModel.startScan: this
            // path shares the daily cap, so it shares the local-vs-UTC day skew
            // that lets a spent allowance past the pre-flight gate.
            if appError.isPaywall {
                Analytics.shared.track(.freeScanLimitHit)
                showPaywall = true
                return
            }

            scanError = appError.errorDescription
            Analytics.shared.track(.scanFailed(reason: ScanFailureReason(appError)))
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
    /// - Returns: whether the flip actually reached the ledger. This used to
    ///   `try?` the save and set `didSaveToLedger` regardless, so a failed
    ///   write gave a success haptic, hid the button, and lost the flip.
    @discardableResult
    func saveToLedger(repository: ScanRepository) -> Bool {
        guard let result = scanResult, let purchase = Self.decimal(shelfPriceText) else { return false }
        result.paidPrice = NSDecimalNumber(decimal: purchase).doubleValue
        result.status = .owned
        do {
            try repository.save(result)
        } catch {
            saveError = "Couldn't save this flip. Try again."
            return false
        }
        saveError = nil
        didSaveToLedger = true
        return true
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
        saveError = nil
    }

    // ── Formatting helpers ──────────────────────────────────────────────────────

    /// A stored amount formatted for an editable text field (no currency symbol).
    static func moneyField(_ value: Decimal) -> String {
        let d = NSDecimalNumber(decimal: value).doubleValue
        let fmt = d.truncatingRemainder(dividingBy: 1) == 0 ? "%.0f" : "%.2f"
        return String(format: fmt, d)
    }

    /// `snapCurrencyCents`, not `snapCurrency`.
    ///
    /// This feature prints an itemised subtraction — resale, minus fees, minus
    /// shipping, minus paid, equals net — and every row went through a
    /// formatter with `maximumFractionDigits = 0`. Each row rounded
    /// independently, so the rows did not add up to the total shown beneath
    /// them: $20.75 − $3.15 − $10.40 rendered as "$21 − $3 − $10" over a net
    /// of "$7", and 21 − 3 − 10 is 8.
    ///
    /// And any profit under a dollar printed as "$0" beneath a green "Worth
    /// flipping", because `isProfitable` is the exact `netProfit > 0`. A
    /// 1-cent margin is a *correct* verdict presented as a contradiction, at
    /// exactly the boundary where the number is the whole decision.
    ///
    /// `snapCurrency` stays where it belongs: a valuation range is an estimate
    /// and cents would imply precision it does not have.
    static func money(_ value: Decimal) -> String {
        NumberFormatter.snapCurrencyCents.string(from: NSDecimalNumber(decimal: value))
            ?? "$0.00"
    }

    static func signedMoney(_ value: Decimal) -> String {
        let base = money(abs(value))
        return value < 0 ? "−\(base)" : base
    }

    static func percent(_ fraction: Decimal) -> String {
        let value = Int((NSDecimalNumber(decimal: fraction).doubleValue * 100).rounded())
        return "\(value)%"
    }

    /// Shared with ResultView's money fields — see `MoneyInput`.
    private static func decimal(_ text: String) -> Decimal? {
        MoneyInput.decimal(text)
    }
}
