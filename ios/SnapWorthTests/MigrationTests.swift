import XCTest
import SwiftData
@testable import SnapWorth

// MARK: - SwiftData 1.1.x -> 1.2.x migration
//
// Why this exists
// ---------------
// 1.2.0 added seven properties to `ScanResult` — the Thrift Flip ledger
// (`statusRaw`, `listedDate`, `soldPrice`, `soldDate`, `feesEstimate`, `notes`)
// and `conditionRaw`. All seven are optional, which is what makes the migration
// additive and lightweight, and there was no test proving it.
//
// The consequence of getting this wrong is the worst one this app has: if the
// store fails to open, `SnapWorthApp` falls back to an in-memory container and
// every scan the user ever saved appears to have vanished. That fallback is the
// right call — it beats a crash loop — but it means a broken migration would
// look like silent total data loss to a user upgrading from 1.1.x, and the only
// signal would be the `persistentStoreFallback` analytics event after the fact.
//
// How the old shape is reconstructed
// ----------------------------------
// The repository has no version tags, so the 1.1.x schema was taken from
// `c319d36` ("feat: v1.1 — paid-price spread and QR footer on share card"), the
// last commit touching this model before `4c483cc` ("feat: v1.2"). Its stored
// properties are reproduced verbatim in `LegacySchemaV1_1.ScanResult` below.
//
// This seeds a store with the old model, closes it, reopens it under the
// current schema, and asserts on what comes back — so it exercises the real
// SwiftData migration rather than simulating one.

/// The 1.1.x schema, namespaced.
///
/// The model is nested inside an enum rather than declared at module level, and
/// that detail is load-bearing twice over. SwiftData derives an entity name from
/// the class's *simple* name, so `LegacySchemaV1_1.ScanResult` still writes an
/// entity called `ScanResult` and produces a store byte-compatible with a real
/// 1.1.x build. But a module-level `ScanResult` in the test target would shadow
/// `SnapWorth.ScanResult` for **every file in the module**, not just this one —
/// the first attempt did exactly that and broke the unrelated tests in
/// `SnapWorthTests.swift`, which suddenly resolved `ScanResult` to the legacy
/// type and could not see `formattedRange`, `condition` or `midpointValue`.
///
/// This is the same shape Apple's `VersionedSchema` samples use, for the same
/// reason.
enum LegacySchemaV1_1 {

    /// Stored properties verbatim from `c319d36`. Do not add fields — the whole
    /// point is that it lacks the seven 1.2 introduced.
    @Model
    final class ScanResult {
        var id: UUID = UUID()
        var timestamp: Date = Date()
        var itemName: String = ""
        var brand: String = ""
        var category: String = ""
        var conditionNotes: String = ""
        var valueLow: Double = 0
        var valueHigh: Double = 0
        var confidence: String = ""
        var soldListingsCount: Int = 0
        var listingTitle: String = ""
        var listingDescription: String = ""
        @Attribute(.externalStorage) var imageData: Data?
        var paidPrice: Double?

        init(id: UUID = UUID(), timestamp: Date = Date(), itemName: String,
             brand: String, category: String, conditionNotes: String,
             valueLow: Double, valueHigh: Double, confidence: String,
             soldListingsCount: Int, listingTitle: String,
             listingDescription: String, imageData: Data? = nil,
             paidPrice: Double? = nil) {
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
        }
    }
}

final class SwiftDataMigrationTests: XCTestCase {

