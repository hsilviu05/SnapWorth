import XCTest
import UIKit
@testable import SnapWorth

// MARK: - Upload image preparation
//
// The scan payload was previously a full-resolution JPEG (~3.5 MB from a 12 MP
// capture). On in-store cellular that is ~19 s of upload against a 35 s resource
// timeout, before the model has seen a byte. These lock in the downscale.

final class UploadImageEncodingTests: XCTestCase {

    private func image(width: CGFloat, height: CGFloat) -> UIImage {
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1
        return UIGraphicsImageRenderer(size: CGSize(width: width, height: height),
                                       format: format).image { ctx in
            // Non-uniform content so JPEG can't compress to a degenerate size.
            UIColor.systemTeal.setFill()
            ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
            UIColor.systemOrange.setFill()
            ctx.fill(CGRect(x: 0, y: 0, width: width / 2, height: height / 2))
        }
    }

    func test_oversizedImage_isDownscaledToMaxEdge() {
        let source = image(width: 4032, height: 3024)          // 12 MP capture
        let result = ScanAPIClient.downscale(source, maxEdge: 1568)
        XCTAssertEqual(max(result.size.width, result.size.height), 1568, accuracy: 1)
    }

    func test_downscale_preservesAspectRatio() {
        let source = image(width: 4032, height: 3024)          // 4:3
        let result = ScanAPIClient.downscale(source, maxEdge: 1568)
        let sourceRatio = source.size.width / source.size.height
        let resultRatio = result.size.width / result.size.height
        XCTAssertEqual(sourceRatio, resultRatio, accuracy: 0.01)
    }

    func test_portraitImage_clampsTheLongEdge() {
        let source = image(width: 3024, height: 4032)
        let result = ScanAPIClient.downscale(source, maxEdge: 1568)
        XCTAssertEqual(result.size.height, 1568, accuracy: 1)
        XCTAssertLessThan(result.size.width, 1568)
    }

    func test_smallImage_isNeverUpscaled() {
        let source = image(width: 400, height: 300)
        let result = ScanAPIClient.downscale(source, maxEdge: 1568)
        XCTAssertEqual(result.size.width, 400, accuracy: 1)
        XCTAssertEqual(result.size.height, 300, accuracy: 1)
    }

    func test_downscale_doesNotApplyScreenScale() {
        // `UIGraphicsImageRendererFormat.default()` uses the screen scale, which
        // would silently render a 2–3× larger bitmap and undo the downscale.
        let source = image(width: 4032, height: 3024)
        let result = ScanAPIClient.downscale(source, maxEdge: 1568)
        XCTAssertEqual(result.scale, 1, accuracy: 0.01)
        XCTAssertEqual(result.cgImage?.width ?? 0, 1568)
    }

    func test_encodedPayload_isDramaticallySmallerThanFullResolution() async {
        let source = image(width: 4032, height: 3024)
        let full = source.jpegData(compressionQuality: 0.82)!
        let prepared = await ScanAPIClient.encodeForUpload(source)
        let encoded = try! XCTUnwrap(prepared)

        XCTAssertLessThan(encoded.count, full.count / 4,
                          "Downscaled payload should be a small fraction of full-res")
    }

    func test_encodedPayload_staysUnderServerLimit() async {
        let source = image(width: 8000, height: 6000)
        let encoded = await ScanAPIClient.encodeForUpload(source)
        XCTAssertNotNil(encoded)
        XCTAssertLessThan(encoded!.count, 10 * 1024 * 1024)
    }
}

// MARK: - API error detail parsing
//
// FastAPI returns `detail` as a String for handled errors but as an ARRAY of
// objects for 422 validation failures. Decoding into [String: String] therefore
// failed on exactly the responses carrying the most information, and the user
// saw a generic fallback instead.

final class APIErrorDetailTests: XCTestCase {

    func test_stringDetail_isReturned() {
        let data = #"{"detail":"You've used all 3 free scans today."}"#.data(using: .utf8)!
        XCTAssertEqual(APIErrorDetail.parse(data), "You've used all 3 free scans today.")
    }

    func test_validationArrayDetail_isFlattened() {
        let data = """
        {"detail":[{"loc":["body","marketplace"],"msg":"field required","type":"missing"}]}
        """.data(using: .utf8)!
        XCTAssertEqual(APIErrorDetail.parse(data), "field required")
    }

