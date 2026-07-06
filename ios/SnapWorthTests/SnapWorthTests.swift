import XCTest
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

// MARK: - ScanViewModel Error Mapping Tests

@MainActor
final class ScanViewModelErrorTests: XCTestCase {

    var vm: ScanViewModel!

    override func setUp() {
        super.setUp()
        vm = ScanViewModel()
    }

    func test_friendlyError_rateLimitMessage() {
        let error = makeError("429 rate limit exceeded")
        XCTAssertTrue(vm.friendlyError(error).lowercased().contains("limit"))
    }

    func test_friendlyError_networkOffline() {
        let error = makeError("network connection offline")
        let msg = vm.friendlyError(error)
        XCTAssertTrue(msg.lowercased().contains("internet") || msg.lowercased().contains("network"))
    }

    func test_friendlyError_timeout() {
        let error = makeError("request timed out")
        XCTAssertTrue(vm.friendlyError(error).lowercased().contains("timed out") ||
                      vm.friendlyError(error).lowercased().contains("timeout"))
    }

    func test_friendlyError_502() {
        let error = makeError("502 bad gateway")
        XCTAssertTrue(vm.friendlyError(error).lowercased().contains("unavailable"))
    }

    func test_friendlyError_500() {
        let error = makeError("500 internal server error")
        XCTAssertTrue(vm.friendlyError(error).lowercased().contains("wrong"))
    }

    func test_friendlyError_unknown_returnsGeneric() {
        let error = makeError("some completely unexpected thing happened")
        XCTAssertFalse(vm.friendlyError(error).isEmpty)
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