    private var directory: URL!
    private var storeURL: URL!

    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(
            at: directory, withIntermediateDirectories: true)
        storeURL = directory.appendingPathComponent("default.store")
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: directory)
    }

    /// Writes a store containing 1.1.x-shaped rows and closes it.
    @discardableResult
    private func seedLegacyStore() throws -> [UUID] {
        let schema = Schema([LegacySchemaV1_1.ScanResult.self])
        let config = ModelConfiguration(schema: schema, url: storeURL)
        let container = try ModelContainer(for: schema, configurations: [config])
        let context = ModelContext(container)

        let rows = [
            LegacySchemaV1_1.ScanResult(itemName: "Patagonia Better Sweater", brand: "Patagonia",
                       category: "clothing", conditionNotes: "Good — light pilling",
                       valueLow: 45, valueHigh: 90, confidence: "High",
                       soldListingsCount: 38,
                       listingTitle: "Patagonia Better Sweater Fleece",
                       listingDescription: "Great used condition.",
                       imageData: Data("fake-jpeg".utf8), paidPrice: 12.50),
            LegacySchemaV1_1.ScanResult(itemName: "Levi's 501", brand: "Levi's",
                       category: "clothing", conditionNotes: "Very Good",
                       valueLow: 28, valueHigh: 55, confidence: "High",
                       soldListingsCount: 12,
                       listingTitle: "Levi's 501 32x32",
                       listingDescription: "Authentic.",
                       imageData: nil, paidPrice: nil),
        ]
        rows.forEach { context.insert($0) }
        try context.save()
        return rows.map(\.id)
    }

    /// Reopens the seeded store under the CURRENT schema.
    private func openUnderCurrentSchema() throws -> [SnapWorth.ScanResult] {
        let schema = Schema([SnapWorth.ScanResult.self])
        let config = ModelConfiguration(schema: schema, url: storeURL)
        let container = try ModelContainer(for: schema, configurations: [config])
        let context = ModelContext(container)
        return try context.fetch(
            FetchDescriptor<SnapWorth.ScanResult>(
                sortBy: [SortDescriptor(\.itemName)]))
    }

    // MARK: The migration itself

    func test_legacyStoreOpensUnderCurrentSchemaWithoutThrowing() throws {
        try seedLegacyStore()
        // The assertion is that this does not throw. If it does, the shipping
        // app would fall back to an in-memory container and the user's entire
        // history would appear to be gone.
        XCTAssertNoThrow(try openUnderCurrentSchema())
    }

    func test_existingScansSurviveTheMigration() throws {
        let seeded = try seedLegacyStore()
        let migrated = try openUnderCurrentSchema()

        XCTAssertEqual(migrated.count, 2, "scans were lost during migration")
        XCTAssertEqual(Set(migrated.map(\.id)), Set(seeded),
                       "identities changed — these would be different rows to the user")
    }

    func test_legacyFieldValuesArePreservedExactly() throws {
        try seedLegacyStore()
        let migrated = try openUnderCurrentSchema()
        let sweater = try XCTUnwrap(migrated.first { $0.brand == "Patagonia" })

        XCTAssertEqual(sweater.itemName, "Patagonia Better Sweater")
        XCTAssertEqual(sweater.category, "clothing")
        XCTAssertEqual(sweater.conditionNotes, "Good — light pilling")
        XCTAssertEqual(sweater.valueLow, 45)
        XCTAssertEqual(sweater.valueHigh, 90)
        XCTAssertEqual(sweater.confidence, "High")
        XCTAssertEqual(sweater.soldListingsCount, 38)
        XCTAssertEqual(sweater.listingTitle, "Patagonia Better Sweater Fleece")
        XCTAssertEqual(sweater.listingDescription, "Great used condition.")
        XCTAssertEqual(sweater.paidPrice, 12.50)
    }

    func test_externalStorageImageDataSurvives() throws {
        // imageData is @Attribute(.externalStorage), so it lives outside the
        // store file. A migration that moved the store without its support
        // directory would lose every photo while keeping every row.
        try seedLegacyStore()
        let migrated = try openUnderCurrentSchema()
        let sweater = try XCTUnwrap(migrated.first { $0.brand == "Patagonia" })
        XCTAssertEqual(sweater.imageData, Data("fake-jpeg".utf8))

        let jeans = try XCTUnwrap(migrated.first { $0.brand == "Levi's" })
        XCTAssertNil(jeans.imageData, "a nil image became non-nil")
    }

    // MARK: The new 1.2 fields

    func test_newOptionalFieldsDefaultToNil() throws {
        try seedLegacyStore()
        let migrated = try openUnderCurrentSchema()

        for row in migrated {
            XCTAssertNil(row.statusRaw, "statusRaw should be absent on migrated rows")
            XCTAssertNil(row.listedDate)
            XCTAssertNil(row.soldPrice)
            XCTAssertNil(row.soldDate)
            XCTAssertNil(row.feesEstimate)
            XCTAssertNil(row.notes)
            XCTAssertNil(row.conditionRaw)
        }
    }

    func test_derivedAccessorsDegradeSensiblyOnMigratedRows() throws {
        // The computed accessors read the new raw fields. On a migrated row
        // those are nil, so this checks the fallbacks hold rather than
        // trapping — a crash here would hit every 1.1.x upgrader on the
        // history screen, which is the first thing they would see.
        try seedLegacyStore()
        let migrated = try openUnderCurrentSchema()
        let sweater = try XCTUnwrap(migrated.first { $0.brand == "Patagonia" })

        XCTAssertEqual(sweater.status, .scanned,
                       "a row with no status should read as freshly scanned")
        XCTAssertFalse(sweater.formattedRange.isEmpty)
        XCTAssertGreaterThan(sweater.midpointValue, 0)
        // condition falls back to inference from the notes rather than trapping.
        XCTAssertNotNil(sweater.condition)
    }

    func test_migratedStoreAcceptsWritesToTheNewFields() throws {
        // Migration is only useful if the upgraded row is then usable: a 1.1.x
        // scan must be able to enter the Flip ledger that 1.2 introduced.
        try seedLegacyStore()

        let schema = Schema([SnapWorth.ScanResult.self])
        let config = ModelConfiguration(schema: schema, url: storeURL)
        let container = try ModelContainer(for: schema, configurations: [config])
        let context = ModelContext(container)

        let rows = try context.fetch(FetchDescriptor<SnapWorth.ScanResult>())
        let row = try XCTUnwrap(rows.first)
        row.soldPrice = 75
        row.notes = "sold at the flea market"
        XCTAssertNoThrow(try context.save())

        let reread = try openUnderCurrentSchema()
        XCTAssertTrue(reread.contains { $0.soldPrice == 75 })
    }

    // MARK: The 1.2.2 portfolio fields

    func test_portfolioFieldsDefaultToNilAfterMigration() throws {
        // Same rule as the 1.2 fields: additive and optional, so a store written
        // before they existed opens without a rewrite.
        try seedLegacyStore()
        for row in try openUnderCurrentSchema() {
            XCTAssertNil(row.portfolioValueRaw)
            XCTAssertNil(row.valueHistoryData)
        }
    }

    func test_migratedRowsStillPriceCorrectly() throws {
        // The one that would silently under-report a long-standing user's
        // portfolio if `nil` were treated as zero.
        try seedLegacyStore()
        let migrated = try openUnderCurrentSchema()
        let sweater = try XCTUnwrap(migrated.first { $0.brand == "Patagonia" })

        XCTAssertNil(sweater.portfolioValueRaw, "nothing stored yet")
        XCTAssertEqual(sweater.portfolioValue,
                       sweater.priceRange(for: sweater.condition).likely)
        XCTAssertGreaterThan(sweater.portfolioValue, 0)
    }

    func test_portfolioTotalOfAMigratedStoreIsNotZero() throws {
        try seedLegacyStore()
        let migrated = try openUnderCurrentSchema()
        let total = HistoryViewModel.total(of: migrated.map(\.portfolioValue))
        XCTAssertGreaterThan(total, 0,
                             "a pre-1.2.2 library must report a real total, not zero")
    }

    func test_migratedRowAcceptsAPortfolioRefresh() throws {
        try seedLegacyStore()
        let schema = Schema([SnapWorth.ScanResult.self])
        let config = ModelConfiguration(schema: schema, url: storeURL)
        let container = try ModelContainer(for: schema, configurations: [config])
        let context = ModelContext(container)
        let row = try XCTUnwrap(try context.fetch(
            FetchDescriptor<SnapWorth.ScanResult>()).first)

        row.refreshPortfolioValue()
        XCTAssertNoThrow(try context.save())
        XCTAssertNotNil(row.portfolioValueRaw)
        XCTAssertEqual(row.valueHistory.count, 1)
    }
}