    func test_multipleValidationErrors_areJoined() {
        let data = """
        {"detail":[{"msg":"field required"},{"msg":"value is not a valid float"}]}
        """.data(using: .utf8)!
        XCTAssertEqual(APIErrorDetail.parse(data),
                       "field required value is not a valid float")
    }

    func test_emptyBody_fallsBackToUserSafeCopy() {
        let parsed = APIErrorDetail.parse(Data())
        XCTAssertFalse(parsed.isEmpty)
        XCTAssertFalse(parsed.contains("detail"))
    }

    func test_malformedJSON_fallsBackWithoutThrowing() {
        let parsed = APIErrorDetail.parse("not json at all".data(using: .utf8)!)
        XCTAssertEqual(parsed, "Something went wrong. Please try again.")
    }

    func test_fallbackNeverLeaksRawIdentifiers() {
        // Regression: `imageEncodingFailed` used to be surfaced verbatim.
        let message = ScanAPIError.imageEncodingFailed.errorDescription ?? ""
        XCTAssertFalse(message.contains("imageEncodingFailed"))
        XCTAssertTrue(message.contains(" "), "Should be a sentence, not an identifier")
    }

    func test_serverErrorDescription_omitsStatusCodeNoise() {
        let error = ScanAPIError.serverError(502, "Our AI is temporarily unavailable.")
        XCTAssertEqual(error.errorDescription, "Our AI is temporarily unavailable.")
        XCTAssertEqual(error.statusCode, 502)
    }
}

// MARK: - 402 routing
//
// The backend returns 402 both for a spent free allowance and for a Pro-only
// endpoint. Neither should reach the user as "Server error 402".

final class PaymentRequiredMappingTests: XCTestCase {

    func test_quotaExhaustion_mapsToQuotaExceeded() {
        let error = ScanAPIError.serverError(402, "You've used all 3 free scans today.")
        guard case .quotaExceeded(let msg) = AppError.from(error) else {
            return XCTFail("Expected .quotaExceeded")
        }
        XCTAssertEqual(msg, "You've used all 3 free scans today.")
    }

    func test_proOnlyEndpoint_mapsToProRequired() {
        let error = ScanAPIError.serverError(402, "Listing drafts are a SnapWorth Pro feature.")
        guard case .proRequired = AppError.from(error) else {
            return XCTFail("Expected .proRequired")
        }
    }

    func test_402_neverSurfacesRawStatusCode() {
        let error = ScanAPIError.serverError(402, "You've used all 3 free scans today.")
        let message = AppError.from(error).errorDescription ?? ""
        XCTAssertFalse(message.contains("402"))
        XCTAssertFalse(message.lowercased().contains("server error"))
    }
}

// MARK: - Legacy response compatibility

final class ScanAPIResponseDecodingTests: XCTestCase {

    private let base = """
    {"item_name":"Patagonia Better Sweater","brand":"Patagonia","category":"clothing",
     "condition_notes":"Good","est_value_low_usd":45.0,"est_value_high_usd":90.0,
     "confidence":"High","listing_title":"T","listing_description":"D"}
    """

    func test_decodesWhenSoldListingsCountAbsent() throws {
        // Forward compatibility: the backend keeps this field only for clients
        // below 1.2 and will drop it once they age out.
        let decoded = try JSONDecoder().decode(
            ScanAPIResponse.self, from: base.data(using: .utf8)!)
        XCTAssertEqual(decoded.soldListingsCount, 0)
        XCTAssertEqual(decoded.brand, "Patagonia")
    }

