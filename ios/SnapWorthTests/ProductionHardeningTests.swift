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