// MARK: - Portfolio value (1.2.2)
//
// The portfolio total is the headline number on My Finds, so its arithmetic is
// worth pinning directly rather than only through the view.
//
// Two properties matter more than the happy path:
//
//   * A row written before 1.2.2 has no stored value. It must price from
//     valueLow/valueHigh exactly as it always did — nil is not zero. Getting
//     that wrong would silently under-report every long-standing user's
//     portfolio, which is the one number this feature exists to show.
//   * The history is append-only and deduplicated, so re-opening an item
//     cannot inflate it.

final class PortfolioValueTests: XCTestCase {

    private func make(low: Double, high: Double, notes: String = "Good") -> ScanResult {
        ScanResult(itemName: "Item", brand: "B", category: "clothing",
                   conditionNotes: notes, valueLow: low, valueHigh: high,
                   confidence: "High", soldListingsCount: 0,
                   listingTitle: "T", listingDescription: "D")
    }

    // ── The nil-is-not-zero rule ───────────────────────────────────────────

    func test_rowWithNoStoredValue_pricesFromItsBounds() {
        let r = make(low: 40, high: 60)
        XCTAssertNil(r.portfolioValueRaw, "precondition: nothing stored yet")
        XCTAssertEqual(r.portfolioValue, r.priceRange(for: r.condition).likely,
                       "a pre-1.2.2 row must price exactly as it always did")
        XCTAssertNotEqual(r.portfolioValue, 0, "nil must not read as zero")
    }