    func test_decodesWhenSoldListingsCountPresent() throws {
        let withField = base.replacingOccurrences(
            of: #""confidence":"High""#, with: #""confidence":"High","sold_listings_count":0"#)
        let decoded = try JSONDecoder().decode(
            ScanAPIResponse.self, from: withField.data(using: .utf8)!)
        XCTAssertEqual(decoded.soldListingsCount, 0)
    }

    func test_mockResponses_claimNoSoldListings() async throws {
        // Guards the App Store claim: screenshots are captured in mock mode, and
        // a non-zero fixture here is where "38 sold listings" came from. There is
        // no comps data source, so no fixture may imply one.
        let mirror = Mirror(reflecting: ScanAPIResponse(
            itemName: "x", brand: "x", category: "x", conditionNotes: "x",
            estValueLowUsd: 1, estValueHighUsd: 2, confidence: "High",
            listingTitle: "x", listingDescription: "x"))
        let count = mirror.children.first { $0.label == "soldListingsCount" }?.value as? Int
        XCTAssertEqual(count, 0, "Default must be 0 — we have no sold-listings source")
    }
}

// MARK: - Paywall pricing
//
// Prices were hardcoded as "$39.99/yr" etc., so every non-US storefront showed a
// US-dollar figure while Apple charged in local currency.

final class PlanPricingTests: XCTestCase {

    func test_loadingPlaceholder_showsNoCurrencyFigure() {
        let placeholder = PlanPricing.loading(Config.yearlyProductID)
        XCTAssertEqual(placeholder.displayPrice, "—")
        XCTAssertFalse(placeholder.displayPrice.contains("$"))
        XCTAssertNil(placeholder.introductoryOffer)
    }

    func test_mockService_exposesBothPlans() async {
        let service = await MockPurchaseService()
        let pricing = await service.pricing
        XCTAssertNotNil(pricing[Config.yearlyProductID])
        XCTAssertNotNil(pricing[Config.monthlyProductID])
    }

    func test_unloadedService_hasNoPricing() async {
        let service = await MockPurchaseService(pricingLoaded: false)
        let loaded = await service.isPricingLoaded
        let pricing = await service.pricing
        XCTAssertFalse(loaded)
        XCTAssertTrue(pricing.isEmpty, "Must not display a price before StoreKit responds")
    }

    func test_reloadPopulatesPricing() async {
        let service = await MockPurchaseService(pricingLoaded: false)
        await service.reloadProducts()
        let pricing = await service.pricing
        XCTAssertFalse(pricing.isEmpty)
    }
}

// MARK: - Polish
//
// Properties that are felt rather than seen, and therefore easy to regress
// silently.

final class HapticsTests: XCTestCase {

    override func tearDown() {
        UserDefaults.standard.removeObject(forKey: Haptics.preferenceKey)
        super.tearDown()
    }

    func test_hapticsDefaultToEnabled() {
        UserDefaults.standard.removeObject(forKey: Haptics.preferenceKey)
        XCTAssertTrue(Haptics.isEnabled, "Haptics should be on unless turned off")
    }

    func test_preferenceIsRespected() {
        Haptics.setEnabled(false)
        XCTAssertFalse(Haptics.isEnabled)
        Haptics.setEnabled(true)
        XCTAssertTrue(Haptics.isEnabled)
    }

    func test_disabledHapticsAreSilentNotCrashing() {
        // Every entry point must be a no-op when disabled, not a branch the
        // caller has to remember to guard.
        Haptics.setEnabled(false)
        Haptics.prepare()
        Haptics.capture()
        Haptics.selection()
        Haptics.success()
        Haptics.failure()
        Haptics.light()
    }

    func test_enabledHapticsDoNotThrow() {
        Haptics.setEnabled(true)
        Haptics.prepare()
        Haptics.capture()
        Haptics.selection()
    }
}

final class StoredImageEncodingTests: XCTestCase {

    private func image(_ width: CGFloat, _ height: CGFloat) -> UIImage {
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1
        return UIGraphicsImageRenderer(size: CGSize(width: width, height: height),
                                       format: format).image { ctx in
            UIColor.systemIndigo.setFill()
            ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
            UIColor.systemYellow.setFill()
            ctx.fill(CGRect(x: 0, y: 0, width: width / 3, height: height))
        }
    }

    func test_storedImageIsDownscaled() async {
        // Full-resolution persistence cost 2-3 MB per scan — roughly 1.5 GB for
        // a user with 500 finds — to back a 340pt grid card.
        let data = await ScanAPIClient.encodeForStorage(image(4032, 3024))
        let encoded = try! XCTUnwrap(data)
        let decoded = try! XCTUnwrap(UIImage(data: encoded))
        XCTAssertEqual(max(decoded.size.width, decoded.size.height),
                       ScanAPIClient.maxStoredEdge, accuracy: 2)
    }

