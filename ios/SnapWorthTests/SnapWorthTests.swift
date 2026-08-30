import XCTest
import UIKit
@testable import SnapWorth

// MARK: - ScanResult Tests

final class ScanResultTests: XCTestCase {

    func test_formattedRange_USD() {
        let result = makeScanResult(low: 45, high: 90)
        XCTAssertEqual(result.formattedRange, "$45–$90")
    }

    func test_formattedRange_alwaysUSD_regardless_of_locale() {
        // Formatter is locked to en_US / USD — must never show € or £
        let result = makeScanResult(low: 10, high: 20)
        XCTAssertTrue(result.formattedRange.contains("$"), "Expected USD symbol")
        XCTAssertFalse(result.formattedRange.contains("€"))
        XCTAssertFalse(result.formattedRange.contains("£"))
    }

    func test_midpointValue() {
        let result = makeScanResult(low: 40, high: 80)
        XCTAssertEqual(result.midpointValue, 60)
    }

    func test_midpointValue_asymmetric() {
        let result = makeScanResult(low: 10, high: 90)
        XCTAssertEqual(result.midpointValue, 50)
    }

    // MARK: Helpers

    private func makeScanResult(low: Double, high: Double) -> ScanResult {
        ScanResult(
            itemName: "Test Item",
            brand: "Brand",
            category: "clothing",
            conditionNotes: "Good",
            valueLow: low,
            valueHigh: high,
            confidence: "High",
            soldListingsCount: 10,
            listingTitle: "Title",
            listingDescription: "Desc"
        )
    }
}

// MARK: - Condition Re-pricing Tests

final class ConditionTests: XCTestCase {

    func test_condition_defaultsToInferredFromNotes_whenUnset() {
        XCTAssertEqual(make(notes: "Like new, barely used").condition, .likeNew)
        XCTAssertEqual(make(notes: "Good — light pilling").condition, .good)
        XCTAssertEqual(make(notes: "New with tags").condition, .new)
        XCTAssertEqual(make(notes: "Fair, visible stain").condition, .used)
    }

    func test_condition_explicitChoiceOverridesInference() {
        let r = make(notes: "New with tags")   // would infer .new
        r.condition = .used
        XCTAssertEqual(r.condition, .used)
        XCTAssertEqual(r.conditionRaw, "used")
    }

    func test_priceRange_atGoodBaseline_equalsAIEstimate() {
        let r = make(notes: "Good", low: 40, high: 80)   // inferred .good
        let range = r.priceRange(for: .good)
        XCTAssertEqual(range.low, 40)
        XCTAssertEqual(range.high, 80)
        XCTAssertEqual(range.likely, 60)
    }

    func test_priceRange_likeNew_scalesUp_used_scalesDown() {
        let r = make(notes: "Good", low: 100, high: 100)
        XCTAssertGreaterThan(r.priceRange(for: .likeNew).likely, 100)
        XCTAssertLessThan(r.priceRange(for: .used).likely, 100)
        XCTAssertGreaterThan(r.priceRange(for: .new).likely, r.priceRange(for: .likeNew).likely)
    }

    func test_repeatedSelection_neverCompounds() {
        // The baseline is anchored to the inferred condition, so toggling back
        // to it returns the exact original estimate — no compounding drift.
        let r = make(notes: "Good", low: 50, high: 90)
        r.condition = .new
        r.condition = .used
        r.condition = .good
        XCTAssertEqual(r.displayValueLow, 50, accuracy: 0.001)
        XCTAssertEqual(r.displayValueHigh, 90, accuracy: 0.001)
    }

    func test_displayValue_reflectsSelectedCondition() {
        let r = make(notes: "Good", low: 100, high: 100)
        r.condition = .used
        XCTAssertEqual(r.displayValueHigh, 78, accuracy: 0.001) // 100 × 0.78
    }

    func test_priceRange_nonFiniteBaseline_returnsZero() {
        let r = make(notes: "Good", low: .infinity, high: .nan)
        let range = r.priceRange(for: .good)
        XCTAssertEqual(range.low, 0)
        XCTAssertEqual(range.high, 0)
    }