    func test_storedValueIsPreferredOverRecomputing() {
        let r = make(low: 40, high: 60)
        r.portfolioValueRaw = 123
        XCTAssertEqual(r.portfolioValue, Decimal(123))
    }

    func test_nonFiniteStoredValueFallsBack() {
        // A corrupt column must not poison the total with NaN or infinity.
        let r = make(low: 40, high: 60)
        r.portfolioValueRaw = Double.nan
        XCTAssertEqual(r.portfolioValue, r.priceRange(for: r.condition).likely)
        r.portfolioValueRaw = .infinity
        XCTAssertEqual(r.portfolioValue, r.priceRange(for: r.condition).likely)
    }

    // ── Aggregation ────────────────────────────────────────────────────────

    func test_totalOfEmptyPortfolioIsZero() {
        XCTAssertEqual(HistoryViewModel.total(of: []), 0)
    }

    func test_totalOfSingleItem() {
        XCTAssertEqual(HistoryViewModel.total(of: [Decimal(42)]), 42)
    }

    func test_totalIsExactAcrossManyItems() {
        // The reason this moved to Decimal: 0.10 has no exact binary
        // representation, so summing it 1,000 times in Double drifts. Decimal
        // must land on exactly 100.
        let values = Array(repeating: Decimal(string: "0.10")!, count: 1000)
        XCTAssertEqual(HistoryViewModel.total(of: values), Decimal(100))
    }

    func test_totalHandlesALargeLibrary() {
        let values = (1...5000).map { Decimal($0) }
        XCTAssertEqual(HistoryViewModel.total(of: values), Decimal(5000 * 5001 / 2))
    }

    // ── History ────────────────────────────────────────────────────────────