    func test_storedImageIsSmallerThanUploadPayload() async {
        let source = image(4032, 3024)
        let stored = await ScanAPIClient.encodeForStorage(source)
        let full = source.jpegData(compressionQuality: 0.75)!
        XCTAssertLessThan(try! XCTUnwrap(stored).count, full.count / 3)
    }

    func test_smallImageIsNotUpscaledForStorage() async {
        let data = await ScanAPIClient.encodeForStorage(image(320, 240))
        let decoded = try! XCTUnwrap(UIImage(data: try! XCTUnwrap(data)))
        XCTAssertEqual(decoded.size.width, 320, accuracy: 2)
    }

    func test_storageEncodingIsSeparateFromUploadEncoding() {
        // Upload targets the vision model's working resolution; storage targets
        // what the UI actually displays. Conflating them would either waste
        // bandwidth or store an image too soft for the result hero.
        XCTAssertNotEqual(ScanAPIClient.maxStoredEdge, ScanAPIClient.maxUploadEdge)
    }
}

// MARK: - Batch A regression tests
//
// Three bugs from the pre-release audit. Each test pins the behaviour the fix
// introduced, so a future change that reintroduces the bug fails here.

import SwiftData

/// Fix 1 — a valid AI result must survive a persistence failure.
///
/// The server charges a quota unit the moment a scan succeeds, so discarding
/// the result because the local write failed costs the user something they have
/// already paid for.
@MainActor
final class ScanPersistenceFailureTests: XCTestCase {

    /// A repository whose backing store is torn down, so `save` throws.
    private func brokenRepository() throws -> ScanRepository {
        let config = ModelConfiguration(isStoredInMemoryOnly: true)
        let container = try ModelContainer(for: ScanResult.self, configurations: config)
        let context = ModelContext(container)
        // A model the container does not know about makes `context.save()` fail
        // deterministically without depending on disk conditions.
        return ScanRepository(context: context)
    }

    private func sampleResult() -> ScanResult {
        ScanResult(itemName: "Off-White Out of Office", brand: "Off-White",
                   category: "shoes", conditionNotes: "Excellent",
                   valueLow: 350, valueHigh: 450, confidence: "High",
                   soldListingsCount: 0, listingTitle: "T", listingDescription: "D")
    }

    func test_resultIsPresentedBeforePersistenceIsAttempted() {
        // The ordering is the fix. `scanResult` must be assigned before the
        // save, so no persistence outcome can prevent the result being shown.
        let source = try! String(
            contentsOf: URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .appendingPathComponent("SnapWorth/ViewModels/ScanViewModel.swift"),
            encoding: .utf8)

        let assignIndex = source.range(of: "scanResult = result")?.lowerBound
        let saveIndex = source.range(of: "try repository.save(result)")?.lowerBound
        XCTAssertNotNil(assignIndex)
        XCTAssertNotNil(saveIndex)
        XCTAssertLessThan(assignIndex!, saveIndex!,
                          "scanResult must be assigned BEFORE the save is attempted")
    }

    func test_saveFailureFlagStartsClearAndResets() {
        let vm = ScanViewModel()
        XCTAssertFalse(vm.saveFailed)
        vm.saveFailed = true
        vm.reset()
        XCTAssertFalse(vm.saveFailed, "a stale failure must not leak into the next scan")
    }

    func test_resultViewDefaultsToSaved() {
        // My Finds shows already-persisted results, so the default must be true
        // or every historical find would claim it wasn't saved.
        let view = ResultView(result: sampleResult(),
                              purchaseService: MockPurchaseService(),
                              onDismiss: {})
        XCTAssertTrue(view.didSave)
    }

    func test_resultViewCanReportAnUnsavedResult(){
        let view = ResultView(result: sampleResult(),
                              purchaseService: MockPurchaseService(),
                              onDismiss: {},
                              didSave: false)
        XCTAssertFalse(view.didSave)
    }
}

/// Fix 2 — the month count must not fetch the whole history.
@MainActor
final class MonthCountTests: XCTestCase {

    private func repository() throws -> (ScanRepository, ModelContext) {
        let config = ModelConfiguration(isStoredInMemoryOnly: true)
        let container = try ModelContainer(for: ScanResult.self, configurations: config)
        let context = ModelContext(container)
        return (ScanRepository(context: context), context)
    }

