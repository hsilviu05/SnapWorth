import ActivityKit
import AVFoundation
import SwiftUI
import XCTest
import ImageIO
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

// MARK: - Price-tag OCR orientation
//
// Vision reads the raw sensor bitmap. `UIImage` keeps the camera's rotation in
// `imageOrientation` and never turns the pixels, so omitting the orientation
// presented every portrait capture — the reader's primary input — a quarter
// turn out. The parser tests below are all pure, which is why CI stayed green
// while the feature was broken end to end.

final class PriceTagOCROrientationTests: XCTestCase {

    /// All eight cases. A transposed `left`/`right` is the classic way this is
    /// got wrong, and it fails silently: the OCR simply finds nothing.
    func test_everyUIImageOrientationMapsToItsCGCounterpart() {
        let pairs: [(UIImage.Orientation, CGImagePropertyOrientation)] = [
            (.up, .up), (.upMirrored, .upMirrored),
            (.down, .down), (.downMirrored, .downMirrored),
            (.left, .left), (.leftMirrored, .leftMirrored),
            (.right, .right), (.rightMirrored, .rightMirrored),
        ]
        for (uiKit, imageIO) in pairs {
            XCTAssertEqual(CGImagePropertyOrientation(uiKit), imageIO,
                           "\(uiKit) mapped to the wrong CGImagePropertyOrientation")
        }
        // `UIImage.Orientation` is an Objective-C enum and is not CaseIterable,
        // so the count is asserted against the eight cases UIKit defines rather
        // than derived. If Apple ever adds one, the `@unknown default` in the
        // initializer keeps it compiling and this number stops being the truth.
        XCTAssertEqual(pairs.count, 8)
    }

    /// A card the reader should manage, upright and untagged.
    ///
    /// Guards the regression the fix itself could cause: passing an orientation
    /// where none was passed before must not break the case that already
    /// worked. The rotated case is deliberately *not* asserted here — it cannot
    /// be fixtured and verified in this environment without guessing the sign
    /// of the rotation, and a test that guesses is worse than none.
    func test_anUprightTagStillReads() async throws {
        let image = Self.card(text: "$12.99")
        let price = try await PriceTagOCR.detectPrice(in: image)
        XCTAssertEqual(price, Decimal(string: "12.99"))
    }

    private static func card(text: String) -> UIImage {
        let size = CGSize(width: 800, height: 400)
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1
        format.opaque = true
        return UIGraphicsImageRenderer(size: size, format: format).image { ctx in
            UIColor.white.setFill()
            ctx.fill(CGRect(origin: .zero, size: size))
            let attributed = NSAttributedString(string: text, attributes: [
                .font: UIFont.boldSystemFont(ofSize: 160),
                .foregroundColor: UIColor.black,
            ])
            let bounds = attributed.size()
            attributed.draw(at: CGPoint(x: (size.width - bounds.width) / 2,
                                        y: (size.height - bounds.height) / 2))
        }
    }
}

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

    // ── A dot can be a thousands separator too ───────────────────────────────

    func test_dotGroupedThousandsAreNotReadAsCents() {
        // There was a branch for both separators and one for comma-only, and
        // none for dot-only — so the dot fell through to `Decimal(string:)` as
        // a decimal point, contradicting the regex that matched it as
        // grouping. "€1.299" became 1.299, and a €1299 item was priced against
        // a $1.30 cost basis.
        XCTAssertEqual(PriceTagOCR.normalizedDecimal("1.299"), Decimal(1299))
        XCTAssertEqual(PriceTagOCR.normalizedDecimal("12.500"), Decimal(12500))
        XCTAssertEqual(PriceTagOCR.normalizedDecimal("1.500.000"), Decimal(1_500_000))
        XCTAssertEqual(PriceTagOCR.firstPrice(in: "€1.299"), Decimal(1299))
    }

    func test_twoDecimalPlacesAreStillCents() {
        // The other half of the same rule: one separator with one or two
        // trailing digits is a fraction, three is grouping.
        XCTAssertEqual(PriceTagOCR.normalizedDecimal("12.99"), Decimal(string: "12.99"))
        XCTAssertEqual(PriceTagOCR.normalizedDecimal("12.9"), Decimal(string: "12.9"))
        XCTAssertEqual(PriceTagOCR.normalizedDecimal("5,99"), Decimal(string: "5.99"))
        XCTAssertEqual(PriceTagOCR.normalizedDecimal("1,299"), Decimal(1299))
    }

    func test_spaceGroupedThousandsAreOneNumber() {
        // French, Nordic and Polish tags print 1299 as "1 299", usually with a
        // no-break space. The `\s?` used to sit outside the numeric group, so
        // the scanner produced two tokens and returned 299.
        XCTAssertEqual(PriceTagOCR.firstPrice(in: "1 299 €"), Decimal(1299))
        XCTAssertEqual(PriceTagOCR.firstPrice(in: "1\u{00A0}299 €"), Decimal(1299))
        XCTAssertEqual(PriceTagOCR.firstPrice(in: "1\u{202F}299 €"), Decimal(1299))
    }

    // ── Strength comes from the token, not the value ─────────────────────────

    func test_wholeCentPriceBeatsALargerSizeNumber() {
        // "19.00" carries an explicit fraction — an unambiguous price signal —
        // but parses to the integer 19, so strength derived from the *value*
        // classed it weak and it lost to the waist and length numbers on the
        // same line. Those are 30-44; thrift prices are 5-20, so the size won
        // whenever the price was printed without a symbol and with .00 cents.
        XCTAssertEqual(PriceTagOCR.firstPrice(in: "W32 L34 19.00"),
                       Decimal(string: "19.00"))
    }

    func test_aGroupedTokenIsWeakLikeAnyBareInteger() {
        // The fraction test is anchored and capped at two digits precisely so
        // the dot-grouping fix above does not also make "1.299" *strong*. A
        // bare grouped number is a bare number: it loses to a symbol-bearing
        // candidate even though it is far larger.
        XCTAssertEqual(PriceTagOCR.parsePrice(from: ["1.299", "$12.99"]),
                       Decimal(string: "12.99"))
        // With its symbol, the same token is strong and wins.
        XCTAssertEqual(PriceTagOCR.parsePrice(from: ["€1.299", "$12.99"]),
                       Decimal(1299))
    }

    func test_aTrailingCurrencySymbolCountsAsASymbol() {
        // Most of Europe prints the symbol after the number, and the docstring
        // already claimed to handle "Sale 12,99 €" while nothing read it — so
        // those tags were never strong and any integer could outrank them.
        XCTAssertEqual(PriceTagOCR.firstPrice(in: "Sale 12,99 € SIZE 40"),
                       Decimal(string: "12.99"))
    }

    // ── Percentages and rates are not prices ─────────────────────────────────

    func test_percentagesAreNotPrices() {
        XCTAssertNil(PriceTagOCR.firstPrice(in: "70% OFF"))
        XCTAssertNil(PriceTagOCR.firstPrice(in: "100% COTTON"))
        XCTAssertNil(PriceTagOCR.firstPrice(in: "70 % OFF"))
    }

    func test_perUnitRatesAndDatesAreNotPrices() {
        // Both sides of the slash: a rate's denominator is no more a price
        // than its numerator, and "12/25" is a date.
        XCTAssertNil(PriceTagOCR.firstPrice(in: "$1.99/oz"))
        XCTAssertNil(PriceTagOCR.firstPrice(in: "12/25"))
        XCTAssertNil(PriceTagOCR.firstPrice(in: "SIZE 12/14"))
    }

    func test_aPriceAfterASlashIsStillAPrice() {
        // The reason the test is around the token and not the whole match.
        XCTAssertEqual(PriceTagOCR.firstPrice(in: "Buy 2/$5"), Decimal(5))
    }
}

// MARK: - Snap → Sell (ListingAPIClient) Tests

final class ListingClientTests: XCTestCase {

    // The listing actor's live path needs the network and its mock path is gated
    // on the compile-time `Config.mockMode` (false in shipping), so these cover
    // the deterministic contract the UI and backend both depend on.

    // ── Nothing owned by a ModelContext crosses to the actor ─────────────────

    @MainActor
    func test_theListingInputIsASnapshotNotTheModel() {
        // `ListingAPIClient` is an `actor`, so its parameters leave the
        // MainActor. It used to take the `ScanResult` itself — a
        // `@Model final class`, not `Sendable` — and read `valueLow`,
        // `valueHigh` and `conditionRaw` on a cooperative-pool thread while
        // the main thread was free to mutate the same object by tapping a
        // condition chip or typing in the Paid field. Swift 5 language mode
        // makes that a warning, not an error, which is why it shipped.
        let result = ScanResult(itemName: "Patagonia Better Sweater", brand: "Patagonia",
                                category: "clothing", conditionNotes: "Good",
                                valueLow: 40, valueHigh: 90, confidence: "High",
                                soldListingsCount: 0, listingTitle: "T",
                                listingDescription: "D")
        let input = ListingInput(result: result, condition: .used)

        XCTAssertEqual(input.itemName, "Patagonia Better Sweater")
        XCTAssertEqual(input.condition, .used)

        // The snapshot is taken by value, so a later edit cannot reach it —
        // which is the whole property, since the edit is what used to race.
        result.itemName = "something else"
        result.valueLow = 1
        XCTAssertEqual(input.itemName, "Patagonia Better Sweater")
    }

    @MainActor
    func test_theInputCarriesTheConditionAdjustedRange() {
        // The range has to be computed where the model lives, not on the
        // actor: `priceRange` reads three stored properties.
        let result = ScanResult(itemName: "I", brand: "B", category: "clothing",
                                conditionNotes: "Good", valueLow: 40, valueHigh: 90,
                                confidence: "High", soldListingsCount: 0,
                                listingTitle: "T", listingDescription: "D")
        let expected = result.priceRange(for: .used)
        let input = ListingInput(result: result, condition: .used)
        XCTAssertEqual(input.low, expected.low)
        XCTAssertEqual(input.likely, expected.likely)
        XCTAssertEqual(input.high, expected.high)
        XCTAssertLessThan(input.low, Decimal(40), "used prices below the good baseline")
    }

    func test_theActorTakesNoPersistentModel() {
        // Source-inspected because the property is about a *type signature*: a
        // test that calls the actor correctly cannot show that calling it
        // incorrectly is impossible.
        let source = try! String(
            contentsOf: URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .appendingPathComponent("SnapWorth/Services/ListingService.swift"),
            encoding: .utf8)
        guard let actorStart = source.range(of: "actor ListingAPIClient") else {
            return XCTFail("could not locate the actor")
        }
        // Bound the region to this actor. Reading to end-of-file swept in
        // `TrendsAPIClient` and everything after it, so an unrelated type
        // could have failed this, or hidden a real hit behind a rename.
        let afterStart = String(source[actorStart.upperBound...])
        let topLevel = ["struct ", "actor ", "enum ", "final class ",
                        "class ", "extension "]
        let body = afterStart
            .split(separator: "\n", omittingEmptySubsequences: false)
            .prefix { line in !topLevel.contains { line.hasPrefix($0) } }
            .joined(separator: "\n")

        // And strip comments, because the property is about what the *code*
        // names. The first version of this test failed on the actor's own doc
        // comment — the sentence explaining that it deliberately does not take
        // a `ScanResult` — which is documentation working exactly as intended.
        // (A `//` inside a string literal would truncate that line early; no
        // literal in this actor contains one, and the cost would be a missed
        // hit rather than a false alarm.)
        let code = body
            .split(separator: "\n", omittingEmptySubsequences: false)
            .map { line -> String in
                guard let marker = line.range(of: "//") else { return String(line) }
                return String(line[line.startIndex..<marker.lowerBound])
            }
            .joined(separator: "\n")

        // The bounding and the stripping are both capable of emptying the
        // haystack, which would make the assertion below pass for the wrong
        // reason. Prove there is still an actor in there.
        XCTAssertTrue(code.contains("func generate("),
                      "the actor body was lost to bounding or comment-stripping")
        XCTAssertTrue(code.contains("ListingInput"),
                      "the actor should still name the Sendable input type")

        XCTAssertFalse(code.contains("ScanResult"),
                       "a ScanResult reaching this actor is an unsynchronised "
                       + "read on the main context")
    }

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

    func test_snapCurrencyCents_keepsTheCents() {
        // Thrift Flip prints an itemised subtraction, and every row went
        // through the 0-decimal formatter — so the rows did not add up to the
        // total beneath them, and any profit under a dollar read "$0" under a
        // green "Worth flipping".
        XCTAssertEqual(NumberFormatter.snapCurrencyCents.string(from: 20.75), "$20.75")
        XCTAssertEqual(NumberFormatter.snapCurrencyCents.string(from: 3.149375), "$3.15")
        XCTAssertEqual(NumberFormatter.snapCurrencyCents.string(from: 0.01), "$0.01")
        XCTAssertEqual(NumberFormatter.snapCurrencyCents.string(from: 7), "$7.00",
                       "a whole amount still shows cents, so a column lines up")
    }

    func test_theItemisedRowsReconcileWithTheirTotal() {
        // The failure in one card: eBay, shop price $10.40, resale $20.75.
        // Fees are 20.75 * 0.1325 + 0.40 = 3.149375, net 7.200625. At zero
        // decimals that printed "$21 − $3 − $10" above a net of "$7" — and
        // 21 − 3 − 10 is 8.
        let resale = Decimal(string: "20.75")!
        let fees = Decimal(string: "3.149375")!
        let paid = Decimal(string: "10.40")!
        let net = resale - fees - paid

        let shown = { (d: Decimal) in
            NumberFormatter.snapCurrencyCents.string(from: NSDecimalNumber(decimal: d))
        }
        XCTAssertEqual(shown(resale), "$20.75")
        XCTAssertEqual(shown(fees), "$3.15")
        XCTAssertEqual(shown(paid), "$10.40")
        XCTAssertEqual(shown(net), "$7.20")
        // 20.75 − 3.15 − 10.40 = 7.20 — the rows and the total agree.
        XCTAssertEqual(shown(Decimal(string: "20.75")! - Decimal(string: "3.15")!
                             - Decimal(string: "10.40")!), shown(net))
    }

    func test_aRangeStillHasNoCents() {
        // The 0-decimal formatter stays where it belongs: a valuation range is
        // an estimate, and cents would imply precision it does not have.
        XCTAssertEqual(NumberFormatter.snapCurrency.string(from: 45.99), "$46")
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
        // The launch-argument override is off unless the scheme passes it —
        // which the test host does not — and its name is what the release
        // notes tell the developer to type.
        XCTAssertFalse(Config.mockScans)
        XCTAssertEqual(Config.mockScansLaunchArgument, "-mock-scans")
    }

    func test_productIDs_areNonEmpty() {
        XCTAssertFalse(Config.monthlyProductID.isEmpty)
        XCTAssertFalse(Config.yearlyProductID.isEmpty)
    }

    func test_productIDs_areDistinct() {
        XCTAssertNotEqual(Config.monthlyProductID, Config.yearlyProductID)
    }

    // ── The anonymous identifier has to actually be salted ───────────────

    func test_theTelemetrySaltIsConfiguredAndLongEnough() {
        // `TelemetryDeck.Config(appID:)` defaults `salt` to the empty string,
        // so what shipped was a plain `sha256(IDFV)` — a globally fixed
        // function anyone holding a device's IDFV could compute, while the
        // in-app privacy policy promised "a one-way salted hash". The SDK's own
        // documentation asks for 64 characters.
        XCTAssertEqual(Config.telemetryDeckSalt.count, 64)
        XCTAssertNotEqual(Config.telemetryDeckSalt, Config.telemetryDeckAppID)
    }