    func test_firstRefreshSeedsOnePoint() {
        let r = make(low: 40, high: 60)
        r.refreshPortfolioValue()
        XCTAssertEqual(r.valueHistory.count, 1)
        XCTAssertNotNil(r.portfolioValueRaw)
    }

    func test_repeatedRefreshWithNoChangeDoesNotGrowHistory() {
        let r = make(low: 40, high: 60)
        r.refreshPortfolioValue()
        r.refreshPortfolioValue()
        r.refreshPortfolioValue()
        XCTAssertEqual(r.valueHistory.count, 1,
                       "re-opening an item must not inflate its history")
    }

    func test_repricingAppendsAPoint() {
        let r = make(low: 40, high: 60, notes: "Good")
        r.refreshPortfolioValue()
        r.condition = .likeNew          // re-prices upward
        r.refreshPortfolioValue()
        XCTAssertEqual(r.valueHistory.count, 2)
        XCTAssertGreaterThan(r.valueHistory[1].value, r.valueHistory[0].value)
    }

    func test_historyIsCapped() {
        let r = make(low: 40, high: 60)
        for i in 1...50 {
            r.valueLow = Double(i); r.valueHigh = Double(i) * 2
            r.refreshPortfolioValue(limit: 10)
        }
        XCTAssertEqual(r.valueHistory.count, 10, "series must stay bounded")
    }

    func test_corruptHistoryReadsAsEmptyRatherThanCrashing() {
        let r = make(low: 40, high: 60)
        r.valueHistoryData = Data("not json".utf8)
        XCTAssertEqual(r.valueHistory, [])
        XCTAssertNil(r.valueChangeSinceAdded)
    }

    func test_changeSinceAddedNeedsTwoPoints() {
        let r = make(low: 40, high: 60)
        r.refreshPortfolioValue()
        XCTAssertNil(r.valueChangeSinceAdded, "one point is not a trend")
        r.condition = .likeNew
        r.refreshPortfolioValue()
        XCTAssertNotNil(r.valueChangeSinceAdded)
    }
}

// MARK: - Portfolio trend + entitlement gating
//
// The commercial shape of this feature: the total is free (it is the reason to
// reopen the app) and the history is Pro. These pin that split, and the trend
// arithmetic underneath it.

final class PortfolioTrendTests: XCTestCase {

    private func pair(_ day: Int, _ value: Decimal) -> (date: Date, value: Decimal) {
        (date: Date(timeIntervalSince1970: TimeInterval(day) * 86_400), value: value)
    }

    // ── Trend arithmetic ───────────────────────────────────────────────────

    func test_emptyPortfolioHasNoTrend() {
        XCTAssertTrue(HistoryViewModel.trend(from: []).isEmpty)
    }

    func test_singleItemIsNotYetATrend() {
        // One point renders nothing — TrendStrip requires two.
        XCTAssertEqual(HistoryViewModel.trend(from: [pair(1, 50)]).count, 1)
    }

    func test_trendAccumulatesInDateOrder() {
        let points = HistoryViewModel.trend(from: [pair(3, 30), pair(1, 10), pair(2, 20)])
        XCTAssertEqual(points.map(\.total), [10, 30, 60],
                       "must accumulate oldest-first regardless of input order")
    }

    func test_finalPointEqualsThePortfolioTotal() {
        // The line has to end where the headline number is, or the two disagree
        // on screen.
        let values: [Decimal] = [12, 40, 3, 99]
        let points = HistoryViewModel.trend(from: values.enumerated().map { pair($0.offset, $0.element) })
        XCTAssertEqual(points.last?.total, HistoryViewModel.total(of: values))
    }

    func test_longHistoryIsDownsampledButKeepsTheEndpoints() {
        let pairs = (0..<500).map { pair($0, Decimal(1)) }
        let points = HistoryViewModel.trend(from: pairs, maxPoints: 40)
        XCTAssertEqual(points.count, 40, "sparkline must stay bounded")
        XCTAssertEqual(points.first?.total, 1)
        XCTAssertEqual(points.last?.total, 500, "the current total must survive downsampling")
    }