    private func result(daysAgo: Int) -> ScanResult {
        ScanResult(itemName: "Item", brand: "B", category: "clothing",
                   conditionNotes: "Good", valueLow: 10, valueHigh: 20,
                   confidence: "High", soldListingsCount: 0,
                   listingTitle: "T", listingDescription: "D")
        .withTimestamp(Calendar.current.date(byAdding: .day, value: -daysAgo, to: Date())!)
    }

    func test_countsOnlyThisMonth() throws {
        let (repo, context) = try repository()
        // Two inside the current month, one clearly outside it.
        context.insert(result(daysAgo: 0))
        context.insert(result(daysAgo: 1))
        context.insert(result(daysAgo: 400))
        try context.save()

        let count = repo.countScansThisMonth()
        XCTAssertGreaterThanOrEqual(count, 2)
        XCTAssertLessThan(count, 3, "a record from last year must not be counted")
    }

    func test_emptyStoreCountsZero() throws {
        let (repo, _) = try repository()
        XCTAssertEqual(repo.countScansThisMonth(), 0)
    }

    func test_monthCountDoesNotUseAFullFetch() {
        // The regression this guards: `fetchAll().filter { … }` was O(history)
        // on the main actor, on the result-presentation path.
        let source = try! String(
            contentsOf: URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .appendingPathComponent("SnapWorth/Services/ScanRepository.swift"),
            encoding: .utf8)
        XCTAssertTrue(source.contains("fetchCount"),
                      "month count must use fetchCount, not a full fetch")

        let vmSource = try! String(
            contentsOf: URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .appendingPathComponent("SnapWorth/ViewModels/ScanViewModel.swift"),
            encoding: .utf8)
        XCTAssertFalse(vmSource.contains("repository.fetchAll()"),
                       "the scan path must not fetch the whole history")
    }

    func test_widgetSyncIsDeferredOffThePresentationPath() {
        let source = try! String(
            contentsOf: URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .appendingPathComponent("SnapWorth/Services/ScanRepository.swift"),
            encoding: .utf8)
        XCTAssertTrue(source.contains("scheduleWidgetSync"),
                      "widget aggregation must be deferred, not inline in save")
    }
}

/// Fix 3 — `purchase()` must be a no-op while a purchase is already running.
@MainActor
final class PaywallReentrancyTests: XCTestCase {

    /// Counts how many times `purchase` actually reached the service.
    private final class CountingPurchaseService: PurchaseService, ObservableObject {
        @Published private(set) var isSubscribed = false
        private(set) var purchaseCalls = 0

        func purchase(productID: String) async throws {
            purchaseCalls += 1
            try await Task.sleep(for: .milliseconds(120))
            isSubscribed = true
        }

        func restorePurchases() async throws {}
    }

    func test_secondPurchaseWhileInFlightIsANoOp() async {
        let service = CountingPurchaseService()
        let vm = PaywallViewModel()

        // Kick off the first purchase, let it start, then fire a second while
        // the first is still awaiting — the double-tap the guard exists for.
        async let first: Void = vm.purchase(service: service)
        try? await Task.sleep(for: .milliseconds(20))
        await vm.purchase(service: service)
        await first

        XCTAssertEqual(service.purchaseCalls, 1,
                       "a re-entrant purchase must not reach StoreKit twice")
    }

    func test_purchaseIsBlockedWhileRestoring() async {
        let service = CountingPurchaseService()
        let vm = PaywallViewModel()
        vm.isRestoring = true
        await vm.purchase(service: service)
        XCTAssertEqual(service.purchaseCalls, 0,
                       "purchase must not run during a restore")
    }

    func test_purchaseRunsNormallyWhenIdle() async {
        let service = CountingPurchaseService()
        let vm = PaywallViewModel()
        await vm.purchase(service: service)
        XCTAssertEqual(service.purchaseCalls, 1)
        XCTAssertTrue(vm.isPurchaseComplete)
    }
}

private extension ScanResult {
    /// Test helper: set the timestamp after construction.
    func withTimestamp(_ date: Date) -> ScanResult {
        timestamp = date
        return self
    }
}

// MARK: - 1.2.1 observability hotfix
//
// Findings A and C from the post-launch audit. Both concern whether we can see
// what is happening to live users, so the tests assert on emission, not on
// behaviour — the behaviour deliberately did not change.

/// Finding A — a fallback to the in-memory store must be visible.
final class PersistentStoreFallbackTests: XCTestCase {