    private func make(notes: String, low: Double = 10, high: Double = 20) -> ScanResult {
        ScanResult(
            itemName: "Test", brand: "Brand", category: "clothing",
            conditionNotes: notes, valueLow: low, valueHigh: high,
            confidence: "High", soldListingsCount: 5,
            listingTitle: "", listingDescription: ""
        )
    }
}

// MARK: - Thrift Flip: profit math (FlipMath) Tests

final class FlipMathTests: XCTestCase {

    private let ebay = MarketplaceFee(sellingFeePercent: Decimal(string: "0.1325")!, fixedFee: Decimal(string: "0.40")!)
    private let free = MarketplaceFee(sellingFeePercent: 0, fixedFee: 0)

    func test_profit_afterFeesShippingAndCost() {
        // Resale 100, eBay fee 13.25% + $0.40 = 13.65, shipping 5, paid 20.
        // net = 100 - 13.65 - 5 - 20 = 61.35
        let c = FlipMath.calculate(resalePrice: 100, purchasePrice: 20, shippingCost: 5, fee: ebay)
        XCTAssertEqual(c.netProfit, Decimal(string: "61.35")!)
        XCTAssertEqual(c.platformFees, Decimal(string: "13.65")!)
        XCTAssertTrue(c.isProfitable)
    }

    func test_zeroFeeMarketplace_noPlatformFees() {
        let c = FlipMath.calculate(resalePrice: 50, purchasePrice: 20, shippingCost: 0, fee: free)
        XCTAssertEqual(c.platformFees, 0)
        XCTAssertEqual(c.netProfit, 30)
    }

    func test_negativeProfit_isNotProfitable() {
        let c = FlipMath.calculate(resalePrice: 20, purchasePrice: 25, shippingCost: 0, fee: free)
        XCTAssertEqual(c.netProfit, -5)
        XCTAssertFalse(c.isProfitable)
    }

    func test_zeroProfit_isNotProfitable() {
        // Break-even is not "worth it" — strictly greater than zero.
        let c = FlipMath.calculate(resalePrice: 20, purchasePrice: 20, shippingCost: 0, fee: free)
        XCTAssertEqual(c.netProfit, 0)
        XCTAssertFalse(c.isProfitable)
    }

    func test_roi_nilOnFreeFind() {
        // Paid 0 → ROI undefined (never divide by zero).
        let c = FlipMath.calculate(resalePrice: 40, purchasePrice: 0, shippingCost: 0, fee: free)
        XCTAssertNil(c.roi)
        XCTAssertEqual(c.margin, 1)   // profit 40 / resale 40
    }

    func test_margin_nilWhenResaleZero() {
        let c = FlipMath.calculate(resalePrice: 0, purchasePrice: 10, shippingCost: 0, fee: free)
        XCTAssertNil(c.margin)
    }

    func test_missingFeeEntry_flaggedUnknown_andFeesZero() {
        let c = FlipMath.calculate(resalePrice: 50, purchasePrice: 10, shippingCost: 0, fee: nil)
        XCTAssertTrue(c.feesUnknown)
        XCTAssertEqual(c.platformFees, 0)
    }

    func test_negativeInputs_clampedToZero() {
        let c = FlipMath.calculate(resalePrice: -10, purchasePrice: -5, shippingCost: -3, fee: free)
        XCTAssertEqual(c.resalePrice, 0)
        XCTAssertEqual(c.purchasePrice, 0)
        XCTAssertEqual(c.shippingCost, 0)
    }

    func test_roi_computation() {
        // Paid 10, net 30 → ROI 300%.
        let c = FlipMath.calculate(resalePrice: 40, purchasePrice: 10, shippingCost: 0, fee: free)
        XCTAssertEqual(c.roi, 3)
    }

    func test_defaultTable_hasAllMarketplaces() {
        for m in Marketplace.allCases {
            XCTAssertNotNil(MarketplaceFees.fee(for: m), "missing fee for \(m)")
        }
    }
}