    func test_downsamplingIsSkippedWhenUnnecessary() {
        let pairs = (0..<10).map { pair($0, Decimal(5)) }
        XCTAssertEqual(HistoryViewModel.trend(from: pairs, maxPoints: 40).count, 10)
    }

    // ── Entitlement gating ─────────────────────────────────────────────────

    @MainActor
    func test_freeUserIsNotEntitled() {
        let service = MockPurchaseService(forcedSubscribed: false)
        XCTAssertFalse(service.isSubscribed,
                       "the trend must be gated for a non-subscriber")
    }

    @MainActor
    func test_proUserIsEntitled() {
        let service = MockPurchaseService(forcedSubscribed: true)
        XCTAssertTrue(service.isSubscribed)
    }

    @MainActor
    func test_totalIsAvailableRegardlessOfEntitlement() {
        // The hook stays free. If this ever starts depending on isSubscribed,
        // the feature has lost the thing that makes people come back.
        let vm = HistoryViewModel()
        let values: [Decimal] = [10, 20, 30]
        let total = HistoryViewModel.total(of: values)
        XCTAssertEqual(total, 60)
        XCTAssertFalse(vm.totalValue(from: []).isEmpty,
                       "an empty portfolio still renders a figure, not a blank")
    }

    @MainActor
    func test_changeLabelIsNilUntilAnItemIsRepriced() {
        let vm = HistoryViewModel()
        let r = ScanResult(itemName: "I", brand: "B", category: "c",
                           conditionNotes: "Good", valueLow: 10, valueHigh: 20,
                           confidence: "High", soldListingsCount: 0,
                           listingTitle: "T", listingDescription: "D")
        r.refreshPortfolioValue()
        XCTAssertNil(vm.changeLabel(for: r))

        r.condition = .likeNew
        r.refreshPortfolioValue()
        let label = vm.changeLabel(for: r)
        XCTAssertNotNil(label, "a re-priced item must report its change")
        XCTAssertTrue(label?.hasPrefix("+") == true,
                      "an upward re-price must read as a gain")
    }
}

// MARK: - Portfolio insights
//
// The header line is the only thing on My Finds that changes between visits
// without a new scan, so its rules matter: it must be true, actionable, and
// silent when there is nothing to say.

final class PortfolioInsightsTests: XCTestCase {

    private func item(_ status: FlipStatus, daysAgo: Int = 1,
                      paid: Double? = nil, sold: Double? = nil,
                      fees: Double? = nil) -> ScanResult {
        let r = ScanResult(timestamp: Date().addingTimeInterval(TimeInterval(-daysAgo) * 86_400),
                           itemName: "I", brand: "B", category: "c",
                           conditionNotes: "Good", valueLow: 40, valueHigh: 60,
                           confidence: "High", soldListingsCount: 0,
                           listingTitle: "T", listingDescription: "D",
                           paidPrice: paid, soldPrice: sold, feesEstimate: fees)
        r.status = status
        return r
    }

    func test_emptyPortfolioSaysNothing() {
        XCTAssertNil(HistoryViewModel.insightLine(HistoryViewModel.insights(for: [])))
    }

    func test_unlistedFindsAreCountedAndLeadTheLine() {
        let i = HistoryViewModel.insights(for: [item(.scanned), item(.scanned), item(.sold)])
        XCTAssertEqual(i.unlisted, 2)
        XCTAssertEqual(HistoryViewModel.insightLine(i), "2 finds you haven't listed yet")
    }

    func test_singleUnlistedUsesSingular() {
        let i = HistoryViewModel.insights(for: [item(.scanned)])
        XCTAssertEqual(HistoryViewModel.insightLine(i), "1 find you haven't listed yet")
    }