    func test_theSaltLooksGeneratedRatherThanTyped() {
        // A placeholder, a repeated phrase or something a person typed would
        // pass a length check and defeat the point. Sixty-four random draws
        // from a 79-character alphabet land far above this bound; any
        // hand-written string of that length lands far below it.
        XCTAssertGreaterThan(Set(Config.telemetryDeckSalt).count, 24,
                             "too few distinct characters to have been generated")
        XCTAssertFalse(Config.telemetryDeckSalt.lowercased().contains("salt"))
        XCTAssertFalse(Config.telemetryDeckSalt.contains(" "))
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
        // the daily allowance, whatever the current limit is.
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

// MARK: - US marketplaces (#54): Poshmark, Mercari, Depop

/// Every number below is hand-calculated from each marketplace's published
/// seller terms (sources in `MarketplaceFees.defaults`). A wrong entry there
/// silently flips a buy/skip verdict, so these pin the exact fee on a sale.
final class USMarketplaceFeeTests: XCTestCase {
    private func fee(_ m: Marketplace) -> MarketplaceFee { MarketplaceFees.fee(for: m)! }

    func test_poshmark_takesTwentyPercentAtFifteenAndAbove() {
        // $50 sale → 20% = $10.00. Paid $10, no shipping (label is prepaid).
        let c = FlipMath.calculate(resalePrice: 50, purchasePrice: 10, shippingCost: 0, fee: fee(.poshmark))
        XCTAssertEqual(c.platformFees, 10)
        XCTAssertEqual(c.netProfit, 30)
        // $15 exactly is not "under $15": 20% applies → $3.00.
        XCTAssertEqual(fee(.poshmark).fees(on: 15), 3)
    }

    func test_poshmark_flatFeeUnderFifteen() {
        // $12 sale → flat $2.95, not 20% ($2.40). The flat fee is worse for
        // the seller on cheap items, which is exactly what the verdict must see.
        let c = FlipMath.calculate(resalePrice: 12, purchasePrice: 5, shippingCost: 0, fee: fee(.poshmark))
        XCTAssertEqual(c.platformFees, Decimal(string: "2.95")!)
        XCTAssertEqual(c.netProfit, Decimal(string: "4.05")!)
    }

    func test_mercari_flatTenPercent() {
        // $50 sale → 10% = $5.00, no processing fee since Jan 2025.
        let c = FlipMath.calculate(resalePrice: 50, purchasePrice: 10, shippingCost: 5, fee: fee(.mercari))
        XCTAssertEqual(c.platformFees, 5)
        XCTAssertEqual(c.netProfit, 30)
    }

    func test_depop_processingOnly() {
        // $50 sale → 3.3% + $0.45 = $1.65 + $0.45 = $2.10 (Depop's own example).
        let c = FlipMath.calculate(resalePrice: 50, purchasePrice: 10, shippingCost: 0, fee: fee(.depop))
        XCTAssertEqual(c.platformFees, Decimal(string: "2.10")!)
        XCTAssertEqual(c.netProfit, Decimal(string: "37.90")!)
    }

    func test_lowPriceRule_isOffByDefault() {
        // Marketplaces without a flat low-price charge are unaffected.
        let plain = MarketplaceFee(sellingFeePercent: Decimal(string: "0.10")!, fixedFee: 1)
        XCTAssertNil(plain.lowPriceFlatFee)
        XCTAssertEqual(plain.fees(on: 5), Decimal(string: "1.50")!)
    }

    func test_verdictFlipsWithTheRightFee() {
        // The same cheap flip is a buy on one marketplace and a skip on another.
        // $12 sale, paid $9, $0.10 shipping:
        //   Mercari  10%   → fee $1.20 → net  +$1.70  (buy)
        //   Poshmark flat  → fee $2.95 → net  −$0.05  (skip)
        // Using a percentage for Poshmark here (20% = $2.40 → +$0.50) would
        // call this a buy. That is the wrong-verdict failure #54 warns about.
        let mercari = FlipMath.calculate(resalePrice: 12, purchasePrice: 9, shippingCost: Decimal(string: "0.10")!, fee: fee(.mercari))
        let poshmark = FlipMath.calculate(resalePrice: 12, purchasePrice: 9, shippingCost: Decimal(string: "0.10")!, fee: fee(.poshmark))
        XCTAssertTrue(mercari.isProfitable)
        XCTAssertFalse(poshmark.isProfitable)
    }
}

/// The runtime override table, which is how fees get corrected between app
/// releases. It had two money bugs, both silent.
final class MarketplaceFeeOverrideTests: XCTestCase {
    private func install(_ json: String) {
        UserDefaults.standard.set(Data(json.utf8),
                                  forKey: MarketplaceFees.overrideKey)
    }

    override func tearDown() {
        UserDefaults.standard.removeObject(forKey: MarketplaceFees.overrideKey)
        super.tearDown()
    }

    func test_anOverrideKeepsTheLowPriceRuleItDidNotMention() {
        // The bug. `overrideTable` rebuilt each entry with only
        // (sellingFeePercent:fixedFee:), so `lowPriceFlatFee` fell back to nil
        // — and `fee(for:)` prefers an override outright. A push correcting
        // Poshmark's percentage therefore deleted the "$2.95 under $15" rule,
        // and a $10 sale was charged 20% ($2.00) instead of $2.95. The old wire
        // shape had no way to express the rule at all, so a push could not have
        // preserved it even deliberately.
        install(#"{"poshmark": {"pct": "0.22", "fixed": "0"}}"#)
        let poshmark = MarketplaceFees.fee(for: .poshmark)!
        XCTAssertEqual(poshmark.sellingFeePercent, Decimal(string: "0.22")!)
        XCTAssertEqual(poshmark.lowPriceFlatFee?.below, 15)
        XCTAssertEqual(poshmark.fees(on: 10), Decimal(string: "2.95")!,
                       "a cheap sale must still pay the flat charge")
        XCTAssertEqual(poshmark.fees(on: 20), Decimal(string: "4.40")!,
                       "and the new percentage applies above the threshold")
    }

    func test_ratesFromJSONNumbersAreExact() {
        // `Decimal(pct)` from a Double gave
        // 0.132500000000000006661338147750939242541790008544921875 — exactly
        // what the comment on `defaults` says not to do.
        install(#"{"ebay": {"pct": 0.1325, "fixed": 0.40}}"#)
        let ebay = MarketplaceFees.fee(for: .ebay)!
        XCTAssertEqual(ebay.sellingFeePercent, Decimal(string: "0.1325")!)
        XCTAssertEqual(ebay.fixedFee, Decimal(string: "0.40")!)
        XCTAssertNotEqual(ebay.sellingFeePercent, Decimal(0.1325),
                          "Decimal(Double) is the error being avoided")
        // On a $100 sale the difference is sub-cent, but it compounds through
        // every margin and ROI figure derived from it.
        XCTAssertEqual(ebay.fees(on: 100), Decimal(string: "13.65")!)
    }

    func test_ratesFromJSONStringsAreExact() {
        install(#"{"ebay": {"pct": "0.1325", "fixed": "0.40"}}"#)
        XCTAssertEqual(MarketplaceFees.fee(for: .ebay)!.fees(on: 100),
                       Decimal(string: "13.65")!)
    }

    func test_aFlatRuleCanBeAddedToAMarketplaceThatHadNone() {
        install(#"{"mercari": {"flatBelow": "5", "flatFee": "1"}}"#)
        let mercari = MarketplaceFees.fee(for: .mercari)!
        XCTAssertEqual(mercari.sellingFeePercent, Decimal(string: "0.10")!,
                       "an entry naming only the flat rule keeps the default rate")
        XCTAssertEqual(mercari.fees(on: 4), 1)
        XCTAssertEqual(mercari.fees(on: 50), 5)
    }

    func test_aFlatRuleCanBeRemovedOnPurpose() {
        install(#"{"poshmark": {"flatBelow": 0}}"#)
        let poshmark = MarketplaceFees.fee(for: .poshmark)!
        XCTAssertNil(poshmark.lowPriceFlatFee)
        XCTAssertEqual(poshmark.fees(on: 10), 2, "20% of $10")
    }

    func test_aFlatFeeAtOrAboveItsThresholdIsRefused() {
        // Every sale under $15 would net the seller nothing or less. That is a
        // bad push, not a fee — fall back to the shipped default.
        install(#"{"poshmark": {"flatBelow": 15, "flatFee": 20}}"#)
        XCTAssertEqual(MarketplaceFees.fee(for: .poshmark)!.lowPriceFlatFee?.fee,
                       Decimal(string: "2.95")!)
    }

    func test_outOfRangeAndMalformedEntriesFallBackToTheDefault() {
        let ebayDefault = MarketplaceFees.defaults[.ebay]!
        for bad in [#"{"ebay": {"pct": "1.0", "fixed": "0"}}"#,
                    #"{"ebay": {"pct": "-0.1", "fixed": "0"}}"#,
                    #"{"ebay": {"pct": "0.1", "fixed": "-1"}}"#,
                    #"{"ebay": {"pct": "abc", "fixed": "0"}}"#,
                    #"{"ebay": {"pct": true, "fixed": "0"}}"#,
                    #"{"etsy": {"pct": "0.1", "fixed": "0"}}"#,
                    #"not json at all"#] {
            install(bad)
            XCTAssertEqual(MarketplaceFees.fee(for: .ebay), ebayDefault,
                           "a bad push must never reach the math: \(bad)")
        }
    }

    func test_rejectionGranularityIsExplicit() {
        // Two different granularities, both safe, and chosen rather than
        // stumbled into:
        //
        //   * a value of the wrong *type* fails `JSONDecoder`, so the whole
        //     push is discarded — you never apply half a fee table;
        //   * a value of the right type but out of *range* discards only that
        //     entry, so one bad marketplace does not cost the others.
        //
        // Asserted because the difference is invisible in a single-entry test
        // and would otherwise be a surprise to whoever writes the push.
        install(#"{"ebay": {"pct": "0.10", "fixed": "0"}, "mercari": {"pct": true}}"#)
        XCTAssertEqual(MarketplaceFees.fee(for: .ebay), MarketplaceFees.defaults[.ebay],
                       "a type error anywhere discards the whole push")

        install(#"{"ebay": {"pct": "0.10", "fixed": "0"}, "mercari": {"pct": "5"}}"#)
        XCTAssertEqual(MarketplaceFees.fee(for: .ebay)!.sellingFeePercent,
                       Decimal(string: "0.10")!,
                       "an out-of-range entry elsewhere does not cost this one")
        XCTAssertEqual(MarketplaceFees.fee(for: .mercari), MarketplaceFees.defaults[.mercari])
    }

    func test_theWireShapeMatchesTheWebsiteMirror() {
        // The website carries the same table with `flatBelow`/`flatFee`
        // (website/index.html, the FEES object). One remote push has to be able
        // to feed both, so the key names are part of the contract.
        install(#"""
        {"poshmark": {"pct": 0.2, "fixed": 0, "flatBelow": 15, "flatFee": 2.95}}
        """#)
        let poshmark = MarketplaceFees.fee(for: .poshmark)!
        XCTAssertEqual(poshmark.sellingFeePercent, Decimal(string: "0.2")!)
        XCTAssertEqual(poshmark.lowPriceFlatFee?.below, 15)
        XCTAssertEqual(poshmark.lowPriceFlatFee?.fee, Decimal(string: "2.95")!)
    }

    func test_noOverrideLeavesTheShippedTableAlone() {
        UserDefaults.standard.removeObject(forKey: MarketplaceFees.overrideKey)
        XCTAssertTrue(MarketplaceFees.overrideTable.isEmpty)
        for marketplace in Marketplace.allCases {
            XCTAssertEqual(MarketplaceFees.fee(for: marketplace),
                           MarketplaceFees.defaults[marketplace])
        }
    }
}

final class USMarketplaceWiringTests: XCTestCase {
    func test_apiValuesMatchTheBackendKeys() {
        XCTAssertEqual(Marketplace.poshmark.apiValue, "poshmark")
        XCTAssertEqual(Marketplace.mercari.apiValue, "mercari")
        XCTAssertEqual(Marketplace.depop.apiValue, "depop")
    }

    func test_usPlatformsLeadTheChipOrder() {
        XCTAssertEqual(Array(Marketplace.allCases.prefix(4)), [.ebay, .poshmark, .mercari, .depop])
        // Named rather than counted. The count alone said "kept, not replaced"
        // and could not tell a removal from an addition — it failed on Xianyu,
        // which was neither. This says what it meant.
        XCTAssertTrue(Set(Marketplace.allCases).isSuperset(
            of: [.ebay, .poshmark, .mercari, .depop, .facebook, .vinted, .olx]),
            "existing marketplaces are kept, not replaced")
        XCTAssertEqual(Marketplace.allCases.count, 9,
                       "a new marketplace needs a fee entry, a sell URL and backend guidance")
    }

    /// Xianyu is the one marketplace whose listing is not in English, and the
    /// one whose absence made a Chinese app pointless.
    func test_xianyuIsWiredLikeTheRest() {
        XCTAssertEqual(Marketplace.xianyu.apiValue, "xianyu")
        XCTAssertEqual(Marketplace.xianyu.displayName, "闲鱼")
        XCTAssertEqual(Marketplace.xianyu.webSellURL.scheme, "https")
        XCTAssertNil(Marketplace.xianyu.appURLScheme,
                     "no public scheme could be cited, so none is claimed")
        XCTAssertNotNil(MarketplaceFees.fee(for: .xianyu))
        XCTAssertEqual(Marketplace.xianyu.iconName, "fish.fill")
    }

    /// Kleinanzeigen is the second single-country marketplace, and the second
    /// whose listing is written in its own language rather than English.
    func test_kleinanzeigenIsWiredLikeTheRest() {
        XCTAssertEqual(Marketplace.kleinanzeigen.apiValue, "kleinanzeigen")
        XCTAssertEqual(Marketplace.kleinanzeigen.displayName, "Kleinanzeigen")
        XCTAssertEqual(Marketplace.kleinanzeigen.webSellURL.scheme, "https")
        XCTAssertNil(Marketplace.kleinanzeigen.appURLScheme,
                     "no public scheme could be cited, so none is claimed")
        XCTAssertEqual(MarketplaceFees.fee(for: .kleinanzeigen)?.sellingFeePercent, 0,
                       "a private ad on Kleinanzeigen pays no commission")
        // Last in the picker: the chip order is US-first by user share.
        XCTAssertEqual(Marketplace.allCases.last, .kleinanzeigen)
    }

    /// A Kleinanzeigen ad is read by German buyers, so the grade is German
    /// whatever language the seller's phone is in.
    func test_kleinanzeigenConditionPhrasesAreGerman() {
        for condition in Condition.allCases {
            let phrase = condition.kleinanzeigenPhrase
            XCTAssertFalse(phrase.isEmpty)
            XCTAssertNotEqual(phrase, condition.listingPhrase)
            XCTAssertNotEqual(phrase, condition.xianyuPhrase)
        }
        XCTAssertEqual(Condition.new.kleinanzeigenPhrase, "neu und unbenutzt")
    }

    /// The grade in a Xianyu listing is Chinese whatever the phone's language,
    /// because the listing is read by Chinese buyers and not by its author.
    func test_xianyuConditionPhrasesAreChinese() {
        for condition in Condition.allCases {
            let phrase = condition.xianyuPhrase
            XCTAssertFalse(phrase.isEmpty)
            XCTAssertTrue(phrase.unicodeScalars.contains { $0.value >= 0x4E00 && $0.value <= 0x9FFF },
                          "\(condition) should read as Chinese, got \(phrase)")
            XCTAssertNotEqual(phrase, condition.listingPhrase)
        }
    }

    func test_noFabricatedURLSchemes() {
        // None of the three publish a scheme; universal links do the job.
        XCTAssertNil(Marketplace.poshmark.appURLScheme)
        XCTAssertNil(Marketplace.mercari.appURLScheme)
        XCTAssertNil(Marketplace.depop.appURLScheme)
        for m in [Marketplace.poshmark, .mercari, .depop] {
            XCTAssertEqual(m.webSellURL.scheme, "https")
        }
    }

    func test_displayNamesArePlainText() {
        // Acceptance criterion on #54: names only, no logos or brand marks.
        XCTAssertEqual(Marketplace.poshmark.displayName, "Poshmark")
        XCTAssertEqual(Marketplace.mercari.displayName, "Mercari")
        XCTAssertEqual(Marketplace.depop.displayName, "Depop")
    }
}

// MARK: - Widget bridge
//
// The widget extension routinely reads a blob written by an *older* build of
// the app: an update installs the new extension, and the app may not run for
// days. Swift's synthesised `Codable` initialiser throws `keyNotFound` for a
// missing key — it does not fall back to a property's default — so adding a
// field to `WidgetHaulData` without a hand-written decoder would blank every
// installed Home Screen widget until the owner's next scan.

final class WidgetHaulDataTests: XCTestCase {

    /// Exactly the six keys 1.3.x wrote. Hardcoded rather than generated, so
    /// it keeps describing the old format even as the struct grows.
    private let v1 = """
        {"totalLow":348,"totalHigh":620,"itemCount":8,
         "lastItemName":"Patagonia Fleece","lastItemRange":"$60–$95",
         "updatedAt":768000000}
        """.data(using: .utf8)!

    func test_aBlobFromTheOldAppStillDecodes() throws {
        let haul = try JSONDecoder().decode(WidgetHaulData.self, from: v1)
        XCTAssertEqual(haul.itemCount, 8)
        XCTAssertEqual(haul.totalLow, 348)
        XCTAssertEqual(haul.lastItemName, "Patagonia Fleece")
    }

    func test_fieldsTheOldAppNeverWroteAreSafeDefaults() throws {
        let haul = try JSONDecoder().decode(WidgetHaulData.self, from: v1)
        XCTAssertNil(haul.freeScansRemaining)
        XCTAssertFalse(haul.isPro, "must not read an absent key as Pro")
        XCTAssertEqual(haul.streak, 0)
        XCTAssertEqual(haul.recentFinds, [])
        XCTAssertNil(haul.monthProfit)
        XCTAssertEqual(haul.monthFlips, 0)
        XCTAssertNil(haul.totalLikely,
                     "1.3.x never wrote a midpoint and none can be derived " +
                     "from a low and a high")
    }

    // ── The one number, when there is room for one number ────────────────────

    func test_theSingleFigureIsTheMiddleOfTheRangeNotItsTop() {
        // Three items at $100-$200: the app's portfolio banner says $450 and
        // the circular complication used to say $600.
        let haul = WidgetHaulData(
            totalLow: 300, totalHigh: 600, itemCount: 3,
            lastItemName: "", lastItemRange: "", updatedAt: .now,
            freeScansRemaining: nil, isPro: false, streak: 0,
            recentFinds: [], monthProfit: nil, monthFlips: 0,
            totalLikely: 450)
        XCTAssertEqual(haul.compactTotal, WidgetHaulData.compactMoney(450))
        XCTAssertNotEqual(haul.compactTotal, WidgetHaulData.compactMoney(600))
    }

    func test_aBlobWithoutAMidpointStillShowsSomething() throws {
        // The extension updates before the app next runs, so this is the
        // ordinary state for a while after an update — not a corner case.
        let haul = try JSONDecoder().decode(WidgetHaulData.self, from: v1)
        XCTAssertEqual(haul.compactTotal, WidgetHaulData.compactMoney(620),
                       "the old behaviour, until the app writes a midpoint")
    }

    func test_theMidpointIsBracketedByTheRangeItIsTheMiddleOf() {
        // `totalLikely` is summed over the same items as `totalLow` and
        // `totalHigh`, so this holds for every blob the app writes.
        let haul = WidgetHaulData(
            totalLow: 300, totalHigh: 600, itemCount: 3,
            lastItemName: "", lastItemRange: "", updatedAt: .now,
            freeScansRemaining: nil, isPro: false, streak: 0,
            recentFinds: [], monthProfit: nil, monthFlips: 0,
            totalLikely: 450)
        let likely = try? XCTUnwrap(haul.totalLikely)
        XCTAssertNotNil(likely)
        XCTAssertGreaterThanOrEqual(likely ?? 0, haul.totalLow)
        XCTAssertLessThanOrEqual(likely ?? 0, haul.totalHigh)
    }

    func test_aTruncatedBlobDegradesRatherThanThrowing() throws {
        // A half-written blob should render an empty widget, not no widget.
        let partial = #"{"itemCount":3}"#.data(using: .utf8)!
        let haul = try JSONDecoder().decode(WidgetHaulData.self, from: partial)
        XCTAssertEqual(haul.itemCount, 3)
        XCTAssertEqual(haul.totalLow, 0)
        XCTAssertEqual(haul.updatedAt, .distantPast)
    }

    func test_everyFieldSurvivesARoundTrip() throws {
        let original = WidgetHaulData(
            totalLow: 120, totalHigh: 260, itemCount: 4,
            lastItemName: "Levi's 501", lastItemRange: "$40–$70",
            updatedAt: Date(timeIntervalSince1970: 768_000_000),
            freeScansRemaining: 2, isPro: true, streak: 9,
            recentFinds: [WidgetFind(id: "a", name: "Levi's 501", range: "$40–$70")],
            monthProfit: 214.5, monthFlips: 6,
            // The name says every field, so it has to mean it: a field left
            // out of `CodingKeys` round-trips as its default and this is the
            // only place that would notice.
            streakLastScan: Date(timeIntervalSince1970: 767_000_000),
            freeScanAllowance: 3, monthSold: 8, totalLikely: 190)
        let data = try JSONEncoder().encode(original)
        XCTAssertEqual(try JSONDecoder().decode(WidgetHaulData.self, from: data),
                       original)
    }

    func test_anEmptyHaulReadsTheSameAsTheApp() {
        // The drift that shipped: the widget's copy had no empty-haul guard,
        // so a library with no scans read "$0–$0" there and "$0" in the app.
        XCTAssertEqual(WidgetHaulData.empty.formattedRange, "$0")
        XCTAssertFalse(WidgetHaulData.empty.hasScans)
    }

    func test_aRangeIsFormattedInTheAppsOwnCurrencyStyle() {
        let haul = WidgetHaulData(
            totalLow: 348, totalHigh: 620, itemCount: 8,
            lastItemName: "", lastItemRange: "", updatedAt: .now,
            freeScansRemaining: nil, isPro: false, streak: 0,
            recentFinds: [], monthProfit: nil, monthFlips: 0)
        XCTAssertEqual(haul.formattedRange, "$348–$620")
    }

    func test_theHaulRangeIsPunctuatedLikeAnItemRange() {
        // They render a few points apart in the medium widget: the haul total
        // on the left, `lastItemRange` on the right. The widget's own copy was
        // spaced and the app's was not, so one card showed "$348 – $620" and
        // "$60–$95" side by side.
        let item = ScanResult(itemName: "Better Sweater", brand: "Patagonia",
                              category: "clothing", conditionNotes: "Solid",
                              valueLow: 60, valueHigh: 95, confidence: "High",
                              soldListingsCount: 0,
                              listingTitle: "T", listingDescription: "D")
        let haul = WidgetHaulData(
            totalLow: 348, totalHigh: 620, itemCount: 8,
            lastItemName: item.itemName, lastItemRange: item.formattedRange,
            updatedAt: .now, freeScansRemaining: nil, isPro: false, streak: 0,
            recentFinds: [], monthProfit: nil, monthFlips: 0)

        XCTAssertFalse(haul.formattedRange.contains(" – "),
                       "the haul total is punctuated differently from the item beside it")
        XCTAssertFalse(item.formattedRange.contains(" – "))
        XCTAssertTrue(haul.formattedRange.contains("–"))
        XCTAssertTrue(item.formattedRange.contains("–"))
    }

    func test_aThriftRunRangeIsPunctuatedTheSameWay() {
        let state = ThriftRunAttributes.ContentState(
            itemCount: 3, totalLow: 95, totalHigh: 150, lastItemName: "Levi's 501")
        XCTAssertEqual(state.formattedRange, "$95–$150")
    }
}

// MARK: - Lock Screen formatting
//
// A circular accessory is about 72 points across, so the total is abbreviated.
// The abbreviation is where this goes wrong: deciding the format from the raw
// value made 9,999 read "$10.0K" while 10,000 read "$10K" — the same number,
// spelled two ways, one dollar apart — and put 999.6 in the sub-thousand
// branch, where it printed the "$1000" the abbreviation exists to avoid.

final class LockScreenMoneyTests: XCTestCase {

    func test_smallAmountsAreNotAbbreviated() {
        XCTAssertEqual(WidgetHaulData.compactMoney(0), "$0")
        XCTAssertEqual(WidgetHaulData.compactMoney(348), "$348")
        XCTAssertEqual(WidgetHaulData.compactMoney(999), "$999")
    }

    func test_theThousandBoundaryIsDecidedAfterRounding() {
        // 999.6 rounds to 1,000 and must be spelled as thousands.
        XCTAssertEqual(WidgetHaulData.compactMoney(999.6), "$1.0K")
        XCTAssertEqual(WidgetHaulData.compactMoney(1_000), "$1.0K")
        XCTAssertEqual(WidgetHaulData.compactMoney(1_240), "$1.2K")
    }

    func test_theTenThousandBoundaryAgreesWithItself() {
        // The bug: one dollar apart, two spellings.
        XCTAssertEqual(WidgetHaulData.compactMoney(9_999),
                       WidgetHaulData.compactMoney(10_000))
        XCTAssertEqual(WidgetHaulData.compactMoney(9_999), "$10K")
        XCTAssertEqual(WidgetHaulData.compactMoney(9_949), "$9.9K")
    }

    func test_largeAmountsDropTheDecimal() {
        XCTAssertEqual(WidgetHaulData.compactMoney(12_400), "$12K")
        XCTAssertEqual(WidgetHaulData.compactMoney(250_000), "$250K")
    }

    // ── Losses ───────────────────────────────────────────────────────────────
    // The month's profit is the one consumer that can be negative, and the
    // sign was being interpolated straight after the "$": "$-420", "$-1.2K".
    // The Flips screen spells the same figure "−$420".

    func test_aLossPutsTheSignBeforeTheDollar() {
        XCTAssertEqual(WidgetHaulData.compactMoney(-420), "−$420")
        XCTAssertEqual(WidgetHaulData.compactMoney(-1_240), "−$1.2K")
        XCTAssertEqual(WidgetHaulData.compactMoney(-12_400), "−$12K")
    }

    func test_noAmountEverPutsTheSignInsideTheAmount() {
        for value in stride(from: -300_000.0, through: 300_000, by: 617) {
            XCTAssertFalse(WidgetHaulData.compactMoney(value).contains("$-"),
                           "\(value) put the sign inside the amount")
        }
    }

    func test_aLossIsSpelledLikeTheFlipsScreenSpellsIt() {
        // Not a literal check of the other surface, but of the convention it
        // sets: U+2212, outside the "$". A hyphen-minus here would read as a
        // different app.
        XCTAssertTrue(WidgetHaulData.compactMoney(-420).hasPrefix("\u{2212}"))
        XCTAssertFalse(WidgetHaulData.compactMoney(-420).contains("-"))
    }

    func test_theNegativeBoundariesAgreeWithThePositiveOnes() {
        XCTAssertEqual(WidgetHaulData.compactMoney(-9_999),
                       WidgetHaulData.compactMoney(-10_000))
        XCTAssertEqual(WidgetHaulData.compactMoney(-999.6), "−$1.0K")
    }

    func test_aLossTooSmallToShowIsNotSignedZero() {
        // −0.4 rounds to zero; "−$0" would be a claim about a loss that isn't.
        XCTAssertEqual(WidgetHaulData.compactMoney(-0.4), "$0")
        XCTAssertEqual(WidgetHaulData.compactMoney(-0.6), "−$1")
    }

    func test_nothingEverRendersAThousandsSeparator() {
        // The whole reason this exists: "$1,240" does not fit in a circular
        // complication at a legible size.
        for value in stride(from: 0.0, through: 300_000, by: 617) {
            XCTAssertFalse(WidgetHaulData.compactMoney(value).contains(","),
                           "\(value) rendered a separator")
        }
    }

    func test_itemsLabelIsSingularForOne() {
        // "1 items in your haul" was the Quick Scan widget's greeting to a
        // user who had just completed their first scan.
        XCTAssertEqual(WidgetHaulData.itemsLabel(1), "1 item")
        XCTAssertEqual(WidgetHaulData.itemsLabel(0), "0 items")
        XCTAssertEqual(WidgetHaulData.itemsLabel(8), "8 items")
    }

    func test_findsLabelIsSingularForOne() {
        func haul(_ count: Int) -> WidgetHaulData {
            WidgetHaulData(totalLow: 0, totalHigh: 0, itemCount: count,
                           lastItemName: "", lastItemRange: "", updatedAt: .now,
                           freeScansRemaining: nil, isPro: false, streak: 0,
                           recentFinds: [], monthProfit: nil, monthFlips: 0)
        }
        XCTAssertEqual(haul(1).findsLabel, "1 find")
        XCTAssertEqual(haul(8).findsLabel, "8 finds")
        XCTAssertEqual(haul(0).findsLabel, "0 finds")
    }
}

// MARK: - Support mail

/// The feedback screen's only job is to hand a message to Mail intact. These
/// pin the two ways it used to fail: a mangled body, and a missing address.
final class SupportMailTests: XCTestCase {

    /// The regression that started this. `URLComponents.queryItems` leaves `+`
    /// alone, and a mail client decodes a bare `+` in a query as a space — so
    /// this exact message used to arrive with its plus signs replaced.
    func test_plusInBodyIsPercentEncoded() throws {
        let url = try XCTUnwrap(SupportMail.composeURL(subject: "S", body: "iOS 26 + widgets"))
        XCTAssertTrue(url.absoluteString.contains("%2B"),
                      "a literal + must survive as %2B, not as a space")
        XCTAssertFalse(url.absoluteString.contains("+ widgets"))
    }

    /// `&` and `=` would otherwise end the body and invent new query fields,
    /// silently truncating everything the user wrote after them.
    func test_queryDelimitersInBodyAreEncoded() throws {
        let url = try XCTUnwrap(SupportMail.composeURL(subject: "S", body: "a&b=c?d#e"))
        let string = url.absoluteString
        for raw in ["a&b", "b=c", "c?d", "d#e"] {
            XCTAssertFalse(string.contains(raw), "\(raw) must not survive raw")
        }
        // Exactly one body field: an unescaped & would have invented more.
        XCTAssertEqual(string.components(separatedBy: "&body=").count, 2)
    }

    /// Decoding the body back out must return exactly what was typed —
    /// newlines, punctuation and all.
    func test_bodyRoundTripsExactly() throws {
        let original = "Line one.\nLine two: 100% + tax — “quoted” & done?"
        let url = try XCTUnwrap(SupportMail.composeURL(subject: "S", body: original))
        let components = try XCTUnwrap(URLComponents(url: url, resolvingAgainstBaseURL: false))
        let body = components.queryItems?.first { $0.name == "body" }?.value
        XCTAssertEqual(body, original)
    }

    func test_addressAndSubjectComeFromConfig() throws {
        let url = try XCTUnwrap(SupportMail.composeURL(subject: "SnapWorth Bug Report", body: "hello"))
        XCTAssertEqual(url.scheme, "mailto")
        XCTAssertTrue(url.absoluteString.hasPrefix("mailto:\(Config.supportEmail)?"))
        let components = try XCTUnwrap(URLComponents(url: url, resolvingAgainstBaseURL: false))
        XCTAssertEqual(components.queryItems?.first { $0.name == "subject" }?.value,
                       "SnapWorth Bug Report")
    }

    /// An empty message is the caller's business, not a reason to hand back a
    /// nil URL — the screen already refuses to send under ten characters.
    func test_emptyBodyStillProducesAURL() {
        XCTAssertNotNil(SupportMail.composeURL(subject: "S", body: ""))
    }

    /// Diagnostics exist so the first reply to a bug report isn't "what
    /// version are you on?".
    func test_diagnosticsCarryVersionAndOS() {
        let text = SupportMail.diagnostics
        XCTAssertTrue(text.contains("SnapWorth"), "app name and version")
        XCTAssertTrue(text.contains("iOS") || text.contains("iPadOS"),
                      "OS name from UIDevice.systemName")
    }

    /// The block rides in an email the user can read, so the only identifier
    /// in it may be the server's own pseudonym.
    func test_diagnosticsCarryNoDeviceIdentifiers() {
        let text = SupportMail.diagnostics
        XCTAssertFalse(text.contains("@"), "no address, no account, no email")
        XCTAssertFalse(text.lowercased().contains("udid"))
        if let vendor = UIDevice.current.identifierForVendor?.uuidString {
            XCTAssertFalse(text.contains(vendor), "the vendor id must never ride along")
        }
    }

    /// `/user <id>` on Telegram is documented as being for answering a
    /// support email, and the email used to carry no id at all.
    func test_diagnosticsQuoteTheSupportIDWhenThereIsOne() {
        let saved = SupportMail.supportID
        defer { SupportMail.supportID = saved }

        SupportMail.supportID = "a1b2c3d4e5f60718"
        XCTAssertTrue(SupportMail.diagnostics.contains("a1b2c3d4e5f60718"))
    }

    /// A build talking to a backend that predates `support_id` has none, and
    /// must still produce a usable block rather than an empty line or "nil".
    func test_diagnosticsOmitTheLineWhenThereIsNoSupportID() {
        let saved = SupportMail.supportID
        defer { SupportMail.supportID = saved }

        SupportMail.supportID = nil
        let text = SupportMail.diagnostics
        XCTAssertFalse(text.contains("Device "))
        XCTAssertFalse(text.contains("nil"))
        XCTAssertFalse(text.hasSuffix("\n"))
        XCTAssertTrue(text.contains("SnapWorth"), "the rest of the block survives")
    }

    /// Setting it to empty is how a server that sent `""` would land, and
    /// must read back as absent rather than as a blank id.
    func test_emptySupportIDIsStoredAsAbsent() {
        let saved = SupportMail.supportID
        defer { SupportMail.supportID = saved }

        SupportMail.supportID = "abc"
        SupportMail.supportID = ""
        XCTAssertNil(SupportMail.supportID)
    }

    /// The id ends up in the clipboard fallback too, because that path has to
    /// carry exactly what the email would have.
    func test_supportIDSurvivesIntoAComposedURL() throws {
        let saved = SupportMail.supportID
        defer { SupportMail.supportID = saved }

        SupportMail.supportID = "deadbeefdeadbeef"
        let url = try XCTUnwrap(SupportMail.composeURL(
            subject: "S", body: "it broke\n\n" + SupportMail.diagnostics))
        let components = try XCTUnwrap(URLComponents(url: url, resolvingAgainstBaseURL: false))
        let body = try XCTUnwrap(components.queryItems?.first { $0.name == "body" }?.value)
        XCTAssertTrue(body.contains("deadbeefdeadbeef"))
        XCTAssertTrue(body.hasPrefix("it broke"))
    }

    /// The address is typed once. This is what would have caught the 1.3.4
    /// drift between the app's inbox and the website's.
    func test_supportEmailIsAPlausibleAddress() {
        let address = Config.supportEmail
        XCTAssertFalse(address.isEmpty)
        XCTAssertEqual(address.components(separatedBy: "@").count, 2)
        XCTAssertTrue(address.contains("."))
        XCTAssertFalse(address.contains(" "))
    }
}

// ── Scans left: nil is not zero ───────────────────────────────────────────────
//
// Every read of `freeScansRemaining` in the widget was `?? 0`, and nil on a
// non-Pro blob means *never established* — no blob in the App Group, a decode
// failure, or a v1 blob from an install not reopened since the update. The
// zero branch is the alarming one: a large terracotta "0" captioned "Back
// tomorrow, or go Pro", shown to someone whose whole allowance is untouched.
// Adding the widget from the gallery before first launch did exactly that.

final class WidgetScansLeftTests: XCTestCase {

    private func haul(isPro: Bool = false,
                      remaining: Int? = nil,
                      streak: Int = 0) -> WidgetHaulData {
        WidgetHaulData(totalLow: 0, totalHigh: 0, itemCount: 0,
                       lastItemName: "", lastItemRange: "", updatedAt: .now,
                       freeScansRemaining: remaining, isPro: isPro, streak: streak,
                       recentFinds: [], monthProfit: nil, monthFlips: 0)
    }

    func test_anUnwrittenCountIsUnknownRatherThanZero() {
        let state = haul(remaining: nil).scansLeft(at: .now)
        XCTAssertEqual(state, .unknown)
        XCTAssertEqual(state.headline, "—")
        XCTAssertEqual(state.circularValue, "—")
        XCTAssertEqual(state.subtitle, "Open SnapWorth")
    }

    func test_anUnknownCountIsNeverPaintedAsSpent() {
        // `isSpent` drives the terracotta accent. Firing it here tells someone
        // with a full allowance that they are out of scans.
        XCTAssertFalse(haul(remaining: nil).scansLeft(at: .now).isSpent)
    }

    func test_theEmptyBlobIsUnknown() {
        // What `WidgetReader.readHaul()` returns before the app has ever run,
        // and after any decode failure.
        XCTAssertEqual(WidgetHaulData.empty.scansLeft(at: .now), .unknown)
    }

    func test_aBlobFromTheOldAppIsUnknown() throws {
        let v1 = """
            {"totalLow":348,"totalHigh":620,"itemCount":8,
             "lastItemName":"Patagonia Fleece","lastItemRange":"$60–$95",
             "updatedAt":768000000}
            """.data(using: .utf8)!
        let decoded = try JSONDecoder().decode(WidgetHaulData.self, from: v1)
        XCTAssertEqual(decoded.scansLeft(at: .now), .unknown)
    }

    func test_anUnknownCountIsNotSpokenAsZero() {
        XCTAssertEqual(haul(remaining: nil).scansLeft(at: .now).spoken,
                       "Scan count not available yet. Open SnapWorth.")
    }

    func test_aSpentAllowanceStillReadsAsSpent() {
        let state = haul(remaining: 0).scansLeft(at: .now)
        XCTAssertEqual(state, .remaining(0))
        XCTAssertEqual(state.headline, "0")
        XCTAssertEqual(state.subtitle, "Back tomorrow, or go Pro")
        XCTAssertEqual(state.spoken, "No free scans left today")
        XCTAssertTrue(state.isSpent)
    }

    func test_oneScanIsSingular() {
        let state = haul(remaining: 1).scansLeft(at: .now)
        XCTAssertEqual(state.subtitle, "free scan left today")
        XCTAssertEqual(state.spoken, "1 free scan left today")
        XCTAssertFalse(state.isSpent)
    }

    func test_severalScansArePlural() {
        XCTAssertEqual(haul(remaining: 3).scansLeft(at: .now).subtitle, "free scans left today")
    }

    func test_proAnswersUnlimitedAndKeepsTheStreakUnderIt() {
        // The widget is "Scans left" and the big line is its answer. It used
        // to read "5-day streak", so a subscriber with a live streak was never
        // told their scans were unlimited — the only state that said so was
        // the streakless one, which is the one they see least. The streak is
        // still there, in the subtitle.
        let state = haul(isPro: true, remaining: nil, streak: 5).scansLeft(at: .now)
        XCTAssertEqual(state, .pro(streak: 5))
        XCTAssertEqual(state.headline, "∞")
        XCTAssertEqual(state.subtitle, "Unlimited · 5-day streak")
        XCTAssertEqual(state.circularValue, "∞")
        XCTAssertEqual(state.spoken, "Unlimited scans, 5 day scanning streak")
        XCTAssertFalse(state.isSpent, "Pro is never out of scans")
    }

    func test_proWithoutAStreakSaysSoPlainly() {
        let state = haul(isPro: true, streak: 0).scansLeft(at: .now)
        XCTAssertEqual(state.headline, "∞")
        XCTAssertEqual(state.subtitle, "Unlimited scans")
        XCTAssertEqual(state.circularValue, "∞")
        XCTAssertEqual(state.spoken, "Unlimited scans")
    }

    func test_theProHeadlineNeverShowsAFiniteCount() {
        // The complaint this came from: a subscriber's Home Screen showing a
        // number where "unlimited" belongs. Whatever the streak, and whatever
        // stale count the blob still carries, the answer is the same glyph.
        for streak in [0, 1, 2, 30] {
            for remaining in [nil, 0, 3] as [Int?] {
                let state = haul(isPro: true, remaining: remaining,
                                 streak: streak).scansLeft(at: .now)
                XCTAssertEqual(state.headline, "∞", "streak \(streak)")
                XCTAssertEqual(state.circularValue, "∞", "streak \(streak)")
                XCTAssertTrue(state.subtitle.hasPrefix("Unlimited"),
                              "streak \(streak): \(state.subtitle)")
            }
        }
    }

    func test_proWinsOverAStaleCount() {
        // A lapse-and-resubscribe can leave a count in the blob; entitlement
        // decides what the widget says, not the leftover number.
        XCTAssertEqual(haul(isPro: true, remaining: 0, streak: 2).scansLeft(at: .now),
                       .pro(streak: 2))
    }
}

// ── Recent finds: the header and the body must agree ─────────────────────────
//
// The header branched on `hasScans` and printed the haul total; the body
// branched on `recentFinds.isEmpty` and printed "Nothing scanned yet". Those
// disagree for exactly one blob — the v1 one, where the totals decode and
// `recentFinds` defaults to empty — which is what an installed widget reads
// after the update and before the app is next opened.

final class WidgetRecentRowsTests: XCTestCase {

    private let v1 = """
        {"totalLow":348,"totalHigh":620,"itemCount":8,
         "lastItemName":"Patagonia Fleece","lastItemRange":"$60–$95",
         "updatedAt":768000000}
        """.data(using: .utf8)!

    private func haul(itemCount: Int,
                      lastName: String = "",
                      lastRange: String = "",
                      finds: [WidgetFind] = []) -> WidgetHaulData {
        WidgetHaulData(totalLow: 0, totalHigh: 0, itemCount: itemCount,
                       lastItemName: lastName, lastItemRange: lastRange,
                       updatedAt: .now, freeScansRemaining: nil, isPro: false,
                       streak: 0, recentFinds: finds, monthProfit: nil, monthFlips: 0)
    }

    func test_aHaulWithScansAlwaysHasARowToShow() throws {
        // The invariant the two halves of the widget were breaking.
        let decoded = try JSONDecoder().decode(WidgetHaulData.self, from: v1)
        XCTAssertTrue(decoded.hasScans)
        XCTAssertTrue(decoded.recentFinds.isEmpty, "v1 carries no find list")
        XCTAssertFalse(decoded.recentRows(limit: WidgetBridge.maxRecentFinds).isEmpty,
                       "header printed a total while the body said nothing was scanned")
    }

    func test_theV1FallbackRowIsTheFindTheOldBlobDoesCarry() throws {
        let decoded = try JSONDecoder().decode(WidgetHaulData.self, from: v1)
        let rows = decoded.recentRows(limit: 4)
        XCTAssertEqual(rows.count, 1)
        XCTAssertEqual(rows.first?.name, "Patagonia Fleece")
        XCTAssertEqual(rows.first?.range, "$60–$95")
    }

    func test_anEmptyLibraryHasNoRows() {
        XCTAssertTrue(haul(itemCount: 0).recentRows(limit: 4).isEmpty)
        XCTAssertTrue(WidgetHaulData.empty.recentRows(limit: 4).isEmpty)
    }

    func test_aHaulWithScansButNoNameIsNotFakedIntoARow() {
        // Defensive: an unnamed find would render a blank row, which is worse
        // than the empty state.
        XCTAssertTrue(haul(itemCount: 3).recentRows(limit: 4).isEmpty)
    }

    func test_theListIsCappedAtTheFamilysLimit() {
        let finds = (1...6).map { WidgetFind(id: "\($0)", name: "Item \($0)", range: "$1") }
        XCTAssertEqual(haul(itemCount: 6, finds: finds).recentRows(limit: 2).count, 2)
        XCTAssertEqual(haul(itemCount: 6, finds: finds).recentRows(limit: 4).count, 4)
        XCTAssertEqual(haul(itemCount: 6, finds: finds)
                        .recentRows(limit: WidgetBridge.maxRecentFinds).count, 6)
    }

    func test_aRealFindListWinsOverTheFallback() {
        let finds = [WidgetFind(id: "a", name: "Levi's 501", range: "$40–$70")]
        let rows = haul(itemCount: 8, lastName: "Patagonia Fleece",
                        lastRange: "$60–$95", finds: finds).recentRows(limit: 4)
        XCTAssertEqual(rows.map(\.name), ["Levi's 501"])
    }
}

// ── Freshness: three snapshots that outlived the period they described ───────
//
// The free-scan count is scoped to a UTC day, the streak to a local day, the
// month's profit to a local month — and all three were stored as bare numbers
// that the extension cannot recompute: `FreeScanCounter` and `ScanStreak` live
// in `UserDefaults.standard`, not the App Group, and the ledger is in
// SwiftData. The providers emitted one entry dated `.now` with a blind hourly
// policy, so every refresh re-read the same frozen number and the correction
// waited for the app to be launched.

final class WidgetFreshnessTests: XCTestCase {

    private func utc(_ year: Int, _ month: Int, _ day: Int, _ hour: Int = 12) -> Date {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(secondsFromGMT: 0) ?? .gmt
        return cal.date(from: DateComponents(year: year, month: month,
                                             day: day, hour: hour))!
    }

    private func haul(updatedAt: Date,
                      remaining: Int? = nil,
                      allowance: Int? = nil,
                      isPro: Bool = false,
                      streak: Int = 0,
                      streakLastScan: Date? = nil,
                      monthProfit: Double? = nil,
                      monthFlips: Int = 0) -> WidgetHaulData {
        WidgetHaulData(totalLow: 0, totalHigh: 0, itemCount: 1,
                       lastItemName: "Levi's 501", lastItemRange: "$40–$70",
                       updatedAt: updatedAt, freeScansRemaining: remaining,
                       isPro: isPro, streak: streak, recentFinds: [],
                       monthProfit: monthProfit, monthFlips: monthFlips,
                       streakLastScan: streakLastScan, freeScanAllowance: allowance)
    }

    // ── The allowance resets on the server's UTC day ─────────────────────────

    func test_aSpentAllowanceStaysSpentWithinItsOwnDay() {
        let state = haul(updatedAt: utc(2026, 9, 12, 1), remaining: 0, allowance: 1)
            .scansLeft(at: utc(2026, 9, 12, 23))
        XCTAssertEqual(state, .remaining(0))
    }

    func test_aSpentAllowanceComesBackAfterTheUTCReset() {
        // A free user in UTC-7 spends their scan at 18:00 UTC Friday. The
        // server resets at 00:00 UTC Saturday. The widget kept reading "0 —
        // Back tomorrow, or go Pro" for the whole of Saturday and beyond.
        let state = haul(updatedAt: utc(2026, 9, 11, 18), remaining: 0, allowance: 1)
            .scansLeft(at: utc(2026, 9, 12, 2))
        XCTAssertEqual(state, .remaining(1))
        XCTAssertFalse(state.isSpent, "still pointing a user with a scan at the paywall")
        XCTAssertEqual(state.subtitle, "free scan left today")
    }

    func test_anAgedBlobWithNoStoredAllowanceIsUnknownRatherThanZero() {
        // A v2 blob carries the count but not the allowance. Unknown is the
        // honest answer; zero is the one that sends someone to the paywall.
        let state = haul(updatedAt: utc(2026, 9, 11, 18), remaining: 0, allowance: nil)
            .scansLeft(at: utc(2026, 9, 12, 2))
        XCTAssertEqual(state, .unknown)
        XCTAssertFalse(state.isSpent)
    }

    func test_theQuotaDayIsTheServersNotThePhones() {
        // 23:30 and 00:30 UTC are different allowance days however the phone
        // is set — this is the UTC-vs-local bug `FreeScanCounter` already
        // fixed on the app side, arrived at from the widget's direction.
        let blob = haul(updatedAt: utc(2026, 9, 11, 23), remaining: 0, allowance: 1)
        XCTAssertTrue(blob.quotaIsCurrent(at: utc(2026, 9, 11, 23)))
        XCTAssertFalse(blob.quotaIsCurrent(at: utc(2026, 9, 12, 0)))
    }

    // ── The streak lapses on a local day ─────────────────────────────────────

    func test_aStreakScannedTodayStands() {
        let now = utc(2026, 9, 12, 12)
        XCTAssertEqual(haul(updatedAt: now, streak: 5, streakLastScan: now)
                        .liveStreak(at: now), 5)
    }

    func test_aStreakScannedYesterdayStillStands() {
        // Matches `ScanStreak.current()`: today *or* yesterday keeps it alive.
        let now = utc(2026, 9, 12, 12)
        let yesterday = Calendar.current.date(byAdding: .day, value: -1, to: now)!
        XCTAssertEqual(haul(updatedAt: yesterday, streak: 5, streakLastScan: yesterday)
                        .liveStreak(at: now), 5)
    }

    func test_aStreakOlderThanYesterdayIsGone() {
        // A 5-day streak, last scan Friday, read on Sunday: the app itself
        // would compute 0, and the Lock Screen kept showing 5 all weekend.
        let now = utc(2026, 9, 13, 12)
        let friday = Calendar.current.date(byAdding: .day, value: -2, to: now)!
        XCTAssertEqual(haul(updatedAt: friday, streak: 5, streakLastScan: friday)
                        .liveStreak(at: now), 0)
    }

    func test_aStreakFromABlobWithNoDateIsTakenAtFaceValue() {
        // A v2 blob has no `streakLastScan`. Showing a possibly-stale streak
        // for one launch beats blanking a real one.
        let now = utc(2026, 9, 12, 12)
        XCTAssertEqual(haul(updatedAt: now, streak: 4, streakLastScan: nil)
                        .liveStreak(at: now), 4)
    }

    func test_aLapsedStreakTakesTheProHeadlineWithIt() {
        let now = utc(2026, 9, 13, 12)
        let friday = Calendar.current.date(byAdding: .day, value: -2, to: now)!
        let state = haul(updatedAt: friday, isPro: true, streak: 5,
                         streakLastScan: friday).scansLeft(at: now)
        XCTAssertEqual(state, .pro(streak: 0))
        XCTAssertEqual(state.headline, "∞", "still unlimited")
        XCTAssertEqual(state.subtitle, "Unlimited scans",
                       "a streak the user has lost is not mentioned")
    }

    // ── The month's profit belongs to one month ──────────────────────────────

    func test_thisMonthsProfitSurvivesInsideItsMonth() {
        let written = utc(2026, 9, 5, 12)
        let later = utc(2026, 9, 28, 12)
        let blob = haul(updatedAt: written, isPro: true, monthProfit: 214, monthFlips: 6)
        XCTAssertEqual(blob.monthProfit(at: later) ?? 0, 214, accuracy: 0.01)
        XCTAssertEqual(blob.monthFlips(at: later), 6)
    }

    func test_thisMonthsProfitDoesNotFollowTheUserIntoNextMonth() {
        // Sold six items in September for $214, last opened the app on the
        // 28th. On 3 October the widget read "$214 · from 6 flips" under a
        // header saying "This month", while the Flips screen showed $0.
        let written = utc(2026, 9, 28, 12)
        let october = Calendar.current.date(byAdding: .month, value: 1, to: written)!
        let blob = haul(updatedAt: written, isPro: true, monthProfit: 214, monthFlips: 6)
        XCTAssertNil(blob.monthProfit(at: october))
        XCTAssertEqual(blob.monthFlips(at: october), 0,
                       "\"$0 from 6 flips\" is worse than either half alone")
    }

    func test_theProfitAndTheFlipCountExpireTogether() {
        let written = utc(2026, 9, 28, 12)
        let october = Calendar.current.date(byAdding: .month, value: 1, to: written)!
        let blob = haul(updatedAt: written, isPro: true, monthProfit: 214, monthFlips: 6)
        XCTAssertEqual(blob.monthProfit(at: october) == nil,
                       blob.monthFlips(at: october) == 0)
    }

    // ── The timeline has to carry an entry at each boundary ──────────────────

    func test_everyBoundaryIsInTheFuture() {
        let now = utc(2026, 9, 12, 12)
        let dates = WidgetHaulData.refreshBoundaries(after: now)
        XCTAssertFalse(dates.isEmpty)
        for date in dates { XCTAssertGreaterThan(date, now) }
    }

    func test_boundariesAreSortedAndUnique() {
        // At UTC+0 the server day and the local day are the same instant, and
        // a duplicated entry date is not something WidgetKit should be handed.
        let now = utc(2026, 9, 12, 12)
        let dates = WidgetHaulData.refreshBoundaries(after: now)
        XCTAssertEqual(dates, dates.sorted())
        XCTAssertEqual(dates.count, Set(dates).count)
    }

    func test_theNextServerMidnightIsAlwaysScheduled() {
        let now = utc(2026, 9, 12, 12)
        let dates = WidgetHaulData.refreshBoundaries(after: now)
        XCTAssertTrue(dates.contains(utc(2026, 9, 13, 0)),
                      "nothing scheduled where the allowance actually resets")
    }

    func test_aMonthEndIsScheduledWhenItIsNear() {
        let now = utc(2026, 9, 29, 12)
        let dates = WidgetHaulData.refreshBoundaries(after: now)
        let local = Calendar.current
        let nextMonth = local.dateInterval(of: .month, for: now)!.end
        XCTAssertTrue(dates.contains(nextMonth))
    }

    func test_noBoundaryIsMoreThanAMonthOut() {
        // A timeline entry a year away is not a refresh, it is a leak.
        let now = utc(2026, 9, 12, 12)
        let limit = Calendar.current.date(byAdding: .day, value: 32, to: now)!
        for date in WidgetHaulData.refreshBoundaries(after: now) {
            XCTAssertLessThanOrEqual(date, limit)
        }
    }
}

// ── A Control Centre press nobody came forward to collect ────────────────────
//
// The intent writes its request into the App Group because it cannot post a
// navigation notification and be heard — on a cold start nothing is listening
// yet. Nothing aged the request out: the writer stored no time, the reader
// returned whatever was there, and nothing clears the key on background. A
// press the app never drained — a launch the system killed, a phone locked on
// the way out of a pocket, a mind changed — waited in the App Group until the
// next launch, whenever that was, and then opened the camera for a tap from
// days ago.
//
// `isFresh` is the decision, split out from `takePendingAction` so it can be
// asked without an App Group, the way `hasExpired` is split from `endIfExpired`.

final class WidgetPendingActionTests: XCTestCase {

    private let pressed = Date(timeIntervalSince1970: 1_757_000_000)

    func test_aPressIsActedOnWhileTheAppIsStillComingUp() {
        // The case the hand-off exists for: the intent writes, the app
        // launches, the scene appears and drains it seconds later.
        for seconds: Double in [0, 0.3, 2, 10, 60] {
            XCTAssertTrue(
                WidgetBridge.isFresh(requested: pressed,
                                     now: pressed.addingTimeInterval(seconds)),
                "\(seconds)s is still the same press")
        }
    }

    func test_thePressIsStillGoodAtExactlyTheWindow() {
        let ttl = WidgetBridge.pendingActionTTL
        XCTAssertTrue(
            WidgetBridge.isFresh(requested: pressed,
                                 now: pressed.addingTimeInterval(ttl)))
        XCTAssertFalse(
            WidgetBridge.isFresh(requested: pressed,
                                 now: pressed.addingTimeInterval(ttl + 1)))
    }

    func test_aPressFromDaysAgoDoesNotOpenTheCamera() {
        // The defect itself. Someone presses the button, the launch dies, and
        // the app is next opened on Thursday — straight into the camera, for
        // a thing they were standing in front of on Monday.
        for hours: Double in [1, 6, 24, 72] {
            XCTAssertFalse(
                WidgetBridge.isFresh(requested: pressed,
                                     now: pressed.addingTimeInterval(hours * 3600)),
                "\(hours)h later is not the press the user made")
        }
    }

    func test_aClockThatMovedBackwardsDropsThePress() {
        // Timezone change, NTP correction, a user setting the date by hand.
        // The age is then meaningless rather than small, so it is not treated
        // as a press made a moment ago.
        XCTAssertFalse(
            WidgetBridge.isFresh(requested: pressed,
                                 now: pressed.addingTimeInterval(-1)))
        XCTAssertFalse(
            WidgetBridge.isFresh(requested: pressed,
                                 now: pressed.addingTimeInterval(-3600)))
    }

    func test_theWindowOutlastsAColdLaunchWithoutOutlastingTheErrand() {
        // Bounds rather than the number: long enough that a slow cold start
        // never loses a real press, short enough that it cannot survive the
        // walk to the next shop.
        XCTAssertGreaterThanOrEqual(WidgetBridge.pendingActionTTL, 60,
                                    "a slow cold launch would lose the press")
        XCTAssertLessThanOrEqual(WidgetBridge.pendingActionTTL, 15 * 60,
                                 "long enough to reopen the camera for a forgotten press")
    }
}

// ── The thrift run's stale date ──────────────────────────────────────────────
//
// `staleDate` flips `context.isStale`; it does not dim anything by itself, and
// for a while a comment in the controller claimed it did — which is why no
// view read the flag. The date also has to sit inside the run: a scan late in
// a long run was pushing it past the point at which `update` ends the run,
// so the Activity would never declare itself stale before being killed.

final class ThriftRunStaleDateTests: XCTestCase {

    private let start = Date(timeIntervalSince1970: 1_757_000_000)

    @MainActor
    func test_theStaleDateIsNinetyMinutesFromTheLastUpdate() {
        let now = start.addingTimeInterval(10 * 60)
        XCTAssertEqual(
            ThriftRunController.staleDate(now: now, startedAt: start),
            now.addingTimeInterval(ThriftRunController.staleAfter))
    }

    @MainActor
    func test_theStaleDateNeverOutlivesTheRun() {
        // A scan at 7h55m would otherwise set it to 9h25m, past the 8-hour cap
        // at which `update` ends the run.
        let lateScan = start.addingTimeInterval(7 * 60 * 60 + 55 * 60)
        let stale = ThriftRunController.staleDate(now: lateScan, startedAt: start)
        XCTAssertEqual(stale,
                       start.addingTimeInterval(ThriftRunController.maximumRunDuration))
        XCTAssertLessThan(stale, lateScan.addingTimeInterval(ThriftRunController.staleAfter))
    }

    // ── The cap, and when it is actually asked ───────────────────────────
    //
    // It lived only inside `update`, which `ScanRepository` reaches only when
    // a scan is written — so the one case the cap exists for, a run somebody
    // started and then stopped scanning on, never met it. `ScanView`'s
    // foreground handler now asks directly; its comment used to claim that
    // already happened.

    @MainActor
    func test_aRunIsOverExactlyAtTheCap() {
        // `>=`, matching `staleDate`, which pins the final stale moment to
        // exactly this instant: the run declares itself stale and is over at
        // the same time rather than a tick apart.
        let cap = ThriftRunController.maximumRunDuration
        XCTAssertTrue(ThriftRunController.hasExpired(
            startedAt: start, now: start.addingTimeInterval(cap)))
        XCTAssertFalse(ThriftRunController.hasExpired(
            startedAt: start, now: start.addingTimeInterval(cap - 1)))
    }

    @MainActor
    func test_aRunInsideTheCapIsLeftAlone() {
        // The whole middle of a real trip. A shopper three hours in must not
        // have the Lock Screen total taken away on returning to the app.
        for hours in [0.0, 0.5, 1.5, 3, 7, 7.9] {
            XCTAssertFalse(
                ThriftRunController.hasExpired(
                    startedAt: start, now: start.addingTimeInterval(hours * 3600)),
                "\(hours)h in is still a live run")
        }
    }

    @MainActor
    func test_aRunLeftOvernightIsOver() {
        // The case in `maximumRunDuration`'s own comment: "People put the
        // phone in a pocket and go home; the Activity should not still be
        // there the next morning claiming to be live."
        XCTAssertTrue(ThriftRunController.hasExpired(
            startedAt: start, now: start.addingTimeInterval(14 * 3600)))
    }

    @MainActor
    func test_aFreshRunGoesStaleLongBeforeItIsEnded() {
        XCTAssertLessThan(ThriftRunController.staleAfter,
                          ThriftRunController.maximumRunDuration,
                          "a run would be killed before it ever declared itself stale")
        XCTAssertEqual(ThriftRunController.staleDate(now: start, startedAt: start),
                       start.addingTimeInterval(ThriftRunController.staleAfter))
    }
}

// ── The widget palette ───────────────────────────────────────────────────────
//
// The extension cannot import `DesignSystem.swift`, so nothing but a test can
// keep the two palettes in step — and for 1.4.0 nothing did. Every widget
// accent was a *light-mode* value drawn on a dark tile, and two of them failed
// WCAG AA while carrying 10-13pt text.
//
// These assertions are the check the compiler cannot make: the hexes still
// clear AA on the ground they are used on, and the fill token is still darker
// than the foreground one it was split out of.

final class WidgetPaletteTests: XCTestCase {

    /// WCAG 2.1 relative luminance.
    private func luminance(_ hex: String) -> Double {
        let channels = stride(from: 0, to: 6, by: 2).map { offset -> Double in
            let start = hex.index(hex.startIndex, offsetBy: offset)
            let end = hex.index(start, offsetBy: 2)
            let value = Double(UInt8(hex[start..<end], radix: 16) ?? 0) / 255
            return value <= 0.03928 ? value / 12.92
                                    : pow((value + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
    }

    private func contrast(_ a: String, _ b: String) -> Double {
        let (x, y) = (luminance(a), luminance(b))
        return (max(x, y) + 0.05) / (min(x, y) + 0.05)
    }

    /// Cream over a ground at partial opacity, which is what the widgets draw
    /// for their secondary labels — the composite is what the eye sees.
    private func composite(_ hex: String, over ground: String, alpha: Double) -> String {
        func bytes(_ h: String) -> [Double] {
            stride(from: 0, to: 6, by: 2).map { offset in
                let start = h.index(h.startIndex, offsetBy: offset)
                let end = h.index(start, offsetBy: 2)
                return Double(UInt8(h[start..<end], radix: 16) ?? 0)
            }
        }
        let (front, back) = (bytes(hex), bytes(ground))
        return (0..<3).map { i in
            String(format: "%02X", Int((alpha * front[i] + (1 - alpha) * back[i]).rounded()))
        }.joined()
    }

    // ── The helper itself, against known values ──────────────────────────────

    func test_theContrastHelperAgreesWithTheSpec() {
        XCTAssertEqual(contrast("FFFFFF", "000000"), 21, accuracy: 0.01)
        XCTAssertEqual(contrast("000000", "000000"), 1, accuracy: 0.01)
        // WebAIM's worked example: #777777 on white is 4.48:1.
        XCTAssertEqual(contrast("777777", "FFFFFF"), 4.48, accuracy: 0.02)
    }

    // ── Every foreground on the tile ─────────────────────────────────────────

    func test_everyForegroundOnTheTileClearsAAForSmallText() {
        // All of these carry 10-13pt labels somewhere in the bundle, so the
        // 4.5:1 threshold applies — not the 3:1 large-text one.
        let foregrounds = [
            ("cream",      SnapDarkHex.cream),
            ("terracotta", SnapDarkHex.terracotta),
            ("sage",       SnapDarkHex.sage),
            ("warmGray",   SnapDarkHex.warmGray),
        ]
        for (name, hex) in foregrounds {
            let ratio = contrast(hex, SnapDarkHex.charcoal)
            XCTAssertGreaterThanOrEqual(
                ratio, 4.5,
                "\(name) is \(String(format: "%.2f", ratio)):1 on the tile")
        }
    }

    func test_theOldPaletteWouldHaveFailedThisTest() {
        // The values that shipped, so the assertion above is known to bite.
        XCTAssertLessThan(contrast("C9583A", "2C2C2C"), 4.5, "old terracotta")
        XCTAssertLessThan(contrast("8A857E", "2C2C2C"), 4.5, "old warm grey")
    }

    func test_dimmedCreamStillClearsAAOnTheTile() {
        // The widgets draw their wordmarks and captions at 65-80% cream.
        for alpha in [0.65, 0.7, 0.75, 0.8] {
            let blended = composite(SnapDarkHex.cream,
                                    over: SnapDarkHex.charcoal, alpha: alpha)
            XCTAssertGreaterThanOrEqual(contrast(blended, SnapDarkHex.charcoal), 4.5,
                                        "cream at \(alpha)")
        }
    }

    // ── Filled accents ───────────────────────────────────────────────────────

    func test_creamClearsAAOnEveryFilledAccent() {
        // The Quick Scan tile's gradient and the medium widget's Scan chip.
        for fill in [SnapDarkHex.terracottaFill, SnapDarkHex.terracottaFillDeep] {
            XCTAssertGreaterThanOrEqual(contrast(SnapDarkHex.cream, fill), 4.5, fill)
        }
    }

    func test_theFillIsDarkerThanTheForegroundItWasSplitFrom() {
        // The whole point of the split: one token cannot be both, and getting
        // them the wrong way round is silent.
        XCTAssertLessThan(luminance(SnapDarkHex.terracottaFill),
                          luminance(SnapDarkHex.terracotta))
        XCTAssertLessThan(luminance(SnapDarkHex.terracottaFillDeep),
                          luminance(SnapDarkHex.terracottaFill))
    }

    func test_theForegroundTerracottaWouldFailAsAFill() {
        // Why the split exists, stated as a test rather than a comment.
        XCTAssertLessThan(contrast(SnapDarkHex.cream, SnapDarkHex.terracotta), 3.0)
    }

    // ── And still the app's own values ───────────────────────────────────────

    func test_theWidgetAccentsAreTheAppsDarkModeValues() {
        // A widget tile is dark in both themes, so the light-mode half of each
        // adaptive pair is the wrong one — which is what had been typed in by
        // hand. `DesignSystem.swift` reads these same constants, so changing a
        // dark-mode accent there changes the widget with it.
        XCTAssertEqual(SnapDarkHex.terracotta, "E8845F")
        XCTAssertEqual(SnapDarkHex.sage, "8FB08A")
        XCTAssertEqual(SnapDarkHex.warmGray, "B0A297")
        XCTAssertEqual(SnapDarkHex.espresso, "F0E9E2")
        XCTAssertEqual(SnapDarkHex.charcoal, "1C1714")
        XCTAssertEqual(SnapDarkHex.cream, "FBF7F2")
    }

    func test_everyHexIsSixUppercaseDigits() {
        // `Color(hex:)` strips non-alphanumerics and scans what is left, so a
        // typo degrades to a colour rather than a build error.
        let all = [SnapDarkHex.charcoal, SnapDarkHex.cream, SnapDarkHex.terracotta,
                   SnapDarkHex.sage, SnapDarkHex.warmGray, SnapDarkHex.espresso,
                   SnapDarkHex.terracottaFill, SnapDarkHex.terracottaFillDeep]
        for hex in all {
            XCTAssertEqual(hex.count, 6, hex)
            XCTAssertTrue(hex.allSatisfy { $0.isHexDigit && !$0.isLowercase }, hex)
        }
    }

    // ── A dimmed accent is no longer a dark surface ──────────────────────────

    func test_creamOnADimmedAccentIsUnreadable() {
        // The premise behind `snapOnAccent` being fixed cream — "the accent is
        // dark enough in both themes" — holds only at full opacity. This is
        // the measurement that says so, kept as a test so the reasoning cannot
        // be quietly re-inverted.
        XCTAssertEqual(contrast(SnapDarkHex.cream, SnapDarkHex.terracottaFill),
                       5.43, accuracy: 0.02)

        let dimmedOnLight = composite(SnapDarkHex.terracottaFill,
                                      over: Color.SnapLightHex.background, alpha: 0.4)
        XCTAssertEqual(contrast(SnapDarkHex.cream, dimmedOnLight),
                       1.82, accuracy: 0.03,
                       "cream on a 40% accent over a light ground — the label " +
                       "did not read as disabled, it disappeared")
    }

    func test_themedInkReadsOnADimmedAccentInEveryVariant() {
        // What `PrimaryButton` uses when disabled. Every ground the button can
        // sit on, in both themes and both high-contrast variants.
        let cases: [(String, String, String)] = [
            ("light bg",    Color.SnapLightHex.background, Color.SnapLightHex.espresso),
            ("light card",  Color.SnapLightHex.card,       Color.SnapLightHex.espresso),
            ("dark bg",     SnapDarkHex.ground,      SnapDarkHex.espresso),
            ("dark card",   SnapDarkHex.card,        SnapDarkHex.espresso),
            ("light HC",    Color.SnapLightHex.background, Color.SnapLightHex.espressoHC),
            ("dark HC",     SnapDarkHex.ground,      "FFFFFF"),
        ]
        for (label, ground, ink) in cases {
            let fill = composite(SnapDarkHex.terracottaFill, over: ground, alpha: 0.4)
            XCTAssertGreaterThan(contrast(ink, fill), 4.5,
                                 "\(label): a disabled label still has to be readable")
        }
    }

    func test_dimmingTheWholeButtonWouldBeWorse() {
        // The obvious fix, measured rather than assumed — it fails light mode
        // anyway and drags dark mode from ~10:1 down under 4:1, because it
        // dims a cream label toward a dark ground.
        let lightFill = composite(SnapDarkHex.terracottaFill,
                                  over: Color.SnapLightHex.background, alpha: 0.5)
        let lightLabel = composite(SnapDarkHex.cream,
                                   over: Color.SnapLightHex.background, alpha: 0.5)
        XCTAssertLessThan(contrast(lightLabel, lightFill), 3.0)

        let darkFill = composite(SnapDarkHex.terracottaFill,
                                 over: SnapDarkHex.ground, alpha: 0.5)
        let darkLabel = composite(SnapDarkHex.cream,
                                  over: SnapDarkHex.ground, alpha: 0.5)
        XCTAssertLessThan(contrast(darkLabel, darkFill), 4.5,
                          "and it would break the theme that currently passes")
    }

    // ── The analysing overlay sits on the user's photo, not on a colour ─────

    func test_theAnalysingCaptionClearsAAOnABrightPhoto() {
        // The scrim is `snapCharcoal.opacity(0.72)` over the captured photo, so
        // the ground is only as dark as the photo lets it be. A phone held over
        // an item on a white shelf is the common case, not the corner one.
        let scrim = composite(SnapDarkHex.charcoal, over: "FFFFFF", alpha: 0.72)
        XCTAssertLessThan(contrast(composite(SnapDarkHex.cream, over: scrim, alpha: 0.7),
                                   scrim), 4.5,
                          "0.7 was the failing value — 4.21:1 at 13pt")
        XCTAssertGreaterThan(contrast(composite(SnapDarkHex.cream, over: scrim, alpha: 0.8),
                                      scrim), 4.5,
                             "0.8 is what ships")
    }

    func test_theAnalysingMessageItselfWasNeverTheProblem() {
        // Full-opacity cream, 17pt. Asserted so a future "fix" does not touch
        // the line that was already fine.
        let scrim = composite(SnapDarkHex.charcoal, over: "FFFFFF", alpha: 0.72)
        XCTAssertGreaterThan(contrast(SnapDarkHex.cream, scrim), 4.5)
    }

    // ── The shimmer sweep has to be visible on the skeleton ──────────────────

    /// The skeleton surface: `snapBorder.opacity(0.6)` over the card.
    private func skeleton(border: String, card: String) -> String {
        composite(border, over: card, alpha: 0.6)
    }

    func test_aWhiteSweepIsInvisibleOnTheLightSkeleton() {
        let base = skeleton(border: Color.SnapBorderHex.light, card: Color.SnapLightHex.card)
        let peak = composite("FFFFFF", over: base, alpha: 0.65)
        let wash = composite("FFFFFF", over: base, alpha: 0.18)
        XCTAssertLessThan(contrast(peak, base), 1.1,
                          "a 1.09:1 peak is below the threshold of visible " +
                          "difference — the placeholder was a static block, so " +
                          "a slow decode looked identical to a missing image")
        XCTAssertLessThan(contrast(wash, base), 1.05,
                          "and the Reduce Motion wash conveyed nothing at all")
    }

    func test_aWhiteSweepIsAColdFlareOnTheDarkSkeleton() {
        let base = skeleton(border: Color.SnapBorderHex.dark, card: SnapDarkHex.card)
        let peak = composite("FFFFFF", over: base, alpha: 0.65)
        XCTAssertGreaterThan(contrast(peak, base), 7.0,
                             "pure white on a warm espresso card — the same " +
                             "token failing in opposite directions")
    }

    func test_thePaletteSweepIsVisibleInBothThemes() {
        // `snapShimmer` resolves to `snapEspresso`: dark ink on light, cream on
        // dark. 3:1 is WCAG 1.4.11's floor for a meaningful non-text boundary,
        // and what a "pending" placeholder has to clear to mean anything.
        let light = skeleton(border: Color.SnapBorderHex.light, card: Color.SnapLightHex.card)
        XCTAssertGreaterThan(
            contrast(composite(Color.SnapLightHex.espresso, over: light, alpha: 0.65), light), 3.0)
        XCTAssertGreaterThan(
            contrast(composite(Color.SnapLightHex.espresso, over: light, alpha: 0.5), light), 3.0,
            "including the static Reduce Motion wash")

        let dark = skeleton(border: Color.SnapBorderHex.dark, card: SnapDarkHex.card)
        XCTAssertGreaterThan(
            contrast(composite(SnapDarkHex.espresso, over: dark, alpha: 0.65), dark), 3.0)
        XCTAssertGreaterThan(
            contrast(composite(SnapDarkHex.espresso, over: dark, alpha: 0.5), dark), 3.0)
    }

    // ── A control's edge is held to 3:1; a divider is not ───────────────────

    func test_theDividerTokenCouldNeverHaveBeenAControlEdge() {
        // Not a regression test on a mistake — the record of why a second
        // token exists. `snapBorder` is right for dividers and wrong for the
        // only affordance an unselected plan card has.
        XCTAssertLessThan(contrast(Color.SnapBorderHex.light, Color.SnapLightHex.card), 1.3)
        XCTAssertLessThan(contrast(Color.SnapBorderHex.dark, SnapDarkHex.card), 1.3)
        XCTAssertLessThan(contrast(Color.SnapBorderHex.lightHighContrast,
                                   Color.SnapLightHex.card), 2.0,
                          "Increase Contrast did not rescue it either")
        XCTAssertLessThan(contrast(Color.SnapBorderHex.darkHighContrast, SnapDarkHex.card), 2.0)
    }

    func test_theControlEdgeClearsThreeToOneInBothThemes() {
        // WCAG 1.4.11: the visual boundary of a UI component.
        XCTAssertGreaterThan(contrast(Color.SnapControlBorderHex.light,
                                      Color.SnapLightHex.card), 3.0)
        XCTAssertGreaterThan(contrast(Color.SnapControlBorderHex.dark, SnapDarkHex.card), 3.0)
    }

    func test_increasedContrastGoesFurtherThanTheMinimum() {
        // Someone who turns the setting on is asking for more than 3:1, and
        // the old high-contrast pair gave them 0.45 of a ratio point.
        XCTAssertGreaterThan(contrast(Color.SnapControlBorderHex.lightHighContrast,
                                      Color.SnapLightHex.card),
                             contrast(Color.SnapControlBorderHex.light,
                                      Color.SnapLightHex.card))
        XCTAssertGreaterThan(contrast(Color.SnapControlBorderHex.darkHighContrast,
                                      SnapDarkHex.card),
                             contrast(Color.SnapControlBorderHex.dark, SnapDarkHex.card))
    }

    // ── A drop shadow cannot separate anything from a near-black ground ─────

    func test_theWarmShadowLightenedTheDarkGroundInsteadOfDarkeningIt() {
        let halo = composite("785032", over: SnapDarkHex.ground, alpha: 0.08)
        XCTAssertLessThan(contrast(SnapDarkHex.card, halo),
                          contrast(SnapDarkHex.card, SnapDarkHex.ground),
                          "the card was harder to pick out against its own " +
                          "shadow than against the plain background")
    }

    func test_blackFixesTheDirectionAndStillCannotDoTheJob() {
        let halo = composite("000000", over: SnapDarkHex.ground, alpha: 0.08)
        XCTAssertGreaterThan(contrast(SnapDarkHex.card, halo),
                             contrast(SnapDarkHex.card, SnapDarkHex.ground))
        // Which is why the hairline exists: even at 45% the halo is nowhere.
        XCTAssertLessThan(contrast(SnapDarkHex.card,
                                   composite("000000", over: SnapDarkHex.ground, alpha: 0.45)),
                          1.2)
    }

    func test_theDarkCardEdgeIsAnEdgeYouCanSee() {
        // Not held to 3:1 — a card is a surface, not a control. It has to be
        // visible, and more visible than the card's 1.09:1 against the ground.
        XCTAssertGreaterThan(contrast("584840", SnapDarkHex.card),
                             contrast(SnapDarkHex.card, SnapDarkHex.ground))
        XCTAssertLessThan(contrast("584840", SnapDarkHex.card), 3.0,
                          "an edge, not an outline you notice")
    }
}

// ── Spoken labels ────────────────────────────────────────────────────────────
//
// Every widget was handing display strings straight to `accessibilityLabel`.
// A money range carries an en dash, which a voice either reads as "dash" or
// drops — and dropping it runs "$348–$620" together into a number that is not
// the answer to anything. Two widgets had no label at all and read out SF
// Symbol names instead.

final class WidgetSpokenLabelTests: XCTestCase {

    private func haul(itemCount: Int, low: Double = 0, high: Double = 0,
                      lastRange: String = "") -> WidgetHaulData {
        WidgetHaulData(totalLow: low, totalHigh: high, itemCount: itemCount,
                       lastItemName: "Levi's 501", lastItemRange: lastRange,
                       updatedAt: .now, freeScansRemaining: nil, isPro: false,
                       streak: 0, recentFinds: [], monthProfit: nil, monthFlips: 0)
    }

    func test_aRangeIsSpokenAsARangeNotADash() {
        let spoken = haul(itemCount: 8, low: 348, high: 620).spokenRange
        XCTAssertEqual(spoken, "$348 to $620")
        XCTAssertFalse(spoken.contains("–"), "an en dash reaches the voice")
    }

    func test_anEmptyHaulIsSpokenPlainly() {
        XCTAssertEqual(WidgetHaulData.empty.spokenRange, "nothing scanned yet")
        XCTAssertEqual(WidgetHaulData.empty.spokenHaul, "SnapWorth. Nothing scanned yet.")
    }

    func test_theWholeHaulIsOneSentence() {
        XCTAssertEqual(haul(itemCount: 8, low: 348, high: 620).spokenHaul,
                       "SnapWorth haul, 8 items scanned, worth $348 to $620.")
    }

    func test_oneItemIsSingularInTheSpokenHaulToo() {
        XCTAssertTrue(haul(itemCount: 1, low: 60, high: 95).spokenHaul
                        .contains("1 item scanned"))
    }

    func test_anItemRangeIsRespelledForTheVoice() {
        // `ScanResult.formattedRange` and every `WidgetFind.range` come in
        // already formatted, so only the separator can be changed.
        XCTAssertEqual(WidgetHaulData.spoken("$60–$95"), "$60 to $95")
        XCTAssertEqual(WidgetHaulData.spoken("$1,240–$2,100"), "$1,240 to $2,100")
    }

    func test_respellingLeavesAnythingWithoutADashAlone() {
        XCTAssertEqual(WidgetHaulData.spoken("$0"), "$0")
        XCTAssertEqual(WidgetHaulData.spoken(""), "")
    }

    func test_noSpokenLabelContainsADisplayDash() {
        // The invariant, over the strings a widget actually hands to VoiceOver.
        let full = haul(itemCount: 3, low: 95, high: 150, lastRange: "$40–$70")
        for label in [full.spokenRange, full.spokenHaul,
                      WidgetHaulData.spoken(full.lastItemRange)] {
            XCTAssertFalse(label.contains("–"), label)
        }
    }
}

// ── Flash mode ───────────────────────────────────────────────────────────────
//
// `AVCapturePhotoSettings.flashMode` must be one of the output's
// `supportedFlashModes`. Anything else raises `NSInvalidArgumentException`,
// which is an abort and not a throw — there is nothing to catch. `.auto` was
// being set unconditionally, and an iPad running the app in iPhone
// compatibility mode reports `[.off]` and nothing else: every shutter tap
// killed the process, on the one screen the app exists for.
//
// The same class of bug was already fixed in this file for photo dimensions,
// with the same comment about uncatchable aborts. The flash was missed.

final class CameraFlashModeTests: XCTestCase {

    func test_autoIsUsedWhenTheDeviceHasIt() {
        XCTAssertEqual(
            CameraManager.flashMode(preferring: .auto, supported: [.off, .on, .auto]),
            .auto)
    }

    func test_aFlashlessDeviceGetsOffRatherThanACrash() {
        // What an iPad in compatibility mode reports.
        XCTAssertEqual(CameraManager.flashMode(preferring: .auto, supported: [.off]), .off)
    }

    func test_aDeviceWithoutAutoIsNotHandedOnInstead() {
        // Falling through to `.first` here would fire a flash nobody asked for.
        XCTAssertEqual(CameraManager.flashMode(preferring: .auto, supported: [.on, .off]),
                       .off)
    }

    func test_anEmptyListAsksForNothing() {
        // The caller skips assigning `flashMode` at all, which is the only safe
        // thing to do: every value would raise.
        XCTAssertNil(CameraManager.flashMode(preferring: .auto, supported: []))
    }

    func test_theResultIsAlwaysSomethingTheDeviceSupports() {
        // The invariant that matters: whatever comes back must be assignable.
        let cases: [[AVCaptureDevice.FlashMode]] = [
            [], [.off], [.on], [.auto], [.off, .on], [.off, .auto], [.on, .auto],
            [.off, .on, .auto],
        ]
        for supported in cases {
            guard let chosen = CameraManager.flashMode(preferring: .auto,
                                                       supported: supported) else {
                XCTAssertTrue(supported.isEmpty, "returned nil for \(supported)")
                continue
            }
            XCTAssertTrue(supported.contains(chosen),
                          "\(chosen) is not in \(supported) — this is the abort")
        }
    }

    // ── Recovering a session we did not stop ─────────────────────────────
    //
    // A stop that came from the system used to be permanent: another client
    // taking the camera, or a mediaserverd reset, left `isRunning` false, the
    // preview frozen on its last frame, and `capturePhoto` returning at its
    // own guard — so every shutter tap after that only vibrated. `onAppear`
    // cannot rescue it because the view never disappeared, and the user's only
    // fix was force-quitting the app.
    //
    // The observers themselves cannot be unit-tested (AVFoundation posts the
    // notifications, and the hardware states cannot be reproduced), so the
    // decision is tested apart from the hardware — the same split
    // `flashMode` and `preferredPhotoDimensions` already use.

    func test_aMediaServicesResetIsWorthRestarting() {
        // mediaserverd restarted underneath us: the session is stopped but
        // still configured, so `startRunning` is the whole recovery.
        XCTAssertTrue(CameraManager.shouldRestart(after: .mediaServicesWereReset))
    }

    func test_everythingElseIsLeftAlone() {
        // Not caution for its own sake: a failure a restart cannot fix posts
        // another runtime error when we retry it, and that is a
        // notification-and-restart loop for as long as the screen is open.
        for code in [AVError.Code.deviceAlreadyUsedByAnotherSession,
                     .sessionConfigurationChanged,
                     .mediaDiscontinuity,
                     .unknown] {
            XCTAssertFalse(CameraManager.shouldRestart(after: code),
                           "\(code) would loop if we retried it")
        }
    }
}

// ── The app palette ──────────────────────────────────────────────────────────
//
// Every contrast failure this palette has had was invisible to the compiler and
// to every test, because a `Color` cannot be measured. Three were live at once:
//
//   • cream on a terracotta fill — 3.18:1 light, 2.49:1 dark, 4.37:1 even under
//     Increased Contrast. That is every primary button in the app.
//   • terracotta as a text colour — 3.39:1 on a card, 3.18:1 on the ground.
//     About thirty labels, including the keyboard toolbar's "Done", which is
//     the only way off the money keypad, and every error message.
//   • cream ink on a dark-mode amber badge — 1.46:1. The "SAVE 33%" on the
//     yearly plan, which is the reason to pick it.
//
// And `snapWarmGray`, deliberately darkened from 3.1:1 to 5.7:1 to clear AA,
// was being re-diluted by `.opacity()` at thirteen call sites back to 2.3-3.7:1
// — one of them Apple's required auto-renew disclosure.
//
// The hexes are in `Color.SnapLightHex` and `SnapDarkHex` so these assertions
// can exist at all.

final class AppPaletteTests: XCTestCase {

    private typealias L = Color.SnapLightHex

    private func luminance(_ hex: String) -> Double {
        let channels = stride(from: 0, to: 6, by: 2).map { offset -> Double in
            let start = hex.index(hex.startIndex, offsetBy: offset)
            let end = hex.index(start, offsetBy: 2)
            let value = Double(UInt8(hex[start..<end], radix: 16) ?? 0) / 255
            return value <= 0.03928 ? value / 12.92
                                    : pow((value + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
    }

    private func contrast(_ a: String, _ b: String) -> Double {
        let (x, y) = (luminance(a), luminance(b))
        return (max(x, y) + 0.05) / (min(x, y) + 0.05)
    }

    private func assertAA(_ fg: String, on bg: String, _ what: String,
                          minimum: Double = 4.5,
                          file: StaticString = #filePath, line: UInt = #line) {
        let ratio = contrast(fg, bg)
        XCTAssertGreaterThanOrEqual(
            ratio, minimum,
            "\(what) is \(String(format: "%.2f", ratio)):1, needs \(minimum):1",
            file: file, line: line)
    }

    // ── Body text, both themes, both surfaces ────────────────────────────────

    func test_bodyTextClearsAAEverywhereItIsDrawn() {
        assertAA(L.espresso, on: L.background, "espresso on the light ground")
        assertAA(L.espresso, on: L.card, "espresso on a light card")
        assertAA(SnapDarkHex.espresso, on: SnapDarkHex.ground, "espresso on the dark ground")
        assertAA(SnapDarkHex.espresso, on: SnapDarkHex.card, "espresso on a dark card")
    }

    func test_secondaryTextClearsAAEverywhereItIsDrawn() {
        // This is the token that was darkened to 5.7:1 on purpose. Thirteen
        // call sites then put `.opacity()` on it, which is what the sweep in
        // this commit removed — an opacity here cannot be caught by a test of
        // the token, so the point of asserting it is that the *token* stays
        // good enough that undiluted use is always correct.
        assertAA(L.warmGray, on: L.background, "warm grey on the light ground")
        assertAA(L.warmGray, on: L.card, "warm grey on a light card")
        assertAA(SnapDarkHex.warmGray, on: SnapDarkHex.ground, "warm grey on the dark ground")
        assertAA(SnapDarkHex.warmGray, on: SnapDarkHex.card, "warm grey on a dark card")
    }

    // ── Accents used as text ─────────────────────────────────────────────────

    func test_terracottaAsTextClearsAA() {
        assertAA(L.terracottaText, on: L.background, "terracotta text on the ground")
        assertAA(L.terracottaText, on: L.card, "terracotta text on a card")
        assertAA(SnapDarkHex.terracotta, on: SnapDarkHex.ground, "terracotta text, dark ground")
        assertAA(SnapDarkHex.terracotta, on: SnapDarkHex.card, "terracotta text, dark card")
    }

    func test_theBrandTerracottaWouldNotHaveClearedItAsText() {
        // Why `snapTerracottaText` exists rather than reusing the brand value.
        XCTAssertLessThan(contrast(L.terracotta, L.card), 4.5)
        XCTAssertLessThan(contrast(L.terracotta, L.background), 4.5)
    }

    func test_theBrandTerracottaIsStillFineForABorder() {
        // Which is why it was kept, rather than darkened app-wide: WCAG holds a
        // UI component boundary to 3:1, and every remaining use of the brand
        // token is a stroke, a tint, a dot or a control accent.
        assertAA(L.terracotta, on: L.card, "terracotta border on a card", minimum: 3.0)
        assertAA(L.terracotta, on: L.background, "terracotta border on the ground", minimum: 3.0)
    }

    func test_sageAsMoneyClearsAA() {
        // Sage is the money colour — every estimate, every profit figure.
        assertAA(L.sageText, on: L.background, "money on the light ground")
        assertAA(L.sageText, on: L.card, "money on a light card")
        assertAA(SnapDarkHex.sage, on: SnapDarkHex.ground, "money on the dark ground")
        assertAA(SnapDarkHex.sage, on: SnapDarkHex.card, "money on a dark card")
    }

    func test_theBrandSageWouldNotHaveClearedItAsText() {
        // 3.38:1 and 3.61:1. In light mode every number the app exists to show
        // was under AA, and this test is what found it — no finder did, because
        // the symptom reported was the *diluted* sage in the History and Flips
        // captions at 2.09:1, which made the undiluted case look fine.
        XCTAssertLessThan(contrast(L.sage, L.background), 4.5)
        XCTAssertLessThan(contrast(L.sage, L.card), 4.5)
    }

    func test_theBrandSageIsStillFineForATintOrAStroke() {
        assertAA(L.sage, on: L.card, "sage stroke on a card", minimum: 3.0)
        assertAA(L.sage, on: L.background, "sage stroke on the ground", minimum: 3.0)
    }

    // ── Filled accents ──────────────────────────────────────────────────────

    func test_creamInkClearsAAOnEveryFilledAccent() {
        // `snapOnAccent` is fixed cream, so the fill has to clear AA against
        // cream in *both* themes — which is why the fill is fixed too.
        assertAA(SnapDarkHex.cream, on: SnapDarkHex.terracottaFill, "button label on its fill")
    }

    func test_theBrandTerracottaWouldNotHaveClearedItAsAFill() {
        XCTAssertLessThan(contrast(SnapDarkHex.cream, L.terracotta), 4.5,
                          "light terracotta fill")
        XCTAssertLessThan(contrast(SnapDarkHex.cream, SnapDarkHex.terracotta), 4.5,
                          "dark terracotta fill")
        XCTAssertLessThan(contrast(SnapDarkHex.cream, L.terracottaHC), 4.5,
                          "even the Increased-Contrast value")
    }

    func test_amberBadgeInkClearsAAInBothThemes() {
        // The ink is fixed dark precisely because amber stays light in both.
        assertAA(L.espresso, on: L.amber, "badge ink on light amber")
        assertAA(L.espresso, on: SnapDarkHex.amber, "badge ink on dark amber")
    }

    func test_themeFollowingInkOnAmberWouldHaveBeenInvisible() {
        // 1.46:1 — what shipped.
        XCTAssertLessThan(contrast(SnapDarkHex.espresso, SnapDarkHex.amber), 2.0)
    }

    // ── Increased Contrast must never make anything worse ───────────────────

    func test_increasedContrastOnlyEverIncreasesContrast() {
        let pairs = [
            ("espresso", L.espresso, L.espressoHC),
            ("warmGray", L.warmGray, L.warmGrayHC),
            ("sageText", L.sageText, L.sageTextHC),
            ("terracottaText", L.terracottaText, L.terracottaTextHC),
        ]
        for (name, normal, high) in pairs {
            XCTAssertGreaterThanOrEqual(
                contrast(high, L.background), contrast(normal, L.background),
                "\(name)'s high-contrast value is lighter than its normal one")
        }
    }

    func test_everyHexIsSixUppercaseDigits() {
        let all = [L.background, L.card, L.terracotta, L.terracottaHC,
                   L.terracottaText, L.terracottaTextHC, L.sage, L.sageHC,
                   L.sageText, L.sageTextHC,
                   L.amber, L.espresso, L.espressoHC, L.warmGray, L.warmGrayHC,
                   L.border]
        for hex in all {
            XCTAssertEqual(hex.count, 6, hex)
            XCTAssertTrue(hex.allSatisfy { $0.isHexDigit && !$0.isLowercase }, hex)
        }
    }
}

// ── "No flips sold yet this month", about a month with sales in it ───────────
//
// `monthFlips` counts the sales that contributed to `monthProfit`, which is
// right for "$214 from 6 flips" and wrong for "did anything sell". A sale with
// no paid price has a nil `realizedProfit`, so it was dropped from both, and
// `monthProfit: nil` meant two different things: nothing sold, and things sold
// that cannot be priced. `paidPrice` is optional and the app advertises one
// benefit for entering it, so the second is the ordinary case — and the widget
// said "No flips sold yet this month" while My Flips, reading the same rows,
// said two items sold.

final class WidgetMonthLedgerTests: XCTestCase {

    private let cal = Calendar(identifier: .gregorian)
    private func day(_ month: Int, _ day: Int) -> Date {
        cal.date(from: DateComponents(year: 2026, month: month, day: day, hour: 12))!
    }

    private func item(sold: Bool, on date: Date?, paid: Double? = nil,
                      price: Double? = nil) -> ScanResult {
        ScanResult(itemName: "Item", brand: "B", category: "clothing",
                   conditionNotes: "Good", valueLow: 40, valueHigh: 60,
                   confidence: "High", soldListingsCount: 0,
                   listingTitle: "T", listingDescription: "D",
                   paidPrice: paid, statusRaw: sold ? "sold" : "owned",
                   soldPrice: price, soldDate: date)
    }

    private func ledger(_ results: [ScanResult]) -> (profit: Double, flips: Int, sold: Int) {
        WidgetDataStore.monthLedger(results: results, now: day(9, 20), calendar: cal)
    }

    func test_salesWithNoCostBasisStillCountAsSales() {
        // The defect. Two sales, neither with a paid price: the profit is
        // genuinely unknown, but "nothing sold" is a false statement about the
        // user's own month.
        let out = ledger([item(sold: true, on: day(9, 3), price: 40),
                          item(sold: true, on: day(9, 11), price: 25)])
        XCTAssertEqual(out.sold, 2, "the widget would have said none")
        XCTAssertEqual(out.flips, 0, "neither can be priced")
        XCTAssertEqual(out.profit, 0)
    }

    func test_theTwoCountsDifferByExactlyTheUnpricedSales() {
        let out = ledger([item(sold: true, on: day(9, 3), paid: 8, price: 65),
                          item(sold: true, on: day(9, 11), price: 25),
                          item(sold: true, on: day(9, 18), price: 30)])
        XCTAssertEqual(out.sold, 3)
        XCTAssertEqual(out.flips, 1)
        XCTAssertEqual(out.profit, 57, accuracy: 0.001)
    }

    func test_aTrulyEmptyMonthIsStillEmpty() {
        // The caption this fix adds must not appear for someone who sold
        // nothing — that would be its own false claim.
        XCTAssertEqual(ledger([]).sold, 0)
        XCTAssertEqual(ledger([item(sold: false, on: nil)]).sold, 0)
        XCTAssertEqual(ledger([item(sold: true, on: day(8, 30), price: 40)]).sold, 0,
                       "August is not this month")
    }

    func test_onlySoldItemsInTheMonthCount() {
        let out = ledger([
            item(sold: false, on: nil, paid: 5),                       // owned
            // Marked sold in September and then set back to Listed. Nothing
            // clears `soldDate` when the status moves back
            // (ResultView.swift:523 only ever sets it), so this state is
            // reachable and the status test is what keeps it out.
            item(sold: false, on: day(9, 7), paid: 5, price: 40),
            item(sold: true, on: nil, paid: 5, price: 40),             // sold, no date
            item(sold: true, on: day(10, 2), paid: 5, price: 40),      // next month
            item(sold: true, on: day(9, 1), paid: 5, price: 40),       // counts
        ])
        XCTAssertEqual(out.sold, 1)
        XCTAssertEqual(out.flips, 1)
        XCTAssertEqual(out.profit, 35, accuracy: 0.001)
    }

    func test_everyProfitCameFromAnItemThatWasAlsoCounted_asSold() {
        // The containment the writer's comment asserts: flips can never exceed
        // sold, whatever the mix.
        for unpriced in 0...3 {
            var rows = [item(sold: true, on: day(9, 4), paid: 10, price: 30)]
            rows += (0..<unpriced).map { _ in item(sold: true, on: day(9, 5), price: 30) }
            let out = ledger(rows)
            XCTAssertLessThanOrEqual(out.flips, out.sold)
            XCTAssertEqual(out.sold - out.flips, unpriced)
        }
    }

    // ── The stored count ages out with the rest of the month ─────────────

    private func haul(monthSold: Int, writtenIn month: Int) -> WidgetHaulData {
        WidgetHaulData(
            totalLow: 0, totalHigh: 0, itemCount: 0,
            lastItemName: "", lastItemRange: "", updatedAt: day(month, 15),
            freeScansRemaining: nil, isPro: true, streak: 0,
            recentFinds: [], monthProfit: nil, monthFlips: 0,
            monthSold: monthSold)
    }

    func test_theSoldCountDiesWithItsMonth() {
        // Same boundary as `monthProfit` and `monthFlips`. A count that
        // outlived its month would caption October with September's sales,
        // which is the defect those two were already fixed for.
        XCTAssertEqual(haul(monthSold: 2, writtenIn: 9).monthSold(at: day(9, 30)), 2)
        XCTAssertEqual(haul(monthSold: 2, writtenIn: 9).monthSold(at: day(10, 1)), 0)
    }

    func test_aBlobFromBeforeThisFieldExistedReadsAsZero() throws {
        // v2 on disk, v3 in the binary: an upgrading install must not render a
        // sold count it never wrote.
        let old = #"{"itemCount":3,"monthFlips":2}"#.data(using: .utf8)!
        let decoded = try JSONDecoder().decode(WidgetHaulData.self, from: old)
        XCTAssertEqual(decoded.monthSold, 0)
        XCTAssertEqual(decoded.monthFlips, 2)
    }
}

// MARK: - Tag mascot assets

/// A missing or misnamed imageset fails silently — `Image("TagHappy")` just
/// draws nothing — and a missing Dark slot falls back to the light artwork,
/// whose espresso outline vanishes on the dark ground. Both are checked here.
final class TagMascotAssetTests: XCTestCase {

    func test_everyMascotImage_resolvesInLightAndDark_andTheyDiffer() {
        let names = TagMascot.Mood.allCases.map(\.assetName) + [TagMascot.blinkAssetName]
        let light = UITraitCollection(userInterfaceStyle: .light)
        let dark = UITraitCollection(userInterfaceStyle: .dark)
        for name in names {
            let l = UIImage(named: name, in: .main, compatibleWith: light)
            let d = UIImage(named: name, in: .main, compatibleWith: dark)
            XCTAssertNotNil(l, "\(name): no light artwork")
            XCTAssertNotNil(d, "\(name): no dark artwork")
            XCTAssertNotEqual(l?.pngData(), d?.pngData(), "\(name): dark artwork is the light one")
        }
    }
}

// MARK: - Listing photo cleanup (#91)

/// Vision's foreground mask cannot run in the simulator ("Could not create
/// inference context"), so these tests feed the pipeline fixed masks and check
/// everything around the model: orientation, cropping, canvas shape, backdrop
/// and the fallback. The one test that calls Vision checks that it degrades,
/// never that it segments. Segmentation quality and the 1.5 s target are
/// device checks. Photos: tools/make_listing_test_photos.py.
final class ListingPhotoCleanupTests: XCTestCase {

    /// White ellipse over the middle half of the image.
    struct EllipseMasker: ForegroundMasking {
        func mask(for image: CGImage) throws -> CGImage? {
            let w = image.width, h = image.height
            let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                                space: CGColorSpaceCreateDeviceGray(),
                                bitmapInfo: CGImageAlphaInfo.none.rawValue)!
            ctx.setFillColor(gray: 0, alpha: 1)
            ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
            ctx.setFillColor(gray: 1, alpha: 1)
            ctx.fillEllipse(in: CGRect(x: w / 4, y: h / 4, width: w / 2, height: h / 2))
            return ctx.makeImage()
        }
    }

    /// The whole mask at one grey level.
    struct FlatMasker: ForegroundMasking {
        let level: CGFloat
        func mask(for image: CGImage) throws -> CGImage? {
            let ctx = CGContext(data: nil, width: image.width, height: image.height, bitsPerComponent: 8,
                                bytesPerRow: 0, space: CGColorSpaceCreateDeviceGray(),
                                bitmapInfo: CGImageAlphaInfo.none.rawValue)!
            ctx.setFillColor(gray: level, alpha: 1)
            ctx.fill(CGRect(x: 0, y: 0, width: image.width, height: image.height))
            return ctx.makeImage()
        }
    }

    /// One white pixel: a speck, well under `minimumCoverage`.
    struct SpeckMasker: ForegroundMasking {
        func mask(for image: CGImage) throws -> CGImage? {
            let ctx = CGContext(data: nil, width: image.width, height: image.height, bitsPerComponent: 8,
                                bytesPerRow: 0, space: CGColorSpaceCreateDeviceGray(),
                                bitmapInfo: CGImageAlphaInfo.none.rawValue)!
            ctx.setFillColor(gray: 1, alpha: 1)
            ctx.fill(CGRect(x: image.width / 2, y: image.height / 2, width: 1, height: 1))
            return ctx.makeImage()
        }
    }

    struct NoSubjectMasker: ForegroundMasking {
        func mask(for image: CGImage) throws -> CGImage? { nil }
    }

    struct FailingMasker: ForegroundMasking {
        struct Boom: Error {}
        func mask(for image: CGImage) throws -> CGImage? { throw Boom() }
    }

    private func photos() throws -> [(name: String, image: UIImage)] {
        let folder = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "ListingPhotos", withExtension: nil),
                                   "ListingPhotos folder missing from the test bundle")
        let files = try FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "jpg" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
        return try files.map { url in
            (url.lastPathComponent, try XCTUnwrap(UIImage(contentsOfFile: url.path), url.lastPathComponent))
        }
    }

    /// The colour of the pixel at the top-left corner of `image`.
    private func cornerRGB(_ image: UIImage) throws -> (Int, Int, Int) {
        let cg = try XCTUnwrap(image.cgImage)
        var px = [UInt8](repeating: 0, count: 4)
        let ctx = try XCTUnwrap(CGContext(data: &px, width: 1, height: 1, bitsPerComponent: 8, bytesPerRow: 4,
                                          space: CGColorSpaceCreateDeviceRGB(),
                                          bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue))
        // Draw so the image's top-left pixel lands on the 1×1 context.
        ctx.draw(cg, in: CGRect(x: 0, y: -(cg.height - 1), width: cg.width, height: cg.height))
        return (Int(px[0]), Int(px[1]), Int(px[2]))
    }

    func test_tenPhotos_areInTheBundle() throws {
        XCTAssertEqual(try photos().count, 10)
    }

    func test_canvas_perMarketplace() {
        XCTAssertEqual(ListingPhotoCanvas(marketplace: .depop), .portrait4x5)
        XCTAssertEqual(ListingPhotoCanvas(marketplace: .poshmark), .square)
        XCTAssertEqual(ListingPhotoCanvas(marketplace: .mercari), .square)
        for marketplace in Marketplace.allCases where marketplace != .depop {
            XCTAssertEqual(ListingPhotoCanvas(marketplace: marketplace), .square, marketplace.rawValue)
        }
        XCTAssertEqual(ListingPhotoCanvas.square.pixelSize, CGSize(width: 1080, height: 1080))
        XCTAssertEqual(ListingPhotoCanvas.portrait4x5.pixelSize, CGSize(width: 1080, height: 1350))
    }

    /// Every photo × every marketplace × both backdrops: no crash, the exact
    /// canvas size, and the backdrop showing at the corner.
    func test_everyPhoto_composesAtTheMarketplacesShape_onTheChosenBackdrop() async throws {
        for (name, photo) in try photos() {
            let cut = try await ListingPhotoCleanup.cutOut(photo, masker: EllipseMasker()).get()
            for marketplace in Marketplace.allCases {
                let canvas = ListingPhotoCanvas(marketplace: marketplace)
                for backdrop in ListingPhotoBackdrop.allCases {
                    let out = ListingPhotoCleanup.compose(cut, canvas: canvas, backdrop: backdrop)
                    let cg = try XCTUnwrap(out.cgImage, name)
                    XCTAssertEqual(CGSize(width: cg.width, height: cg.height), canvas.pixelSize,
                                   "\(name) for \(marketplace.rawValue)")
                    var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
                    backdrop.color.getRed(&r, green: &g, blue: &b, alpha: &a)
                    let corner = try cornerRGB(out)
                    XCTAssertEqual(corner.0, Int((r * 255).rounded()), accuracy: 2, "\(name) \(backdrop)")
                    XCTAssertEqual(corner.1, Int((g * 255).rounded()), accuracy: 2, "\(name) \(backdrop)")
                    XCTAssertEqual(corner.2, Int((b * 255).rounded()), accuracy: 2, "\(name) \(backdrop)")
                }
            }
        }
    }

    /// `UIImage.cgImage` ignores orientation; the cut-out must not. The
    /// EXIF-rotated photo is stored 1024×768 and displays 768×1024.
    func test_exifRotatedPhoto_isMaskedUpright() throws {
        let rotated = try XCTUnwrap(try photos().first { $0.name.contains("exif") }).image
        XCTAssertEqual(rotated.imageOrientation, .right)
        let upright = try XCTUnwrap(ListingPhotoCleanup.uprightCGImage(rotated))
        XCTAssertEqual(upright.width, 768)
        XCTAssertEqual(upright.height, 1024)
    }

    func test_cutOut_isCroppedToTheSubject() throws {
        let image = try XCTUnwrap(ListingPhotoCleanup.uprightCGImage(try photos()[0].image))
        let cut = try ListingPhotoCleanup.cutOut(image, mask: XCTUnwrap(EllipseMasker().mask(for: image))).get()
        XCTAssertEqual(Double(cut.image.width), Double(image.width) / 2, accuracy: 3)
        XCTAssertEqual(Double(cut.image.height), Double(image.height) / 2, accuracy: 3)
    }

    /// The fallback: every way a mask can be unusable leaves the original.
    func test_unusableMasks_fallBack() async throws {
        let photo = try photos()[0].image
        func outcome(_ masker: any ForegroundMasking) async -> ListingPhotoCleanup.Fallback? {
            if case .failure(let reason) = await ListingPhotoCleanup.cutOut(photo, masker: masker) { return reason }
            return nil
        }
        let none = await outcome(NoSubjectMasker())
        let failing = await outcome(FailingMasker())
        let black = await outcome(FlatMasker(level: 0))
        let white = await outcome(FlatMasker(level: 1))
        let speck = await outcome(SpeckMasker())
        XCTAssertEqual(none, .noSubject)
        XCTAssertEqual(failing, .failed)
        XCTAssertEqual(black, .noSubject)
        XCTAssertEqual(white, .lowConfidence, "a mask of the whole frame separates nothing")
        XCTAssertEqual(speck, .lowConfidence)
    }

    /// The real Vision path, on all ten photos: whatever it returns, it must
    /// return rather than crash. In the simulator it is always `.failed`.
    func test_visionMasker_neverCrashes() async throws {
        for (name, photo) in try photos() {
            switch await ListingPhotoCleanup.cutOut(photo) {
            case .success(let cut):
                XCTAssertGreaterThan(cut.image.width, 0, name)
            case .failure(let reason):
                XCTAssertTrue([.failed, .noSubject, .lowConfidence].contains(reason), name)
            }
        }
    }

    @MainActor
    func test_viewModel_keepsTheOriginal_andReshapesForDepop() async throws {
        let photo = try photos()[0].image
        let vm = ResultViewModel()

        await vm.cleanUpPhoto(photo, masker: NoSubjectMasker())
        XCTAssertNil(vm.cleanedPhoto)
        XCTAssertNotNil(vm.photoCleanupNote, "a fallback must say so")

        await vm.cleanUpPhoto(photo, masker: EllipseMasker())
        XCTAssertNil(vm.photoCleanupNote)
        XCTAssertEqual(vm.cleanedPhoto?.cgImage?.height, 1080)

        vm.selectMarketplace(.depop)
        XCTAssertEqual(vm.cleanedPhoto?.cgImage?.height, 1350, "Depop re-shapes to 4:5 without re-running Vision")

        vm.photoBackdrop = .softGrey
        XCTAssertEqual(vm.cleanedPhoto?.cgImage?.height, 1350)
    }
}

// MARK: - Referrals (#97)

/// The client half of `backend/referral.py`: decoding its responses, wording
/// its failures, and announcing each earned week once.
final class ReferralTests: XCTestCase {

    func test_decodesTheServersStatus() throws {
        let json = """
        {"enabled": true, "code": "K7Q2MX", "share_url": "https://www.snapworth.eu/i/K7Q2MX",
         "rewards": [{"code": "APPLE1", "redeem_url": "https://apps.apple.com/redeem?ctx=offercodes&id=1&code=APPLE1",
                      "earned_at": 1790000000}],
         "rewards_left_this_year": 4}
        """.data(using: .utf8)!
        let status = try JSONDecoder().decode(ReferralStatus.self, from: json)
        XCTAssertTrue(status.enabled)
        XCTAssertEqual(status.code, "K7Q2MX")
        XCTAssertEqual(status.rewards.first?.redeemURL.absoluteString.hasSuffix("code=APPLE1"), true)
        XCTAssertEqual(status.rewardsLeftThisYear, 4)
    }

    /// What the server sends while the flag is off: every surface must hide.
    func test_decodesTheDisabledStatus() throws {
        let json = #"{"enabled": false, "code": null, "share_url": null, "rewards": [], "rewards_left_this_year": null}"#
        XCTAssertEqual(try JSONDecoder().decode(ReferralStatus.self, from: Data(json.utf8)), .disabled)
    }

    func test_claimFailuresAreWordedByStatus_notByTheServersEnglish() {
        XCTAssertEqual(ReferralClaimError.from(status: 404), .unknownCode)
        XCTAssertEqual(ReferralClaimError.from(status: 400), .ownCode)
        XCTAssertEqual(ReferralClaimError.from(status: 409), .alreadyUsed)
        XCTAssertEqual(ReferralClaimError.from(status: 429), .tooManyTries)
        XCTAssertEqual(ReferralClaimError.from(status: 503), .paused)
        XCTAssertEqual(ReferralClaimError.from(status: 500), .other)
        for error in [ReferralClaimError.unknownCode, .ownCode, .alreadyUsed, .tooManyTries, .paused, .other] {
            XCTAssertFalse(error.errorDescription?.isEmpty ?? true)
        }
    }

    func test_shareMessageCarriesTheCodeAndTheLink() throws {
        let message = try XCTUnwrap(ReferralAPIClient.mockStatus.shareMessage)
        XCTAssertTrue(message.contains("K7Q2MX"))
        XCTAssertTrue(message.contains("https://www.snapworth.eu/i/K7Q2MX"))
        XCTAssertNil(ReferralStatus.disabled.shareMessage)
    }

    func test_eachEarnedWeekIsAnnouncedOnce() throws {
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "ReferralTests-\(UUID().uuidString)"))
        let url = URL(string: "https://apps.apple.com/redeem")!
        let first = ReferralStatus.Reward(code: "A1", redeemURL: url, earnedAt: 1)
        let second = ReferralStatus.Reward(code: "B2", redeemURL: url, earnedAt: 2)

        XCTAssertEqual(ReferralRewardNotice.unannounced([first], defaults: defaults), [first])
        ReferralRewardNotice.markAnnounced([first], defaults: defaults)
        XCTAssertEqual(ReferralRewardNotice.unannounced([first, second], defaults: defaults), [second])
        ReferralRewardNotice.markAnnounced([second], defaults: defaults)
        XCTAssertEqual(ReferralRewardNotice.unannounced([first, second], defaults: defaults), [])
    }
}

// MARK: - Embedded extensions

/// A version bump now touches twelve build-setting slots: app, widgets and
/// stickers, each in Debug and Release. App Store Connect flags an extension
/// whose version or build differs from the app's, and no build notices, so a
/// missed slot is caught here instead.
final class ExtensionBundleTests: XCTestCase {

    private func embedded(_ name: String) throws -> Bundle {
        let plugIns = try XCTUnwrap(Bundle.main.builtInPlugInsURL)
        return try XCTUnwrap(Bundle(url: plugIns.appendingPathComponent(name)), "\(name) is not embedded in the app")
    }

    private func info(_ bundle: Bundle, _ key: String) -> String? {
        bundle.object(forInfoDictionaryKey: key) as? String
    }

    func test_everyExtension_carriesTheAppsVersionAndBuild() throws {
        let version = try XCTUnwrap(info(.main, "CFBundleShortVersionString"))
        let build = try XCTUnwrap(info(.main, "CFBundleVersion"))
        for name in ["SnapWorthWidgetsExtension.appex", "SnapWorthStickers.appex"] {
            let ext = try embedded(name)
            XCTAssertEqual(info(ext, "CFBundleShortVersionString"), version, name)
            XCTAssertEqual(info(ext, "CFBundleVersion"), build, name)
        }
    }

    /// The media context is what puts the stickers in the system sticker
    /// drawer (the emoji keyboard, the Messages camera, FaceTime) rather than
    /// only in the list of apps inside Messages.
    func test_stickerPack_offersTheSystemStickerDrawer() throws {
        let contexts = try embedded("SnapWorthStickers.appex")
            .object(forInfoDictionaryKey: "MSSupportedPresentationContexts") as? [String]
        XCTAssertEqual(Set(contexts ?? []), ["MSMessagesAppPresentationContextMessages",
                                             "MSMessagesAppPresentationContextMedia"])
    }
}