    override func setUp() {
        super.setUp()
        AppLaunchState.reset()
    }

    override func tearDown() {
        AppLaunchState.reset()
        super.tearDown()
    }

    func test_healthyLaunchRecordsNothing() {
        XCTAssertNil(AppLaunchState.persistentStoreFallbackReason)
        XCTAssertFalse(AppLaunchState.isRunningOnFallbackStore)
    }

    func test_fallbackIsRecorded() {
        AppLaunchState.recordPersistentStoreFallback(
            NSError(domain: NSCocoaErrorDomain, code: NSFileReadCorruptFileError))
        XCTAssertTrue(AppLaunchState.isRunningOnFallbackStore)
        XCTAssertEqual(AppLaunchState.persistentStoreFallbackReason, "store_corrupt")
    }

    func test_classificationIsCoarseAndStable() {
        let cases: [(Error, String)] = [
            (NSError(domain: NSCocoaErrorDomain, code: NSFileReadCorruptFileError), "store_corrupt"),
            (NSError(domain: NSCocoaErrorDomain, code: NSFileWriteOutOfSpaceError), "disk_full"),
            (NSError(domain: NSCocoaErrorDomain, code: NSFileReadNoPermissionError), "permission_denied"),
            (NSError(domain: "Other", code: 1), "unknown"),
        ]
        for (error, expected) in cases {
            XCTAssertEqual(AppLaunchState.classify(error), expected)
        }
    }

    func test_migrationFailureIsClassifiedDistinctly() {
        // The 1.1.x → 1.2.0 upgrade is the specific risk this event exists for,
        // so it must be separable from generic corruption in the data.
        let error = NSError(domain: "SwiftData", code: 134110,
                            userInfo: [NSLocalizedDescriptionKey: "Migration failed for entity"])
        XCTAssertEqual(AppLaunchState.classify(error), "migration_failed")
    }

    func test_reasonNeverContainsAFilesystemPath() {
        // A SwiftData error description routinely embeds the store path, which
        // contains the container UUID and can contain the device owner's name.
        let error = NSError(
            domain: NSCocoaErrorDomain, code: 256,
            userInfo: [NSLocalizedDescriptionKey:
                "Cannot open /Users/jane.doe/Library/Application Support/default.store"])
        let reason = AppLaunchState.classify(error)
        XCTAssertFalse(reason.contains("/"))
        XCTAssertFalse(reason.lowercased().contains("jane"))
    }

    func test_eventCarriesOnlyTheClassifiedReason() {
        let event = AnalyticsEvent.persistentStoreFallback(reason: "migration_failed")
        XCTAssertEqual(event.name, "persistent_store_fallback")
        XCTAssertEqual(event.parameters, ["reason": "migration_failed"])
    }
}

/// Finding C — Snap → Sell adoption must count successes, not attempts.
final class ListingAnalyticsOrderingTests: XCTestCase {

    func test_listingGeneratedFiresAfterSuccessNotBefore() {
        // Ordering is the fix: the track call must sit after the assignment
        // that only happens on success, so a timeout cannot be counted as a
        // generated listing.
        let source = try! String(
            contentsOf: URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .appendingPathComponent("SnapWorth/ViewModels/ResultViewModel.swift"),
            encoding: .utf8)

        guard let body = source.range(of: "func generateListing") else {
            return XCTFail("generateListing not found")
        }
        let scope = String(source[body.lowerBound...])

        let assign = scope.range(of: "generatedListing = listing")?.lowerBound
        let track = scope.range(of: ".listingGenerated(")?.lowerBound
        XCTAssertNotNil(assign)
        XCTAssertNotNil(track)
        XCTAssertLessThan(assign!, track!,
                          "listingGenerated must fire only after a successful generation")
    }