    func test_soldItemsCountTowardRealisedNotUnrealised() {
        // Sold: paid 20, sold 100, fees 10 -> realised 70. It must not also be
        // counted as value still held.
        let i = HistoryViewModel.insights(for: [item(.sold, paid: 20, sold: 100, fees: 10)])
        XCTAssertEqual(i.realized, 70)
        XCTAssertEqual(i.unrealized, 0, "a sold item is no longer held")
    }

    func test_saleWithNoCostBasisContributesNothingRatherThanGuessing() {
        // realizedProfit is nil without a paid price. Treating that as zero is
        // right; inventing a cost basis would not be.
        let i = HistoryViewModel.insights(for: [item(.sold, sold: 100)])
        XCTAssertEqual(i.realized, 0)
    }

    func test_heldItemsCountTowardUnrealised() {
        let i = HistoryViewModel.insights(for: [item(.owned), item(.listed)])
        XCTAssertGreaterThan(i.unrealized, 0)
        XCTAssertEqual(i.realized, 0)
    }

    func test_realisedLineAppearsOnceNothingIsUnlisted() {
        let i = HistoryViewModel.insights(for: [
            item(.sold, paid: 20, sold: 100, fees: 10),
            item(.owned)
        ])
        let line = HistoryViewModel.insightLine(i)
        XCTAssertEqual(line?.contains("realised"), true, "got: \(line ?? "nil")")
        XCTAssertEqual(line?.contains("still held"), true)
    }

    func test_oldestHoldIgnoresSoldItems() {
        // A thing sold a year ago is not "held for 365 days".
        let i = HistoryViewModel.insights(for: [
            item(.sold, daysAgo: 365, paid: 10, sold: 20),
            item(.owned, daysAgo: 5)
        ])
        XCTAssertEqual(i.oldestHoldDays, 5)
    }

    func test_ageLineOnlyAppearsAfterAMeaningfulHold() {
        let recent = HistoryViewModel.insights(for: [item(.owned, daysAgo: 3)])
        XCTAssertNil(HistoryViewModel.insightLine(recent),
                     "three days is not worth remarking on")

        let old = HistoryViewModel.insights(for: [item(.owned, daysAgo: 45)])
        XCTAssertEqual(HistoryViewModel.insightLine(old), "Held for 45 days")
    }
}

// MARK: - Weekly portfolio digest
//
// The return hook. What matters here is less the scheduling than the copy
// rules: this is the app speaking to someone who is not currently using it, so
// every sentence has to be true from local data and worth an interruption.
//
// Specifically NOT tested, because it is deliberately not built: any claim that
// an item's value moved. Nothing re-values a saved item — `refreshPortfolioValue`
// runs only on creation and on a user's own condition edit — so "worth $40 more"
// would report the user's edit back as market movement.

final class PortfolioDigestTests: XCTestCase {

    private func scan(_ daysAgo: Int, low: Double = 40, high: Double = 60) -> ScanResult {
        ScanResult(timestamp: Date().addingTimeInterval(TimeInterval(-daysAgo) * 86_400),
                   itemName: "Item", brand: "B", category: "clothing",
                   conditionNotes: "Good", valueLow: low, valueHigh: high,
                   confidence: "High", soldListingsCount: 0,
                   listingTitle: "T", listingDescription: "D")
    }

    // ── When it must stay silent ───────────────────────────────────────────

    func test_emptyPortfolioProducesNoNotification() {
        let digest = NotificationManager.digest(for: [])
        XCTAssertNil(digest.body,
                     "an empty portfolio has nothing worth interrupting someone for")
    }

    // ── Copy correctness ───────────────────────────────────────────────────

    func test_singleItemUsesSingularGrammar() {
        let body = NotificationManager.digest(for: [scan(30)]).body
        XCTAssertEqual(body?.contains("1 find is"), true, "got: \(body ?? "nil")")
        XCTAssertEqual(body?.contains("finds are"), false)
    }