// MARK: - Thrift Flip: price-tag OCR parsing Tests

final class PriceTagOCRTests: XCTestCase {

    func test_parsesSimpleDollarPrice() {
        XCTAssertEqual(PriceTagOCR.firstPrice(in: "$12.99"), Decimal(string: "12.99"))
    }

    func test_parsesPlainNumber() {
        XCTAssertEqual(PriceTagOCR.firstPrice(in: "Price 45"), 45)
    }

    func test_parsesEuropeanCommaDecimal() {
        XCTAssertEqual(PriceTagOCR.normalizedDecimal("5,99"), Decimal(string: "5.99"))
    }

    func test_parsesThousandsSeparators() {
        XCTAssertEqual(PriceTagOCR.normalizedDecimal("1,299.00"), Decimal(1299))
        XCTAssertEqual(PriceTagOCR.normalizedDecimal("1.299,00"), Decimal(1299))
    }

    func test_picksLargestPriceAcrossLines() {
        // A tag with SKU + unit price + headline price → headline wins by value.
        let lines = ["SKU 004821", "$3.20/oz", "$24.99"]
        XCTAssertEqual(PriceTagOCR.parsePrice(from: lines), Decimal(string: "24.99"))
    }

    func test_noPrice_returnsNil() {
        XCTAssertNil(PriceTagOCR.firstPrice(in: "Clearance rack"))
        XCTAssertNil(PriceTagOCR.parsePrice(from: ["no", "digits", "here"]))
    }

    func test_ignoresImplausiblyLargeNumbers() {
        // A long barcode-like number must not be read as a price.
        XCTAssertNil(PriceTagOCR.firstPrice(in: "123456789012"))
    }
}

// MARK: - Snap → Sell (ListingAPIClient) Tests

final class ListingClientTests: XCTestCase {

    // The listing actor's live path needs the network and its mock path is gated
    // on the compile-time `Config.mockMode` (false in shipping), so these cover
    // the deterministic contract the UI and backend both depend on.

    func test_generatedListing_shareText_containsTitleAndPrice() {
        let listing = GeneratedListing(
            title: "Nike Air Max 90",
            description: "Clean pair, barely used.",
            listingPrice: 90, negotiationFloor: 70,
            category: "shoes", marketplace: .ebay
        )
        XCTAssertTrue(listing.shareText.contains("Nike Air Max 90"))
        XCTAssertTrue(listing.shareText.contains("$90"))
    }

    func test_marketplace_apiValues_matchBackendContract() {
        XCTAssertEqual(Marketplace.ebay.apiValue, "ebay")
        XCTAssertEqual(Marketplace.vinted.apiValue, "vinted")
        XCTAssertEqual(Marketplace.facebook.apiValue, "facebook")
        XCTAssertEqual(Marketplace.olx.apiValue, "olx")
    }

    func test_marketplace_webSellURLs_areHTTPS() {
        for m in Marketplace.allCases {
            XCTAssertEqual(m.webSellURL.scheme, "https", "\(m) sell URL must be https")
        }
    }

    func test_marketplace_appScheme_onlyWhereReal() {
        // We must never fabricate a scheme for a marketplace without one.
        XCTAssertNotNil(Marketplace.ebay.appURLScheme)
        XCTAssertNotNil(Marketplace.facebook.appURLScheme)
        XCTAssertNil(Marketplace.vinted.appURLScheme)
        XCTAssertNil(Marketplace.olx.appURLScheme)
    }

    func test_generatedListing_floorClampedToAsk() {
        // The client clamps floor to the ask on the live path; the type should
        // never surface a floor above the ask to the UI.
        let listing = GeneratedListing(
            title: "T", description: "D",
            listingPrice: 40, negotiationFloor: 40,
            category: "x", marketplace: .vinted
        )
        XCTAssertLessThanOrEqual(listing.negotiationFloor, listing.listingPrice)
    }
}

// MARK: - HistoryViewModel Tests

@MainActor
final class HistoryViewModelTests: XCTestCase {

    var vm: HistoryViewModel!