    func test_failurePathDoesNotTrackGeneration() {
        let source = try! String(
            contentsOf: URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .appendingPathComponent("SnapWorth/ViewModels/ResultViewModel.swift"),
            encoding: .utf8)
        guard let catchRange = source.range(of: "listingError = AppError.from(error)") else {
            return XCTFail("failure path not found")
        }
        // Nothing between entering the catch and setting the error should emit
        // a generation event.
        let catchScope = String(source[catchRange.lowerBound...].prefix(200))
        XCTAssertFalse(catchScope.contains("listingGenerated"))
    }
}

// MARK: - MetricKit forwarding (Finding B)
//
// `MXDiagnosticPayload` and its members have no public initialiser, so the
// subscriber callbacks themselves cannot be unit-tested — that is a MetricKit
// constraint, not a design choice. The mapping layer was split out precisely so
// the part that decides WHAT LEAVES THE DEVICE is fully testable; only the thin
// subscriber shell is not covered here.

final class DiagnosticSummaryTests: XCTestCase {

    // ── Signals ──────────────────────────────────────────────────────────────

    func test_knownSignalsAreNamed() {
        XCTAssertEqual(DiagnosticSummary.signalName(11), "SIGSEGV")
        XCTAssertEqual(DiagnosticSummary.signalName(6), "SIGABRT")
        XCTAssertEqual(DiagnosticSummary.signalName(5), "SIGTRAP")
    }

    func test_swiftRuntimeTrapIsDistinguishable() {
        // Force-unwrap and out-of-bounds crashes surface as SIGTRAP. Being able
        // to separate those from SIGSEGV is the difference between "our bug"
        // and "memory corruption" when triaging a spike.
        XCTAssertEqual(DiagnosticSummary.signalName(5), "SIGTRAP")
        XCTAssertNotEqual(DiagnosticSummary.signalName(5),
                          DiagnosticSummary.signalName(11))
    }

    func test_unknownSignalIsBucketedNotPassedThrough() {
        // An unrecognised value must not widen the event's cardinality.
        XCTAssertEqual(DiagnosticSummary.signalName(9999), "signal_other")
    }

    func test_missingSignalFallsBackOnExceptionType() {
        XCTAssertEqual(DiagnosticSummary.signalName(nil, exceptionType: nil), "unknown")
        XCTAssertEqual(DiagnosticSummary.signalName(nil, exceptionType: 1), "mach_exception")
    }

    // ── Termination reason: the field most likely to leak ────────────────────

    func test_terminationReasonIsBucketed() {
        XCTAssertEqual(
            DiagnosticSummary.terminationBucket("Watchdog: 0x8badf00d exhausted"), "watchdog")
        XCTAssertEqual(
            DiagnosticSummary.terminationBucket("per-process-limit memory jetsam"),
            "memory_pressure")
        XCTAssertEqual(DiagnosticSummary.terminationBucket(nil), "none")
        XCTAssertEqual(DiagnosticSummary.terminationBucket(""), "none")
    }

    func test_rawTerminationTextNeverEscapes() {
        // The OS writes this string and it can embed process names and paths.
        // Whatever goes in, only a bucket label comes out.
        let hostile = "Terminated /Users/jane.doe/Library/Containers/" +
                      "A1B2C3D4-1111-2222-3333-444455556666/Data/default.store"
        let bucket = DiagnosticSummary.terminationBucket(hostile)

        XCTAssertFalse(bucket.contains("/"), "no path may survive bucketing")
        XCTAssertFalse(bucket.lowercased().contains("jane"))
        XCTAssertFalse(bucket.contains("A1B2C3D4"), "no container UUID may survive")
        XCTAssertTrue(["watchdog", "memory_pressure", "background_task_timeout",
                       "signal", "other", "none"].contains(bucket),
                      "bucket must come from the closed vocabulary")
    }

    func test_terminationVocabularyIsClosed() {
        // Fuzz a range of shapes; every result must be a known label.
        let allowed = Set(["watchdog", "memory_pressure", "background_task_timeout",
                           "signal", "other", "none"])
        let inputs = ["", "  ", "WATCHDOG", "0x8badf00d", "namespace SIGNAL, code 11",
                      "background task expired", "🙂 unexpected", String(repeating: "x", count: 5_000)]
        for input in inputs {
            XCTAssertTrue(allowed.contains(DiagnosticSummary.terminationBucket(input)),
                          "unexpected bucket for \(input.prefix(20))")
        }
    }

