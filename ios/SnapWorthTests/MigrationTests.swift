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
}