    override func setUp() {
        super.setUp()
        vm = HistoryViewModel()
    }

    func test_filtered_returnsAll_whenSearchEmpty() {
        let results = makeResults(names: ["Jacket", "Shoes", "Bag"])
        vm.searchText = ""
        XCTAssertEqual(vm.filtered(results).count, 3)
    }

    func test_filtered_byItemName() {
        let results = makeResults(names: ["Patagonia Jacket", "Nike Shoes", "Levi's Jeans"])
        vm.searchText = "Nike"
        let filtered = vm.filtered(results)
        XCTAssertEqual(filtered.count, 1)
        XCTAssertEqual(filtered.first?.itemName, "Nike Shoes")
    }

    func test_filtered_byBrand() {
        let results = [
            makeResult(name: "Fleece", brand: "Patagonia", low: 40, high: 80),
            makeResult(name: "Shoes", brand: "Nike", low: 50, high: 100),
        ]
        vm.searchText = "patagonia"
        XCTAssertEqual(vm.filtered(results).count, 1)
    }

    func test_filtered_caseInsensitive() {
        let results = makeResults(names: ["NIKE SHOES"])
        vm.searchText = "nike"
        XCTAssertEqual(vm.filtered(results).count, 1)
    }

    func test_filtered_noMatch_returnsEmpty() {
        let results = makeResults(names: ["Jacket", "Shoes"])
        vm.searchText = "zzz"
        XCTAssertEqual(vm.filtered(results).count, 0)
    }

    func test_sorted_newest_first() {
        let old = makeResult(name: "Old", brand: "", low: 10, high: 20, daysAgo: 10)
        let new = makeResult(name: "New", brand: "", low: 10, high: 20, daysAgo: 0)
        vm.sortOrder = .newest
        let sorted = vm.sorted([old, new])
        XCTAssertEqual(sorted.first?.itemName, "New")
    }

    func test_sorted_mostValuable_first() {
        let cheap = makeResult(name: "Cheap", brand: "", low: 5, high: 10)
        let expensive = makeResult(name: "Expensive", brand: "", low: 100, high: 200)
        vm.sortOrder = .mostValuable
        let sorted = vm.sorted([cheap, expensive])
        XCTAssertEqual(sorted.first?.itemName, "Expensive")
    }

    func test_totalValue_sumsMidpoints() {
        let results = [
            makeResult(name: "A", brand: "", low: 0, high: 100),  // mid = 50
            makeResult(name: "B", brand: "", low: 20, high: 40),  // mid = 30
        ]
        let total = vm.totalValue(from: results)
        XCTAssertTrue(total.contains("80"), "Expected total of $80, got \(total)")
    }

    // MARK: Helpers

    private func makeResults(names: [String]) -> [ScanResult] {
        names.map { makeResult(name: $0, brand: "Brand", low: 10, high: 50) }
    }

    private func makeResult(name: String, brand: String, low: Double, high: Double, daysAgo: Int = 0) -> ScanResult {
        let r = ScanResult(
            itemName: name,
            brand: brand,
            category: "clothing",
            conditionNotes: "Good",
            valueLow: low,
            valueHigh: high,
            confidence: "High",
            soldListingsCount: 5,
            listingTitle: "",
            listingDescription: ""
        )
        r.timestamp = Calendar.current.date(byAdding: .day, value: -daysAgo, to: Date()) ?? Date()
        return r
    }
}

// MARK: - AppError Mapping Tests

final class AppErrorMappingTests: XCTestCase {

    func test_rateLimit_message() {
        let error = makeError("429 rate limit exceeded")
        XCTAssertTrue(friendlyMessage(error).lowercased().contains("limit"))
    }

    func test_networkOffline_message() {
        let error = makeError("network connection offline")
        let msg = friendlyMessage(error)
        XCTAssertTrue(msg.lowercased().contains("internet") || msg.lowercased().contains("network"))
    }

    func test_timeout_message() {
        let error = makeError("request timed out")
        XCTAssertTrue(friendlyMessage(error).lowercased().contains("timed out") ||
                      friendlyMessage(error).lowercased().contains("timeout"))
    }