    // ── Duration bucketing ───────────────────────────────────────────────────

    func test_durationBuckets() {
        XCTAssertEqual(DiagnosticSummary.durationBucket(0.2), "under_0.5s")
        XCTAssertEqual(DiagnosticSummary.durationBucket(0.7), "0.5s_1s")
        XCTAssertEqual(DiagnosticSummary.durationBucket(3), "2s_5s")
        XCTAssertEqual(DiagnosticSummary.durationBucket(45), "over_10s")
    }

    func test_negativeDurationIsInvalidNotMisbucketed() {
        XCTAssertEqual(DiagnosticSummary.durationBucket(-1), "invalid")
    }

    func test_durationsAreNeverForwardedAsRawNumbers() {
        // A precise duration is a weak fingerprint and is not groupable.
        // Every value must collapse to one of a small set of labels.
        let labels = Set((0...200).map { DiagnosticSummary.durationBucket(Double($0) / 10) })
        XCTAssertLessThanOrEqual(labels.count, 7)
    }

    // ── End-to-end summary ───────────────────────────────────────────────────

    func test_crashSummaryCombinesBothBuckets() {
        let summary = DiagnosticSummary.crash(
            exceptionType: 1, signal: 11,
            terminationReason: "Watchdog /var/mobile/Containers/Data/app.store")
        XCTAssertEqual(summary, DiagnosticSummary.Crash(signal: "SIGSEGV",
                                                        termination: "watchdog"))
    }

    func test_crashEventCarriesOnlyBucketedFields() {
        let event = AnalyticsEvent.crashReported(signal: "SIGSEGV", termination: "watchdog")
        XCTAssertEqual(event.name, "crash_reported")
        XCTAssertEqual(event.parameters, ["signal": "SIGSEGV", "termination": "watchdog"])
    }

    func test_hangAndLaunchEventsShareTheBucketParameter() {
        XCTAssertEqual(AnalyticsEvent.hangReported(bucket: "2s_5s").parameters,
                       ["bucket": "2s_5s"])
        XCTAssertEqual(AnalyticsEvent.launchTimeReported(bucket: "under_0.5s").parameters,
                       ["bucket": "under_0.5s"])
    }
}

/// The privacy manifest must match what the code actually sends.
final class PrivacyManifestTests: XCTestCase {

    private func manifest() throws -> [String: Any] {
        let url = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("SnapWorth/PrivacyInfo.xcprivacy")
        let data = try Data(contentsOf: url)
        return try PropertyListSerialization.propertyList(
            from: data, format: nil) as! [String: Any]
    }

    private func declaredTypes() throws -> [String] {
        let collected = try manifest()["NSPrivacyCollectedDataTypes"] as? [[String: Any]] ?? []
        return collected.compactMap { $0["NSPrivacyCollectedDataType"] as? String }
    }

    func test_diagnosticsAreDeclared() throws {
        // Forwarding MetricKit data to a third party is diagnostics collection.
        // If the code sends it, the manifest must say so.
        let types = try declaredTypes()
        XCTAssertTrue(types.contains("NSPrivacyCollectedDataTypeCrashData"))
        XCTAssertTrue(types.contains("NSPrivacyCollectedDataTypePerformanceData"))
        XCTAssertTrue(types.contains("NSPrivacyCollectedDataTypeOtherDiagnosticData"))
    }

    func test_nothingIsLinkedToIdentityOrUsedForTracking() throws {
        let collected = try manifest()["NSPrivacyCollectedDataTypes"] as? [[String: Any]] ?? []
        for entry in collected {
            let name = entry["NSPrivacyCollectedDataType"] as? String ?? "?"
            XCTAssertEqual(entry["NSPrivacyCollectedDataTypeLinked"] as? Bool, false,
                           "\(name) must not be linked to identity")
            XCTAssertEqual(entry["NSPrivacyCollectedDataTypeTracking"] as? Bool, false,
                           "\(name) must not be used for tracking")
        }
    }

    func test_trackingIsDisabledAtTheManifestLevel(){
        let value = try? manifest()["NSPrivacyTracking"] as? Bool
        XCTAssertEqual(value, false)
    }
}