    func test_multipleItemsUsePluralGrammar() {
        let body = NotificationManager.digest(for: [scan(30), scan(31)]).body
        XCTAssertEqual(body?.contains("2 finds are"), true, "got: \(body ?? "nil")")
    }

    func test_recentAdditionsAreCalledOut() {
        // Two added in the last week, one older.
        let body = NotificationManager.digest(for: [scan(1), scan(2), scan(40)]).body
        XCTAssertEqual(body?.hasPrefix("You added 2 finds this week"), true,
                       "got: \(body ?? "nil")")
    }

    func test_oneRecentAdditionIsSingular() {
        let body = NotificationManager.digest(for: [scan(1), scan(40)]).body
        XCTAssertEqual(body?.hasPrefix("You added 1 find this week"), true,
                       "got: \(body ?? "nil")")
    }

    func test_quietWeekReportsStatusWithoutManufacturingUrgency() {
        let body = NotificationManager.digest(for: [scan(40), scan(50)]).body
        XCTAssertEqual(body?.contains("You added"), false,
                       "nothing was added — do not imply otherwise")
        XCTAssertEqual(body?.hasPrefix("Your 2 finds are worth"), true,
                       "got: \(body ?? "nil")")
    }

    func test_bodyCarriesTheRealTotal() {
        let items = [scan(10), scan(11)]
        let expected = HistoryViewModel.money(
            HistoryViewModel.total(of: items.map(\.portfolioValue)))
        XCTAssertEqual(NotificationManager.digest(for: items).body?.contains(expected), true,
                       "the figure in the notification must be the one in the app")
    }

    func test_itemsExactlyAtTheWeekBoundaryCount() {
        // Guards an off-by-one that would silently drop the newest scan.
        let now = Date()
        let justInside = ScanResult(timestamp: now.addingTimeInterval(-7 * 86_400 + 60),
                                    itemName: "I", brand: "B", category: "c",
                                    conditionNotes: "Good", valueLow: 10, valueHigh: 20,
                                    confidence: "High", soldListingsCount: 0,
                                    listingTitle: "T", listingDescription: "D")
        XCTAssertEqual(NotificationManager.digest(for: [justInside], now: now).addedThisWeek, 1)
    }

    // ── Scheduling ─────────────────────────────────────────────────────────

    func test_nextDigestIsAlwaysInTheFuture() {
        let now = Date()
        let next = NotificationManager.nextDigestDate(after: now)
        XCTAssertNotNil(next)
        XCTAssertGreaterThan(next!, now)
    }

    func test_digestFiresOnASundayMorning() throws {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let next = try XCTUnwrap(
            NotificationManager.nextDigestDate(after: Date(), calendar: cal))
        let comps = cal.dateComponents([.weekday, .hour], from: next)
        XCTAssertEqual(comps.weekday, 1, "Sunday")
        XCTAssertEqual(comps.hour, 11)
    }

    func test_portfolioSitsBelowTimeCriticalCategoriesInTheCap() {
        // The daily cap drops the lower priority. A weekly habit nudge must
        // never displace a trial-ending warning.
        XCTAssertLessThan(NotificationManager.Category.portfolio.priority,
                          NotificationManager.Category.trial.priority)
        XCTAssertLessThan(NotificationManager.Category.portfolio.priority,
                          NotificationManager.Category.ledger.priority)
        XCTAssertGreaterThan(NotificationManager.Category.portfolio.priority,
                             NotificationManager.Category.recap.priority)
    }

    func test_portfolioIsIndependentlyToggleable() {
        XCTAssertNotEqual(NotificationManager.Category.portfolio.toggleKey,
                          NotificationManager.Category.recap.toggleKey)
        XCTAssertTrue(NotificationManager.Category.allCases.contains(.portfolio))
    }
}