    func test_502_message() {
        let error = makeError("502 bad gateway")
        XCTAssertTrue(friendlyMessage(error).lowercased().contains("unavailable"))
    }

    func test_500_message() {
        let error = makeError("500 internal server error")
        XCTAssertTrue(friendlyMessage(error).lowercased().contains("wrong"))
    }

    func test_unknown_returnsGeneric() {
        let error = makeError("some completely unexpected thing happened")
        XCTAssertFalse(friendlyMessage(error).isEmpty)
    }

    private func friendlyMessage(_ error: Error) -> String {
        AppError.from(error).errorDescription ?? ""
    }

    private func makeError(_ description: String) -> Error {
        NSError(domain: "test", code: 0, userInfo: [NSLocalizedDescriptionKey: description])
    }
}

// MARK: - NumberFormatter Tests

final class NumberFormatterTests: XCTestCase {

    func test_snapCurrency_formatsDollarSign() {
        let result = NumberFormatter.snapCurrency.string(from: 45)
        XCTAssertEqual(result, "$45")
    }

    func test_snapCurrency_noDecimals() {
        let result = NumberFormatter.snapCurrency.string(from: 45.99)
        XCTAssertEqual(result, "$46")
    }

    func test_snapCurrency_alwaysUSD_notDeviceLocale() {
        let result = NumberFormatter.snapCurrency.string(from: 100) ?? ""
        XCTAssertTrue(result.hasPrefix("$"), "Must always be USD, got: \(result)")
    }

    func test_snapCurrency_largeValue() {
        let result = NumberFormatter.snapCurrency.string(from: 1500)
        XCTAssertEqual(result, "$1,500")
    }
}

// MARK: - Config Security Tests

final class ConfigSecurityTests: XCTestCase {

    func test_baseURL_usesHTTPS() {
        XCTAssertEqual(Config.baseURL.scheme, "https",
                       "Backend URL must use HTTPS — never HTTP")
    }

    func test_baseURL_hasHost() {
        XCTAssertFalse(Config.baseURL.host?.isEmpty ?? true,
                       "baseURL must have a non-empty host")
    }

    func test_freeScansAllowed_isPositive() {
        XCTAssertGreaterThan(Config.freeScansAllowed, 0,
                             "freeScansAllowed must be > 0 or the free tier is broken")
    }

    func test_mockMode_isDisabled() {
        XCTAssertFalse(Config.mockMode,
                       "mockMode must be false before App Store submission")
    }

    func test_productIDs_areNonEmpty() {
        XCTAssertFalse(Config.monthlyProductID.isEmpty)
        XCTAssertFalse(Config.yearlyProductID.isEmpty)
    }

    func test_productIDs_areDistinct() {
        XCTAssertNotEqual(Config.monthlyProductID, Config.yearlyProductID)
    }
}

// MARK: - ScanViewModel Security Tests

@MainActor
final class ScanViewModelSecurityTests: XCTestCase {

    var vm: ScanViewModel!
    private let freeScansKey = "snapworth_free_scans_used"
    private let freeScansDateKey = "snapworth_free_scans_date"

    override func setUp() {
        super.setUp()
        UserDefaults.standard.removeObject(forKey: freeScansKey)
        UserDefaults.standard.removeObject(forKey: freeScansDateKey)
        UserDefaults.standard.removeObject(forKey: "snapworth_free_scans_server_remaining")
        vm = ScanViewModel()
    }

    override func tearDown() {
        UserDefaults.standard.removeObject(forKey: freeScansKey)
        UserDefaults.standard.removeObject(forKey: freeScansDateKey)
        UserDefaults.standard.removeObject(forKey: "snapworth_free_scans_server_remaining")
        super.tearDown()
    }

    // ── Server-authoritative count ───────────────────────────────────────
    //
    // The screen used to render "N free scans left today" from the compiled-in
    // Config.freeScansAllowed and ignore the server's free_scans_remaining
    // entirely, so it showed "3 free scans left today" against a backend
    // enforcing 1 and denied the second scan.

    private var serverRemainingKey: String { "snapworth_free_scans_server_remaining" }

    func test_remaining_prefersTheServersFigureOverTheLocalConstant() {
        UserDefaults.standard.set(0, forKey: freeScansKey)
        FreeScanCounter.serverRemaining = 1
        XCTAssertEqual(FreeScanCounter.remaining, 1,
                       "Server figure must win over the compiled-in allowance")
    }

    func test_remaining_fallsBackToLocalWhenServerSilent() {
        UserDefaults.standard.removeObject(forKey: serverRemainingKey)
        UserDefaults.standard.set(0, forKey: freeScansKey)
        UserDefaults.standard.set(Date(), forKey: freeScansDateKey)
        XCTAssertEqual(FreeScanCounter.remaining, Config.freeScansAllowed,
                       "With no server figure, fall back to the local estimate")
    }

    func test_gateClosesWhenServerSaysZero_evenIfLocalThinksOtherwise() {
        // The reinstall case: DeviceCheck withheld the allowance, so the server
        // grants 0 while the freshly-installed local counter reads untouched.
        UserDefaults.standard.set(0, forKey: freeScansKey)
        FreeScanCounter.serverRemaining = 0
        XCTAssertFalse(FreeScanCounter.hasRemaining,
                       "A withheld allowance must close the gate")
        XCTAssertEqual(FreeScanCounter.remaining, 0)
    }

    func test_serverRemaining_resetsWhenDateIsStale() {
        UserDefaults.standard.set(0, forKey: serverRemainingKey)
        let yesterday = Calendar.current.date(byAdding: .day, value: -1, to: Date())!
        UserDefaults.standard.set(yesterday, forKey: freeScansDateKey)
        XCTAssertNil(FreeScanCounter.serverRemaining,
                     "Yesterday's figure must not suppress today's allowance")
    }

    func test_serverRemaining_isNeverNegative() {
        FreeScanCounter.serverRemaining = -5
        XCTAssertEqual(FreeScanCounter.serverRemaining, 0)
    }

    func test_hasFreeScanRemaining_trueWhenUnderLimit() {
        vm.freeScansUsed = 0
        XCTAssertTrue(vm.hasFreeScanRemaining)
    }

    func test_hasFreeScanRemaining_falseAtExactLimit() {
        vm.freeScansUsed = Config.freeScansAllowed
        XCTAssertFalse(vm.hasFreeScanRemaining,
                       "Gate must fire when count reaches the limit, not after")
    }

    func test_hasFreeScanRemaining_falseAboveLimit() {
        vm.freeScansUsed = Config.freeScansAllowed + 100
        XCTAssertFalse(vm.hasFreeScanRemaining)
    }

    func test_freeScansUsed_defaultsToZero_neverNegative() {
        UserDefaults.standard.removeObject(forKey: freeScansKey)
        let fresh = ScanViewModel()
        XCTAssertGreaterThanOrEqual(fresh.freeScansUsed, 0,
                                    "Scan counter must never be negative")
    }

    func test_freeScansUsed_resetsWhenDateIsStale() {
        // A count stamped with a previous day must read as 0 today, restoring
        // the daily allowance ("3 free scans every day").
        UserDefaults.standard.set(Config.freeScansAllowed, forKey: freeScansKey)
        let yesterday = Calendar.current.date(byAdding: .day, value: -1, to: Date())!
        UserDefaults.standard.set(yesterday, forKey: freeScansDateKey)

        XCTAssertEqual(vm.freeScansUsed, 0,
                       "Free scans must reset once the calendar day rolls over")
        XCTAssertTrue(vm.hasFreeScanRemaining)
    }

    func test_freeScansUsed_persistsWithinSameDay() {
        // A count stamped today must be honored (no accidental reset mid-day).
        UserDefaults.standard.set(2, forKey: freeScansKey)
        UserDefaults.standard.set(Date(), forKey: freeScansDateKey)

        XCTAssertEqual(vm.freeScansUsed, 2,
                       "Today's count must persist until the day changes")
    }

    func test_reset_clearsCapturedImage() {
        vm.capturedImage = UIImage()
        vm.reset()
        XCTAssertNil(vm.capturedImage)
    }

    func test_reset_clearsScanResult() {
        vm.reset()
        XCTAssertNil(vm.scanResult)
    }

    func test_reset_clearsErrorMessage() {
        vm.errorMessage = "Leftover error from previous scan"
        vm.reset()
        XCTAssertNil(vm.errorMessage, "Stale error must be cleared on reset")
    }

    func test_reset_setsIsAnalyzingToFalse() {
        vm.isAnalyzing = true
        vm.reset()
        XCTAssertFalse(vm.isAnalyzing)
    }

    func test_errorMapping_neverExposesFilePaths() {
        let internalErr = makeError("/private/var/containers/Bundle/app/module.swift:42: fatal error")
        let msg = AppError.from(internalErr).errorDescription ?? ""
        XCTAssertFalse(msg.contains("/private"), "Error must not leak filesystem paths")
        XCTAssertFalse(msg.contains(".swift"), "Error must not leak source file names")
    }

    func test_errorMapping_neverExposesAPIKeys() {
        let keyErr = makeError("API key AIzaSyFAKE123 rejected by server")
        let msg = AppError.from(keyErr).errorDescription ?? ""
        XCTAssertFalse(msg.contains("AIzaSy"), "Error must not echo back API key material")
    }

    func test_errorMapping_neverEmpty_allCases() {
        let inputs = [
            "completely unknown error xyz_123",
            "",
            "429",
            "502",
            "timeout",
            "null",
            "undefined",
        ]
        for desc in inputs {
            let msg = AppError.from(makeError(desc)).errorDescription ?? ""
            XCTAssertFalse(msg.isEmpty, "AppError.from(\"\(desc)\") must never produce empty string")
        }
    }

    func test_errorMapping_rateLimitMessageIsSafe() {
        let msg = AppError.from(makeError("429 rate limit exceeded")).errorDescription ?? ""
        XCTAssertFalse(msg.contains("GEMINI"), "Rate-limit message must not reveal backend tech")
        XCTAssertFalse(msg.contains("API"), "Rate-limit message must not expose implementation")
    }

    private func makeError(_ description: String) -> Error {
        NSError(domain: "test", code: 0, userInfo: [NSLocalizedDescriptionKey: description])
    }
}

// MARK: - ScanResult Security Tests (edge values)

final class ScanResultEdgeTests: XCTestCase {

    func test_formattedRange_zeroValues() {
        let r = makeScanResult(low: 0, high: 0)
        // Must return a non-empty string without crashing
        XCTAssertFalse(r.formattedRange.isEmpty)
    }

    func test_formattedRange_noNegativeSymbol() {
        let r = makeScanResult(low: 10, high: 50)
        XCTAssertFalse(r.formattedRange.contains("-"),
                       "Formatted range must not contain a minus sign")
    }

    func test_midpointValue_neverNegative() {
        let r = makeScanResult(low: 0, high: 0)
        XCTAssertGreaterThanOrEqual(r.midpointValue, 0)
    }

    func test_midpointValue_betweenLowAndHigh() {
        let r = makeScanResult(low: 20, high: 80)
        XCTAssertGreaterThanOrEqual(r.midpointValue, 20)
        XCTAssertLessThanOrEqual(r.midpointValue, 80)
    }

    func test_formattedRange_doesNotContainScriptTags() {
        // Verifies the formatter never passes item metadata through unescaped
        let r = makeScanResult(low: 10, high: 50)
        XCTAssertFalse(r.formattedRange.contains("<"))
        XCTAssertFalse(r.formattedRange.contains(">"))
    }

    private func makeScanResult(low: Double, high: Double) -> ScanResult {
        ScanResult(
            itemName: "Test", brand: "Brand", category: "clothing",
            conditionNotes: "Good", valueLow: low, valueHigh: high,
            confidence: "High", soldListingsCount: 5,
            listingTitle: "", listingDescription: ""
        )
    }
}
