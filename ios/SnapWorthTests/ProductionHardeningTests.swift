import AVFoundation
import CoreMedia
import DeviceCheck
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

    // ── 429 carries the real wait in a header ────────────────────────────────

    func test_retryAfterIsReadFromTheHeader() {
        let response = HTTPURLResponse(
            url: URL(string: "https://api.snapworth.eu/scan")!, statusCode: 429,
            httpVersion: nil, headerFields: ["Retry-After": "300"])!
        XCTAssertEqual(ScanAPIError.retryAfter(from: response), 300)
    }

    func test_retryAfterIsNilWhenAbsentOrNotANumber() {
        func response(_ headers: [String: String]) -> HTTPURLResponse {
            HTTPURLResponse(url: URL(string: "https://api.snapworth.eu/scan")!,
                            statusCode: 429, httpVersion: nil,
                            headerFields: headers)!
        }
        XCTAssertNil(ScanAPIError.retryAfter(from: response([:])))
        // RFC 9110 also permits an HTTP-date. This API only sends seconds, and
        // guessing at a date whose clock we do not share is worse than nil.
        XCTAssertNil(ScanAPIError.retryAfter(
            from: response(["Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"])))
    }

    func test_a429BecomesRateLimitCarryingTheWait() {
        let response = HTTPURLResponse(
            url: URL(string: "https://api.snapworth.eu/scan")!, statusCode: 429,
            httpVersion: nil, headerFields: ["Retry-After": "120"])!
        let body = Data(#"{"detail": "Rate limit: 20 requests/hour."}"#.utf8)
        let error = ScanAPIError.from(response, data: body)
        XCTAssertEqual(error.statusCode, 429)
        XCTAssertEqual(AppError.from(error), .rateLimit(retryAfter: 120))
    }

    func test_otherStatusesStillBecomePlainServerErrors() {
        let response = HTTPURLResponse(
            url: URL(string: "https://api.snapworth.eu/scan")!, statusCode: 402,
            httpVersion: nil, headerFields: [:])!
        let body = Data(#"{"detail": "You've used all 3 free scans today."}"#.utf8)
        let mapped = AppError.from(ScanAPIError.from(response, data: body))
        XCTAssertTrue(mapped.isPaywall)
    }

    func test_rateLimitMessageUsesTheServerWait() {
        // The fixed "Try again in an hour" was wrong by up to an hour in the
        // user's disfavour: the window slides, so someone who tripped it 55
        // minutes ago is minutes away from scanning again.
        XCTAssertEqual(AppError.rateLimitMessage(retryAfter: 300),
                       "You've hit the scan limit. Try again in 5 minutes.")
        XCTAssertEqual(AppError.rateLimitMessage(retryAfter: 45),
                       "You've hit the scan limit. Try again in 45 seconds.")
    }

    func test_rateLimitMessageBoundaries() {
        // Rounding is always up, so the copy never invites a retry that fails.
        let cases: [(TimeInterval?, String)] = [
            (nil,   "Try again in an hour."),        // header absent: say the window
            (0,     "Try again in a few seconds."),
            (9,     "Try again in a few seconds."),
            (10,    "Try again in 10 seconds."),
            (59,    "Try again in 59 seconds."),
            (60,    "Try again in a minute."),
            (61,    "Try again in 2 minutes."),      // up, not down
            (1800,  "Try again in 30 minutes."),
            (3540,  "Try again in 59 minutes."),
            (3541,  "Try again in an hour."),        // never "60 minutes"
            (7200,  "Try again in an hour."),
        ]
        for (seconds, tail) in cases {
            XCTAssertEqual(AppError.rateLimitMessage(retryAfter: seconds),
                           "You've hit the scan limit. \(tail)",
                           "wrong copy for retryAfter=\(String(describing: seconds))")
        }
    }

    func test_rateLimitStillRevealsNothingAboutTheBackend() {
        // The property the original copy was protecting; kept while the wait
        // becomes real. The backend's own detail ("Rate limit: 20
        // requests/hour.") is deliberately *not* surfaced.
        for seconds: TimeInterval? in [nil, 5, 45, 300, 3600] {
            let message = AppError.rateLimitMessage(retryAfter: seconds)
            XCTAssertFalse(message.contains("GEMINI"))
            XCTAssertFalse(message.contains("API"))
            XCTAssertFalse(message.contains("requests/hour"))
        }
    }

    func test_twoDifferentWaitsAreNotEqual() {
        // Otherwise a SwiftUI alert bound to the error would not re-present
        // when the wait changed — the same bug `.sessionExpired` had.
        XCTAssertNotEqual(AppError.rateLimit(retryAfter: 60),
                          AppError.rateLimit(retryAfter: 600))
        XCTAssertEqual(AppError.rateLimit(retryAfter: 60),
                       AppError.rateLimit(retryAfter: 60))
        XCTAssertEqual(AppError.rateLimit(retryAfter: nil),
                       AppError.rateLimit(retryAfter: nil))
    }

    func test_serverErrorDescription_omitsStatusCodeNoise() {
        let error = ScanAPIError.serverError(502, "Our AI is temporarily unavailable.")
        XCTAssertEqual(error.errorDescription, "Our AI is temporarily unavailable.")
        XCTAssertEqual(error.statusCode, 502)
    }

    func test_a502WithNoRealDetailIsAnOutageNotAnAIFailure() {
        // The `.serverUnavailable` branch for 502 was unreachable. It tested
        // `detail.isEmpty`, and `APIErrorDetail.parse` never returns empty —
        // with no usable `detail` in the body it returns its own fixed
        // sentence, the very words `.unknown` prints. So a genuine outage with
        // an empty body arrived carrying "Something went wrong. Please try
        // again." and was reported as an AI failure with that text: the user
        // was told to retry rather than that the service was down.
        XCTAssertEqual(AppError.from(ScanAPIError.serverError(
            502, "Something went wrong. Please try again.")), .serverUnavailable)
        XCTAssertEqual(AppError.from(ScanAPIError.serverError(502, "")),
                       .serverUnavailable)
        XCTAssertEqual(AppError.from(ScanAPIError.serverError(502, "   ")),
                       .serverUnavailable)

        // And real backend copy still reaches the user, which is the whole
        // reason 502 surfaces its detail at all.
        guard case .aiFailed(let msg) = AppError.from(ScanAPIError.serverError(
            502, "The AI couldn't price this item. Please try again.")) else {
            return XCTFail("real detail must still surface")
        }
        XCTAssertEqual(msg, "The AI couldn't price this item. Please try again.")
    }

    func test_aPhoneWithNoSignalIsToldSoRatherThanSomethingWentWrong() {
        // iOS's own strings for these codes contain no "network", no
        // "offline" and no status number, so the substring fallback could not
        // rescue them and they all reached `.unknown` — "Something went
        // wrong. Please try again." to someone standing in a shop with no
        // signal, while the right copy lived one case away.
        for code in [URLError.Code.cannotFindHost,
                     .dnsLookupFailed,
                     .dataNotAllowed,
                     .internationalRoamingOff,
                     .callIsActive] {
            XCTAssertEqual(AppError.from(URLError(code)), .network,
                           "\(code) should read as a network problem")
        }
        // Unchanged, and asserted so the widening did not disturb them.
        XCTAssertEqual(AppError.from(URLError(.notConnectedToInternet)), .network)
        XCTAssertEqual(AppError.from(URLError(.timedOut)), .timeout)
    }

    func test_aPinningFailureIsNotCalledANetworkProblem() {
        // `.secureConnectionFailed` is deliberately left out of the widening:
        // this app pins its certificate, so that code can mean interception
        // rather than an outage, and "check your network and try again" is
        // advice whose retry is the thing that would succeed.
        XCTAssertNotEqual(AppError.from(URLError(.secureConnectionFailed)), .network)
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

    // I-23. The mapping above existed and was correct; nothing consumed it.
    // Both 402s reached the generic "Scan Failed / OK" alert — a dead end at
    // the moment of highest purchase intent — and were filed as
    // scan_failed{reason:no_result}, the wrong bucket in the one funnel the
    // free-scan experiment is read against.
    func test_bothPaymentRequiredCases_areRoutedToThePaywall() {
        let quota = AppError.from(ScanAPIError.serverError(402, "You've used all 3 free scans today."))
        let pro = AppError.from(ScanAPIError.serverError(402, "Listing drafts are a SnapWorth Pro feature."))
        XCTAssertTrue(quota.isPaywall)
        XCTAssertTrue(pro.isPaywall)
    }

    func test_realFailures_areNotRoutedToThePaywall() {
        // A paywall shown for a network blip would be worse than the dead end
        // it replaces: it asks for money over a problem money cannot fix.
        for error: AppError in [.network, .timeout, .rateLimit(retryAfter: nil), .serverUnavailable,
                                .sessionExpired, .imageEncodingFailed, .persistence,
                                .aiFailed("couldn't price it"), .unusablePhoto("too blurry"),
                                .unknown("?")] {
            XCTAssertFalse(error.isPaywall, "\(error) must not open the paywall")
        }
    }
}

// MARK: - App Attest key recovery (I-1)
//
// The key id lives in UserDefaults, which iCloud backup and Quick Start
// restore onto a new iPhone. The Secure Enclave key it names does not
// migrate, so `generateAssertion` throws DCError.invalidKey before any
// request leaves the device. That error used to escape mintToken: callers
// sent unauthenticated, got 401, and the user was told to reinstall — and
// every retry repeated it, because nothing cleared the stale id.
//
// The decision of *which* failures justify discarding the key is the part
// worth pinning: throwing it away on a transient DeviceCheck outage forces a
// pointless re-attestation, so the rule has to be narrow in both directions.

final class AppAttestKeyRecoveryTests: XCTestCase {

    private func dcError(_ code: DCError.Code) -> Error {
        NSError(domain: DCErrorDomain, code: code.rawValue)
    }

    func test_aKeyTheEnclaveDoesNotHave_forcesFreshAttestation() {
        XCTAssertTrue(AttestationService.requiresFreshKey(dcError(.invalidKey)),
                      "device migration must recover, not dead-end")
    }

    func test_anUnusableStoredValue_forcesFreshAttestation() {
        XCTAssertTrue(AttestationService.requiresFreshKey(dcError(.invalidInput)))
    }

    func test_transientFailures_keepTheExistingKey() {
        // Re-attesting on an Apple outage burns a server record and a round
        // trip for a condition that will clear on its own.
        XCTAssertFalse(AttestationService.requiresFreshKey(dcError(.serverUnavailable)))
        XCTAssertFalse(AttestationService.requiresFreshKey(dcError(.unknownSystemFailure)))
    }

    func test_unsupportedDevice_doesNotRetryForever() {
        // A new key cannot help where the feature is absent.
        XCTAssertFalse(AttestationService.requiresFreshKey(dcError(.featureUnsupported)))
    }

    func test_nonDeviceCheckErrors_areNotTreatedAsKeyProblems() {
        XCTAssertFalse(AttestationService.requiresFreshKey(URLError(.notConnectedToInternet)))
        XCTAssertFalse(AttestationService.requiresFreshKey(AttestationError.challengeFailed))
    }
}

// MARK: - Privacy policy disclosure (I-18)
//
// The web copy at /privacy gained a Service Providers section on 2026-09-03;
// the shipped in-app copy did not. For a week the app told users their data
// was not shared with anyone — on the screen the paywall links to, which is
// also what App Review and an EU user read — while photos went to Google on
// every scan. Nothing caught it because the text lived inside a view body
// where no test could reach it.

final class PrivacyPolicyDisclosureTests: XCTestCase {

    private var policy: String {
        PrivacyPolicy.sections.map { ($0.heading ?? "") + " " + $0.text }.joined(separator: "\n")
    }

    func test_everyProcessorThatReceivesData_isNamed() {
        for processor in PrivacyPolicy.processors {
            XCTAssertTrue(policy.contains(processor),
                          "\(processor) receives user data but the policy does not name it")
        }
    }

    func test_policyDoesNotClaimDataIsUnshared() {
        // The exact sentence that was false: "We do not sell, rent, or share
        // your photos or device identifier with third parties, except as
        // required by law." Any unqualified version of it is a false claim.
        XCTAssertTrue(policy.contains("except for the service providers below"),
                      "the sharing sentence must point at the processor list")
    }

    func test_theTelegramParagraphDescribesWhatIsActuallyRelayed() {
        // It said "never a device identifier, never anything that links a scan
        // to a device or a person" — while five bot surfaces send a stable
        // salted hash of the device's attestation key plus that device's scan
        // count, activity dates and subscription state. A one-way hash is
        // still a pseudonymous identifier, so the sentence was false.
        //
        // Same shape as the drift above: the web copy and this copy are two
        // files, and only a test connects them.
        XCTAssertFalse(policy.contains("never a device identifier"),
                       "the claim the bot contradicts is back")
        XCTAssertTrue(
            policy.contains("one-way salted hash of your device's attestation key"),
            "the in-app policy has drifted from /privacy again")
        for detail in ["scan count", "first and last activity dates",
                       "subscription state", "400 days"] {
            XCTAssertTrue(policy.contains(detail),
                          "the policy no longer mentions \(detail)")
        }
    }

    func test_theDisclosureIsNotWiderThanTheTruth() {
        XCTAssertTrue(policy.contains("Never the photo"))
        XCTAssertTrue(policy.contains("never your name, email address or location"))
        XCTAssertTrue(policy.contains("not an advertising identifier"))
    }

    func test_analyticsAndDeviceCheckCollectionAreDisclosed() {
        // Both are collected by the shipped app; neither was mentioned.
        XCTAssertTrue(policy.contains("TelemetryDeck"))
        XCTAssertTrue(policy.contains("DeviceCheck"))
        XCTAssertTrue(policy.lowercased().contains("turn analytics off"),
                      "an opt-out that exists must be findable in the policy")
    }

    func test_updatedDateIsNotOlderThanTheProcessorDisclosure() {
        // A policy that gains processors but keeps its old date reads as
        // unchanged to anyone checking whether they need to re-consent.
        XCTAssertNotEqual(PrivacyPolicy.updated, "September 2, 2026",
                          "the date must move when the policy does")
    }
}

// MARK: - The shared /scan contract
//
// C-3. The `/scan` response shape was asserted twice, independently and in two
// languages: once in `backend/tests/test_ai_pipeline.py` against a Python
// dict, and once here against a hand-typed JSON string literal. Nothing
// compared them — and because the two CI workflows have mutually exclusive
// path filters, eight non-merge commits changed `main.py`, `valuation.py` or
// `prompts.py` and deployed to production with zero client-decode
// verification. A renamed field would have been caught by neither suite.
//
// `contract/scan-response.json` is now the single fixture both sides read.

final class ScanContractTests: XCTestCase {

    /// The repo-root fixture, located from this file rather than from a
    /// bundle: the test target has no resources phase, and adding one to
    /// carry a single JSON file would be more machinery than the file.
    static func contractData() throws -> Data {
        let url = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()   // SnapWorthTests
            .deletingLastPathComponent()   // ios
            .deletingLastPathComponent()   // repo root
            .appendingPathComponent("contract/scan-response.json")
        return try Data(contentsOf: url)
    }

    func test_theSharedFixtureDecodes() throws {
        let decoded = try JSONDecoder().decode(
            ScanAPIResponse.self, from: Self.contractData())
        XCTAssertEqual(decoded.brand, "Patagonia")
        XCTAssertEqual(decoded.category, "clothing")
        XCTAssertEqual(decoded.estValueLowUsd, 32.0)
        XCTAssertEqual(decoded.estValueHighUsd, 85.0)
        XCTAssertFalse(decoded.itemName.isEmpty)
        XCTAssertFalse(decoded.listingTitle.isEmpty)
        XCTAssertFalse(decoded.listingDescription.isEmpty)
    }

    /// The v2 valuation payload is what powers "why this price". It is
    /// optional on the wire, so a decode failure here is silent in the app —
    /// the panel simply never appears — which is exactly why it needs a test.
    func test_theSharedFixtureCarriesTheValuationDetail() throws {
        let decoded = try JSONDecoder().decode(
            ScanAPIResponse.self, from: Self.contractData())
        let detail = ValuationDetail(response: decoded)
        XCTAssertNotNil(detail, "the v2 payload in the shared fixture no longer decodes")
    }

    func test_freeScansRemainingDecodesAsOptional() throws {
        // Nil when the server omits it (Pro, or the quota store is down) —
        // see I-3. The fixture carries a value, so this checks the present
        // case; the absent case is covered below by `base`.
        let decoded = try JSONDecoder().decode(
            ScanAPIResponse.self, from: Self.contractData())
        XCTAssertEqual(decoded.freeScansRemaining, 2)
    }
}

// MARK: - Legacy response compatibility

final class ScanAPIResponseDecodingTests: XCTestCase {

    /// Deliberately still a literal: this is the *minimal* v1 body, which is
    /// what an old server or a trimmed response looks like. The full,
    /// current shape lives in `contract/scan-response.json` and is checked by
    /// `ScanContractTests` above.
    private let base = """
    {"item_name":"Patagonia Better Sweater","brand":"Patagonia","category":"clothing",
     "condition_notes":"Good","est_value_low_usd":45.0,"est_value_high_usd":90.0,
     "confidence":"High","listing_title":"T","listing_description":"D"}
    """

    func test_decodesWhenSoldListingsCountAbsent() throws {
        // The normal case since #49: the backend no longer sends the field.
        let decoded = try JSONDecoder().decode(
            ScanAPIResponse.self, from: base.data(using: .utf8)!)
        XCTAssertEqual(decoded.soldListingsCount, 0)
        XCTAssertEqual(decoded.brand, "Patagonia")
    }

    func test_decodesWhenSoldListingsCountPresent() throws {
        // An old cached response, or a server rolled back past #49.
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

// MARK: - Paywall copy
//
// Two production bugs live here.
//
// 1. The trial headline shipped reading "Try SnapWorth free for 3 dayss" —
//    the service pluralised a sentence and the view pluralised the result.
//    That round-trip is gone: the offer is data now, and the unit is
//    pluralised in exactly one place.
//
// 2. Every caller read "an introductory offer exists" as "it is free", so a
//    paid intro offer rendered as "Try SnapWorth free for $9.99 for 3 months".
//    Not visible today — the configured product is a real 3-day free trial —
//    but it becomes visible the moment the offer is changed in App Store
//    Connect, a change made entirely outside the app.
//
// `MockPurchaseService` hardcodes the shipping offer, so previews and tests
// rendered the correct string while real StoreKit did not. Every assertion
// below builds its own `IntroOffer` rather than leaning on the mock.

// `MockPurchaseService` is `@MainActor`, and one test reads its sample pricing.
@MainActor
final class PaywallCopyTests: XCTestCase {

    private func freeTrial(_ count: Int = 3, _ unit: String = "day") -> IntroOffer {
        IntroOffer(kind: .freeTrial, displayPrice: "",
                   unitCount: count, unit: unit, periodCount: 1)
    }

    private func payUpFront(_ price: String = "$9.99",
                            _ count: Int = 3, _ unit: String = "month") -> IntroOffer {
        IntroOffer(kind: .payUpFront, displayPrice: price,
                   unitCount: count, unit: unit, periodCount: 1)
    }

    private func payAsYouGo(_ price: String = "$1.99", unitCount: Int = 1,
                            unit: String = "month", periods: Int = 3) -> IntroOffer {
        IntroOffer(kind: .payAsYouGo, displayPrice: price,
                   unitCount: unitCount, unit: unit, periodCount: periods)
    }

    // ── The paid-offer bug ────────────────────────────────────────────────

    func test_paidOffer_neverSaysFreeAnywhere() {
        // The whole point. Nothing on this screen may call a paid offer free.
        for offer in [payUpFront(), payAsYouGo()] {
            let strings = [
                PaywallCopy.headline(isYearly: true, offer: offer),
                PaywallCopy.subheadline(isYearly: true, price: "$39.99", offer: offer),
                PaywallCopy.offerPhrase(offer),
                PaywallCopy.planDetail(weekly: "$0.77", offer: offer),
                PaywallCopy.ctaTitle(isYearly: true, offer: offer),
            ]
            for text in strings {
                XCTAssertFalse(text.lowercased().contains("free"),
                               "Paid offer described as free: \(text)")
            }
        }
    }

    func test_paidOffer_doesNotProduceTheShippedContradiction() {
        // The literal string the old code built.
        XCTAssertNotEqual(
            PaywallCopy.headline(isYearly: true, offer: payUpFront()),
            "Try SnapWorth\nfree for $9.99 for 3 months")
        XCTAssertEqual(
            PaywallCopy.headline(isYearly: true, offer: payUpFront()),
            "Unlock\nSnapWorth Pro")
    }

    func test_payUpFront_statesThePriceAndWhatFollows() {
        XCTAssertEqual(
            PaywallCopy.subheadline(isYearly: true, price: "$39.99", offer: payUpFront()),
            "$9.99 for your first 3 months, then $39.99/year. Cancel anytime.")
        XCTAssertEqual(PaywallCopy.offerPhrase(payUpFront()),
                       "$9.99 for your first 3 months")
    }

    func test_payAsYouGo_statesTheRateAndHowLong() {
        XCTAssertEqual(
            PaywallCopy.subheadline(isYearly: true, price: "$39.99", offer: payAsYouGo()),
            "$1.99 per month for 3 months, then $39.99/year. Cancel anytime.")
        XCTAssertEqual(PaywallCopy.offerPhrase(payAsYouGo()),
                       "$1.99 per month for 3 months")
    }

    func test_payAsYouGo_neverSaysPerOneUnit() {
        // A one-unit period is "per month", not "per 1 month"; a longer one
        // keeps its count, because "per week" would understate the charge.
        XCTAssertEqual(PaywallCopy.perPeriod(payAsYouGo()), "month")
        XCTAssertEqual(
            PaywallCopy.perPeriod(payAsYouGo(unitCount: 2, unit: "week")), "2 weeks")
        XCTAssertEqual(
            PaywallCopy.offerPhrase(payAsYouGo("$3.00", unitCount: 2, unit: "week")),
            "$3.00 per 2 weeks for 6 weeks")
    }

    func test_paidOffer_ctaDoesNotOfferToStartATrial() {
        XCTAssertEqual(PaywallCopy.ctaTitle(isYearly: true, offer: payUpFront()),
                       "Subscribe Yearly")
        XCTAssertEqual(PaywallCopy.ctaTitle(isYearly: true, offer: payAsYouGo()),
                       "Subscribe Yearly")
    }

    // ── The free trial still reads exactly as it ships today ──────────────

    func test_freeTrial_isUnchangedFromWhatShips() {
        let offer = freeTrial()
        XCTAssertEqual(PaywallCopy.headline(isYearly: true, offer: offer),
                       "Try SnapWorth\nfree for 3 days")
        XCTAssertEqual(
            PaywallCopy.subheadline(isYearly: true, price: "$39.99", offer: offer),
            "Then $39.99/year. Cancel anytime.")
        XCTAssertEqual(PaywallCopy.offerPhrase(offer), "3-day free trial")
        XCTAssertEqual(PaywallCopy.ctaTitle(isYearly: true, offer: offer),
                       "Start Free Trial")
    }

    func test_mockMirrorsTheShippingOffer() {
        // If the mock drifts from the real product, previews and tests keep
        // rendering a string production no longer produces.
        let mocked = MockPurchaseService.samplePricing[Config.yearlyProductID]?
            .introductoryOffer
        XCTAssertEqual(mocked, freeTrial())
    }

    // ── Pluralisation, the "3 dayss" gravestone ───────────────────────────

    func test_phrase_pluralisesExactlyOnce() {
        XCTAssertEqual(PaywallCopy.phrase(count: 3, unit: "day"), "3 days")
        XCTAssertEqual(PaywallCopy.phrase(count: 1, unit: "day"), "1 day")
        XCTAssertEqual(PaywallCopy.phrase(count: 2, unit: "week"), "2 weeks")
        XCTAssertEqual(PaywallCopy.phrase(count: 1, unit: "month"), "1 month")
        // A unit that already arrived plural is not doubled.
        XCTAssertEqual(PaywallCopy.phrase(count: 3, unit: "days"), "3 days")
    }

    func test_headline_neverReadsDayss() {
        // The exact string that shipped, guarded directly — including from a
        // caller that hands over an already-plural unit.
        for unit in ["day", "days"] {
            XCTAssertFalse(
                PaywallCopy.headline(isYearly: true, offer: freeTrial(3, unit))
                    .contains("dayss"))
        }
    }

    func test_durationCoversEveryPeriodOfTheOffer() {
        // periodCount > 1 means the offer repeats; the duration is the whole of
        // it, not one period.
        XCTAssertEqual(PaywallCopy.duration(payAsYouGo()), "3 months")
        XCTAssertEqual(PaywallCopy.duration(freeTrial(1, "month")), "1 month")
    }

    // ── No offer, and the non-yearly plan ─────────────────────────────────

    func test_headline_withoutOfferDoesNotPromiseOne() {
        XCTAssertEqual(
            PaywallCopy.headline(isYearly: true, offer: nil), "Unlock\nSnapWorth Pro")
        // The offer belongs to the yearly product; selecting monthly must not
        // inherit it.
        XCTAssertEqual(
            PaywallCopy.headline(isYearly: false, offer: freeTrial()),
            "Unlock\nSnapWorth Pro")
        XCTAssertEqual(
            PaywallCopy.ctaTitle(isYearly: false, offer: freeTrial()),
            "Subscribe Monthly")
        XCTAssertEqual(
            PaywallCopy.subheadline(isYearly: false, price: "$4.99", offer: freeTrial()),
            "$4.99/month. Cancel anytime.")
    }

    func test_subheadline_withoutAPriceSaysSoRatherThanGuessing() {
        XCTAssertEqual(
            PaywallCopy.subheadline(isYearly: true, price: "—", offer: freeTrial()),
            "Loading plans…")
    }

    func test_planDetail_survivesEveryMissingPiece() {
        XCTAssertEqual(PaywallCopy.planDetail(weekly: nil, offer: nil), "Best value")
        XCTAssertEqual(PaywallCopy.planDetail(weekly: "$0.77", offer: nil),
                       "$0.77 per week")
        XCTAssertEqual(PaywallCopy.planDetail(weekly: "$0.77", offer: freeTrial()),
                       "$0.77 per week · 3-day free trial")
    }

    // ── Partial fetch copy ────────────────────────────────────────────────

    func test_pricingProblem_distinguishesPartialFromEmpty() {
        // A partial fetch leaves something purchasable, so the copy must not
        // read as though the screen is dead.
        XCTAssertEqual(PaywallCopy.pricingProblem(hasSomePricing: false),
                       "Couldn't load plans. Check your connection and try again.")
        XCTAssertTrue(PaywallCopy.pricingProblem(hasSomePricing: true)
            .contains("continue with the one shown"))
    }
}

// MARK: - Terms of Service
//
// The Terms promised "a 3-day free trial" as a flat fact. Nothing in this
// repository controls that — App Store Connect does. Changing the offer there
// to a paid one, or removing it, made the Terms a promise the app does not
// keep, with no release in between to catch it. The same sentence was served
// from `GET /terms`, so the falsehood shipped twice.

final class TermsCopyTests: XCTestCase {

    func test_termsDoNotNameAnOfferOnlyAppleControls() {
        let text = TermsCopy.subscriptions
        XCTAssertFalse(text.lowercased().contains("free trial"),
                       "The Terms must not name a trial App Store Connect can withdraw")
        // No "3-day", "3 days", "1 month" — any concrete offer length.
        let duration = try? NSRegularExpression(
            pattern: #"\d+[\s-](day|week|month|year)"#, options: .caseInsensitive)
        let range = NSRange(text.startIndex..., in: text)
        XCTAssertEqual(duration?.numberOfMatches(in: text, range: range), 0,
                       "The Terms state an offer length they cannot guarantee")
    }

    func test_termsPointAtTheSurfaceThatKnowsTheRealOffer() {
        // Dropping the claim is only safe if the Terms say where the truth is.
        let text = TermsCopy.subscriptions
        XCTAssertTrue(text.contains("shown on the subscription screen"))
        XCTAssertTrue(text.contains("before you are charged"))
        // Still says the things the Terms must say.
        XCTAssertTrue(text.contains("auto-renewing"))
        XCTAssertTrue(text.contains("cancel at any time"))
    }
}

// MARK: - Paywall selection
//
// The paywall selects yearly by default, and `isPurchasable` reads the
// *selected* plan. A product fetch that returned only the monthly plan
// therefore left a disabled CTA under "Loading plans…", with a purchasable
// monthly card sitting unselected beside it and no copy pointing at it.

@MainActor
final class PaywallSelectionTests: XCTestCase {

    private func pricing(_ id: String) -> [String: PlanPricing] {
        [id: PlanPricing(productID: id, displayPrice: "$4.99",
                         displayPricePerWeek: nil, introductoryOffer: nil,
                         savingsPercent: nil)]
    }

    // ── Restore has to say something either way ─────────────────────────────

    @MainActor
    func test_restoreWithNothingToRestoreSaysSo() async {
        // `AppStore.sync()` succeeding with no entitlement is a *success*:
        // `restorePurchases` throws only on a real sync error, so the catch
        // never ran, `errorMessage` stayed nil, `isPurchaseComplete` stayed
        // false and the sheet did not dismiss. The spinner ran for a second,
        // stopped, and nothing else on screen changed — indistinguishable from
        // a button that does nothing, which is what an App Review tester on a
        // fresh sandbox account taps.
        let vm = PaywallViewModel()
        let service = MockPurchaseService(forcedSubscribed: false)

        await vm.restore(service: service)

        XCTAssertFalse(vm.isPurchaseComplete)
        XCTAssertNil(vm.errorMessage, "nothing failed, so nothing is red")
        XCTAssertEqual(vm.pendingMessage,
                       "No active subscription found on this Apple ID.")
        XCTAssertFalse(vm.isRestoring, "the spinner stops either way")
    }

    @MainActor
    func test_restoreThatFindsASubscriptionStillCompletes() async {
        let vm = PaywallViewModel()
        let service = MockPurchaseService(forcedSubscribed: true)

        await vm.restore(service: service)

        XCTAssertTrue(vm.isPurchaseComplete)
        XCTAssertNil(vm.pendingMessage, "no 'nothing found' on a success")
        XCTAssertNil(vm.errorMessage)
    }

    @MainActor
    func test_restoreClearsAStalePendingMessage() async {
        // An Ask-to-Buy attempt leaves "waiting for approval" behind. Without
        // clearing it, the next Restore reads as its result.
        let vm = PaywallViewModel()
        vm.pendingMessage = "Waiting for approval."

        await vm.restore(service: MockPurchaseService(forcedSubscribed: true))

        XCTAssertNil(vm.pendingMessage)
    }

    func test_fallsBackToTheOnlyPlanThatLoaded() {
        let vm = PaywallViewModel()
        XCTAssertEqual(vm.selectedProductID, Config.yearlyProductID)
        vm.reconcileSelection(with: pricing(Config.monthlyProductID))
        XCTAssertEqual(vm.selectedProductID, Config.monthlyProductID,
                       "A selection with no price leaves the CTA permanently disabled")
    }

    func test_keepsTheDefaultWhenItLoaded() {
        let vm = PaywallViewModel()
        vm.reconcileSelection(with: MockPurchaseService.samplePricing)
        XCTAssertEqual(vm.selectedProductID, Config.yearlyProductID)
    }

    func test_prefersYearlyWhenTheUserHasNotChosen() {
        let vm = PaywallViewModel()
        vm.selectedProductID = "com.snapworth.retired"
        vm.reconcileSelection(with: MockPurchaseService.samplePricing)
        XCTAssertEqual(vm.selectedProductID, Config.yearlyProductID)
    }

    func test_doesNotMoveTheSelectionWhenNothingLoaded() {
        // Total failure: there is nothing better to move to, and moving would
        // change the screen for no gain.
        let vm = PaywallViewModel()
        vm.reconcileSelection(with: [:])
        XCTAssertEqual(vm.selectedProductID, Config.yearlyProductID)
    }

    func test_leavesAnExplicitMonthlyChoiceAlone() {
        let vm = PaywallViewModel()
        vm.selectedProductID = Config.monthlyProductID
        vm.reconcileSelection(with: MockPurchaseService.samplePricing)
        XCTAssertEqual(vm.selectedProductID, Config.monthlyProductID)
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

    // A `brokenRepository()` helper used to sit here, unreferenced, with a
    // comment claiming it made `save()` fail deterministically "without
    // depending on disk conditions". Its body built an ordinary in-memory
    // container that saves perfectly well, so it neither did that nor was
    // called. Removed rather than left as a trap: the next person to reach for
    // it would have written a test that passes because nothing failed.
    //
    // A real save failure on the *shared* context cannot be provoked from a
    // unit test — every context here is a secondary one, whose autosave
    // defaults to false — which is exactly why the missing rollback was
    // invisible to this suite, and why the assertion about it is
    // source-inspected below.

    private func sampleResult() -> ScanResult {
        ScanResult(itemName: "Off-White Out of Office", brand: "Off-White",
                   category: "shoes", conditionNotes: "Excellent",
                   valueLow: 350, valueHigh: 450, confidence: "High",
                   soldListingsCount: 0, listingTitle: "T", listingDescription: "D")
    }

    func test_aFailedSaveHandsBackSomethingSafeToDisplay() {
        // `ScanViewModel` assigns `scanResult` before attempting the save, on
        // purpose: a storage failure must not take the user's result away. The
        // rollback un-registers that object, so the repository takes a copy
        // first and returns it with the error — otherwise the sheet would be
        // holding a model SwiftData had discarded.
        let original = sampleResult()
        original.paidPrice = 9.50
        original.notes = "back-room rail"
        let copy = original.detachedCopy()

        XCTAssertEqual(copy.id, original.id, "the same find, not a new one")
        XCTAssertEqual(copy.itemName, original.itemName)
        XCTAssertEqual(copy.paidPrice, 9.50)
        XCTAssertEqual(copy.notes, "back-room rail")
        XCTAssertFalse(copy === original, "a copy, so a rollback cannot reach it")
    }

    func test_detachedCopyCarriesEveryStoredProperty() {
        // A copy that silently dropped a field would show the user a result
        // missing their photo or what they paid. The model's memberwise init
        // is the list of stored properties, so the copy has to name every
        // parameter of it.
        let source = try! String(
            contentsOf: URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .appendingPathComponent("SnapWorth/Models/ScanResult.swift"),
            encoding: .utf8)

        guard let initRange = source.range(of: "    init(\n"),
              let initEnd = source.range(of: "    ) {", range: initRange.lowerBound..<source.endIndex),
              let copyRange = source.range(of: "func detachedCopy() -> ScanResult {"),
              let copyEnd = source.range(of: "        )\n    }",
                                         range: copyRange.lowerBound..<source.endIndex)
        else { return XCTFail("could not locate init or detachedCopy") }

        func labels(_ text: String) -> Set<String> {
            Set(text.split(separator: "\n").compactMap { line in
                let trimmed = line.trimmingCharacters(in: .whitespaces)
                guard let colon = trimmed.firstIndex(of: ":") else { return nil }
                let label = String(trimmed[trimmed.startIndex..<colon])
                return label.allSatisfy { $0.isLetter || $0.isNumber } ? label : nil
            })
        }

        let declared = labels(String(source[initRange.upperBound..<initEnd.lowerBound]))
        let copied = labels(String(source[copyRange.upperBound..<copyEnd.lowerBound]))
        XCTAssertFalse(declared.isEmpty, "the parser found no init parameters")
        XCTAssertEqual(declared.subtracting(copied), [],
                       "detachedCopy() is missing stored properties — a copy " +
                       "that drops a field shows the user an incomplete result")
    }

    func test_theRepositoryRollsBackOnEveryFailurePath() {
        // Source-inspected because a real save failure on the *shared* context
        // cannot be provoked in a unit test — the suite's contexts are
        // secondary ones, whose autosave defaults to false, which is exactly
        // why this bug was invisible to the existing tests.
        let source = try! String(
            contentsOf: URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .appendingPathComponent("SnapWorth/Services/ScanRepository.swift"),
            encoding: .utf8)
        let catches = source.components(separatedBy: "} catch {").dropFirst()
        XCTAssertEqual(catches.count, 3, "save, delete, deleteAll")
        for (index, block) in catches.enumerated() {
            let body = String(block.prefix(400))
            XCTAssertTrue(body.contains("context.rollback()"),
                          "failure path \(index) leaves the change in the " +
                          "shared context, which breaks every later save")
        }
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

    private func result(at date: Date) -> ScanResult {
        ScanResult(itemName: "Item", brand: "B", category: "clothing",
                   conditionNotes: "Good", valueLow: 10, valueHigh: 20,
                   confidence: "High", soldListingsCount: 0,
                   listingTitle: "T", listingDescription: "D")
        .withTimestamp(date)
    }

    func test_countsOnlyThisMonth() throws {
        let (repo, context) = try repository()
        // Anchored to the month's own boundary, not to "days ago". The
        // original fixture used "yesterday" as an in-month record, which is in
        // the *previous* month whenever the suite runs on the 1st — so this
        // test failed one day a month, on every PR, and looked like the PR's
        // fault. (Found on September 1st, naturally.)
        let cal = Calendar.current
        let startOfMonth = cal.date(
            from: cal.dateComponents([.year, .month], from: Date()))!
        // Two inside the current month on any date the suite runs:
        context.insert(result(at: Date()))
        context.insert(result(at: startOfMonth.addingTimeInterval(3600)))
        // Two clearly outside it: the hour before the month began, and long ago.
        context.insert(result(at: startOfMonth.addingTimeInterval(-3600)))
        context.insert(result(at: startOfMonth.addingTimeInterval(-400 * 86_400)))
        try context.save()

        let count = repo.countScansThisMonth()
        XCTAssertGreaterThanOrEqual(count, 2)
        XCTAssertLessThan(count, 3, "records from before this month must not be counted")
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

        func purchase(productID: String) async throws -> PurchaseOutcome {
            purchaseCalls += 1
            try await Task.sleep(for: .milliseconds(120))
            isSubscribed = true
            return .completed
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

    func test_aStaleListingResponseIsDroppedNotInstalled() {
        // Neither the marketplace chip nor the condition chip is disabled
        // while a generation is in flight — only the Generate button is — and
        // both clear the draft on the way out. So the user could tap Vinted,
        // watch the eBay draft correctly disappear, and then see it reinstate
        // itself when the in-flight response landed: eBay's voice under the
        // Vinted chip, at eBay's Ask and Floor, behind an "Open eBay" button.
        //
        // Source-inspected because the property is an *ordering* one, like its
        // neighbour above: the guard has to sit between the await and the
        // assignment, and a test that drives the happy path cannot show that.
        // Matched on a string that cannot occur in prose, so the explanatory
        // comment beside the guard cannot satisfy this by accident — the
        // mistake a source-inspecting test in SnapWorthTests made earlier.
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

        let needle = "guard requested == selectedMarketplace,"
        let guards = scope.components(separatedBy: needle).count - 1
        XCTAssertEqual(guards, 2,
                       "both the success and the failure path must drop a stale response")

        guard let firstGuard = scope.range(of: needle)?.lowerBound,
              let assign = scope.range(of: "generatedListing = listing")?.lowerBound else {
            return XCTFail("the guard or the assignment is missing")
        }
        XCTAssertLessThan(firstGuard, assign,
                          "the staleness test must run before the listing is installed")

        // And the request must be pinned before the await, not read back from
        // live state afterwards — which is the whole defect.
        guard let pin = scope.range(of: "let requested = selectedMarketplace")?.lowerBound,
              let call = scope.range(of: "try await ListingAPIClient")?.lowerBound else {
            return XCTFail("the pinned marketplace is missing")
        }
        XCTAssertLessThan(pin, call)
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

    /// The only field signal that can ever justify `Config.pinningEnforced = true`.
    /// It says that a mismatch happened and whether it blocked — nothing about
    /// the host or the certificate, which would be PII-adjacent for no gain.
    func test_pinMismatchEventIsBoundedAndSaysWhetherItBlocked() {
        let reportOnly = AnalyticsEvent.certificatePinMismatch(enforced: false)
        XCTAssertEqual(reportOnly.name, "certificate_pin_mismatch")
        XCTAssertEqual(reportOnly.parameters, ["enforced": "false"])
        XCTAssertEqual(AnalyticsEvent.certificatePinMismatch(enforced: true).parameters,
                       ["enforced": "true"])
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

    private func reasons(for category: String) throws -> [String] {
        let accessed = try manifest()["NSPrivacyAccessedAPITypes"] as? [[String: Any]] ?? []
        let entry = accessed.first { $0["NSPrivacyAccessedAPIType"] as? String == category }
        return entry?["NSPrivacyAccessedAPITypeReasons"] as? [String] ?? []
    }

    /// The manifest declared the right *categories* with the wrong *reasons*,
    /// and nothing checked the reasons — which is how it went unnoticed.
    func test_userDefaultsDeclaresTheAppGroupReason() throws {
        let declared = try reasons(for: "NSPrivacyAccessedAPICategoryUserDefaults")
        // The widget extension reads WidgetDataStore's shared suite, so the
        // app-group reason is required alongside the app-private one.
        XCTAssertTrue(declared.contains("CA92.1"), "missing the app-private reason")
        XCTAssertTrue(declared.contains("1C8F.1"),
                      "the widget shares \(WidgetDataStore.appGroupID); 1C8F.1 is required")
    }

    func test_fileTimestampReasonIsTheFirstPartyOne() throws {
        let declared = try reasons(for: "NSPrivacyAccessedAPICategoryFileTimestamp")
        XCTAssertEqual(declared, ["C617.1"],
                       "0A2A.1 is the third-party-SDK-wrapper reason; this app reads its own container")
    }
}

// MARK: - Money typed on a comma-decimal keypad

/// `Double("12,50")` is nil, and every money field wrote `Double(newValue)`
/// straight onto the model on each keystroke — so on a German, French,
/// Romanian or Brazilian keypad the amount silently vanished.
final class MoneyInputTests: XCTestCase {
    func test_pointAndCommaBothParse() {
        XCTAssertEqual(MoneyInput.parse("12.50"), 12.5)
        XCTAssertEqual(MoneyInput.parse("12,50"), 12.5)
        XCTAssertEqual(MoneyInput.parse(" 12,50 "), 12.5)
        XCTAssertEqual(MoneyInput.parse("8"), 8)
    }

    /// The comma cannot simply be folded: "$1,250" is a real thing a US user
    /// types, and folding reads it as 1.25.
    func test_aLoneCommaIsGroupingWhenThreeDigitsFollow() {
        XCTAssertEqual(MoneyInput.parse("1,250"), 1250)
        XCTAssertEqual(MoneyInput.parse("1,234,567"), 1234567)
        XCTAssertEqual(MoneyInput.parse("12,5"), 12.5, "one digit is a decimal")
        XCTAssertEqual(MoneyInput.parse("12,50"), 12.5, "two digits is a decimal")
    }

    /// With both separators present the last one is the decimal, so each
    /// writing convention lands on the same number.
    func test_bothSeparatorsResolveByPosition() {
        XCTAssertEqual(MoneyInput.parse("1.234,56"), 1234.56)
        XCTAssertEqual(MoneyInput.parse("1,234.56"), 1234.56)
    }

    func test_currencySymbolsAreIgnored() {
        XCTAssertEqual(MoneyInput.parse("$45.50"), 45.5)
        XCTAssertEqual(MoneyInput.parse("44,99 €"), 44.99)
    }

    func test_emptyAndJunkAreNil() {
        XCTAssertNil(MoneyInput.parse(""))
        XCTAssertNil(MoneyInput.parse("   "))
        XCTAssertNil(MoneyInput.parse("abc"))
        XCTAssertNil(MoneyInput.parse("."), "a separator alone is not a number")
        XCTAssertNil(MoneyInput.parse(","))
    }

    func test_decimalVariantMatches() {
        XCTAssertEqual(MoneyInput.decimal("12,50"), Decimal(string: "12.50"))
        XCTAssertNil(MoneyInput.decimal(""))
    }

    /// The guess field stripped the comma rather than folding it, so "12,50"
    /// scored as 1250 — a hundredfold-wrong guess, worse than refusing it.
    func test_guessParsingReadsTheCommaRatherThanStrippingIt() {
        XCTAssertEqual(GuessScoring.parse("12,50"), 12.5)
        XCTAssertEqual(GuessScoring.parse("$12.50"), 12.5)
        XCTAssertEqual(GuessScoring.parse("$1,250"), 1250, "still a thousands separator")
        XCTAssertNil(GuessScoring.parse("-5"))
    }
}

// MARK: - Capture resolution

/// `maxPhotoDimensions` was hard-coded to 4032x3024. AVFoundation aborts the
/// process for a value the active format does not list, and this target
/// installs on the 8MP iPads in compatibility mode.
final class PhotoDimensionTests: XCTestCase {
    private func dims(_ w: Int32, _ h: Int32) -> CMVideoDimensions {
        CMVideoDimensions(width: w, height: h)
    }

    func test_picksTheTwelveMegapixelOptionWhenOffered() {
        let chosen = CameraManager.preferredPhotoDimensions(
            [dims(1920, 1080), dims(4032, 3024)])
        XCTAssertEqual(chosen?.width, 4032)
        XCTAssertEqual(chosen?.height, 3024)
    }

    /// A 48MP Pro camera lists 8064x6048. Taking the maximum would quadruple
    /// decode cost and memory for an image downscaled to 1568px before it
    /// leaves the device.
    func test_doesNotClimbAboveTheCap() {
        let chosen = CameraManager.preferredPhotoDimensions(
            [dims(4032, 3024), dims(8064, 6048)])
        XCTAssertEqual(chosen?.width, 4032)
    }

    /// The iPad case: nothing at or above 12MP, so take the largest on offer
    /// rather than a value the format would reject.
    func test_fallsBackToTheLargestOnEightMegapixelHardware() {
        let chosen = CameraManager.preferredPhotoDimensions(
            [dims(1920, 1080), dims(3264, 2448)])
        XCTAssertEqual(chosen?.width, 3264)
        XCTAssertEqual(chosen?.height, 2448)
    }

    func test_nilWhenTheFormatListsNothing() {
        XCTAssertNil(CameraManager.preferredPhotoDimensions([]))
    }
}

// MARK: - Paywall benefits

/// The benefits list named none of the gates that actually present the
/// paywall, and led with "Full scan history", which is not gated at all.
final class PaywallBenefitsTests: XCTestCase {
    func test_everyRowNamesSomethingReal() {
        let texts = PaywallCopy.benefits.map(\.text)
        XCTAssertFalse(texts.contains { $0.localizedCaseInsensitiveContains("scan history") },
                       "scan history is not gated — HistoryView's grid has no isPro check")
        XCTAssertTrue(texts.contains { $0.localizedCaseInsensitiveContains("unlimited scans") })
        XCTAssertTrue(texts.contains { $0.localizedCaseInsensitiveContains("thrift flip") })
        XCTAssertTrue(texts.contains { $0.localizedCaseInsensitiveContains("tag") })
        XCTAssertTrue(texts.contains { $0.localizedCaseInsensitiveContains("export") })
    }

    func test_rowsAreDistinctAndNonEmpty() {
        let texts = PaywallCopy.benefits.map(\.text)
        XCTAssertEqual(Set(texts).count, texts.count)
        XCTAssertFalse(texts.contains(where: \.isEmpty))
        XCTAssertFalse(PaywallCopy.benefits.contains { $0.icon.isEmpty })
    }
}

// MARK: - EXIF / GPS on the upload path
//
// A photo of your belongings, taken at home, carries your home coordinates in
// its EXIF GPS IFD (tag 0x8825). If that reaches the backend it is a location
// leak from an app that never asks for location permission — and it would be
// invisible, because nothing in the UI mentions it.
//
// The suspicion was specific: `downscale` returns the *original* UIImage
// unchanged when the longest edge is already <= maxEdge, so a small library
// pick skips the redraw entirely. If EXIF rode along with the UIImage, that
// path would forward it.
//
// These tests settle it against the real types rather than by reasoning about
// them. The fixture is a genuine JPEG carrying a real GPS dictionary written by
// ImageIO, and `test_fixtureItselfCarriesGPS` exists so the others cannot pass
// vacuously — without it, a fixture that silently lost its GPS at construction
// would make every assertion below trivially true.

final class UploadEXIFStrippingTests: XCTestCase {

    /// A real JPEG carrying a GPS IFD, built with ImageIO.
    private func geotaggedJPEG(width: Int, height: Int) -> Data {
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1
        let image = UIGraphicsImageRenderer(
            size: CGSize(width: width, height: height), format: format
        ).image { ctx in
            UIColor.systemTeal.setFill()
            ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
            UIColor.systemPink.setFill()
            ctx.fill(CGRect(x: 0, y: 0, width: width / 2, height: height / 2))
        }

        let out = NSMutableData()
        let dest = CGImageDestinationCreateWithData(
            out, "public.jpeg" as CFString, 1, nil)!

        // Apple Park, to a precision that would identify a home.
        let gps: [CFString: Any] = [
            kCGImagePropertyGPSLatitude: 37.334886,
            kCGImagePropertyGPSLatitudeRef: "N",
            kCGImagePropertyGPSLongitude: 122.008988,
            kCGImagePropertyGPSLongitudeRef: "W",
            kCGImagePropertyGPSAltitude: 56.0,
        ]
        let exif: [CFString: Any] = [
            kCGImagePropertyExifDateTimeOriginal: "2026:08:05 14:30:00",
            kCGImagePropertyExifUserComment: "should-not-survive",
        ]
        CGImageDestinationAddImage(dest, image.cgImage!, [
            kCGImagePropertyGPSDictionary: gps,
            kCGImagePropertyExifDictionary: exif,
        ] as CFDictionary)
        CGImageDestinationFinalize(dest)
        return out as Data
    }

    /// Reads the GPS dictionary back out of encoded image data, if present.
    private func gpsDictionary(of data: Data) -> [String: Any]? {
        guard let src = CGImageSourceCreateWithData(data as CFData, nil),
              let props = CGImageSourceCopyPropertiesAtIndex(src, 0, nil)
                as? [String: Any] else { return nil }
        return props[kCGImagePropertyGPSDictionary as String] as? [String: Any]
    }

    // MARK: Guard against a vacuous suite

    func test_fixtureItselfCarriesGPS() throws {
        let data = geotaggedJPEG(width: 800, height: 600)
        let gps = try XCTUnwrap(gpsDictionary(of: data), """
            The fixture lost its GPS at construction, so every other test in \
            this class would pass without proving anything.
            """)
        let latitude = try XCTUnwrap(
            gps[kCGImagePropertyGPSLatitude as String] as? Double)
        XCTAssertEqual(latitude, 37.334886, accuracy: 0.000001)
    }

    // MARK: The path that skips the redraw

    func test_smallImage_takesTheNoDownscalePath() {
        // Establishes the precondition for the test below: at 800x600 the
        // longest edge is under 1568, so `downscale` returns the input object
        // itself and no re-render happens.
        let source = UIImage(data: geotaggedJPEG(width: 800, height: 600))!
        let result = ScanAPIClient.downscale(source, maxEdge: ScanAPIClient.maxUploadEdge)
        XCTAssertTrue(result === source, "expected the original instance back")
    }

    func test_smallGeotaggedImage_uploadsWithoutGPS() async {
        let source = UIImage(data: geotaggedJPEG(width: 800, height: 600))!
        let encoded = await ScanAPIClient.encodeForUpload(source)
        XCTAssertNotNil(encoded)
        XCTAssertNil(gpsDictionary(of: encoded!),
                     "GPS survived the no-downscale upload path")
    }

    // MARK: The path that does redraw

    func test_largeGeotaggedImage_uploadsWithoutGPS() async {
        let source = UIImage(data: geotaggedJPEG(width: 3000, height: 2000))!
        let encoded = await ScanAPIClient.encodeForUpload(source)
        XCTAssertNotNil(encoded)
        XCTAssertNil(gpsDictionary(of: encoded!),
                     "GPS survived the downscaling upload path")
    }

    // MARK: The copy persisted to disk

    func test_storedCopyCarriesNoGPS() async {
        let source = UIImage(data: geotaggedJPEG(width: 800, height: 600))!
        let encoded = await ScanAPIClient.encodeForStorage(source)
        XCTAssertNotNil(encoded)
        XCTAssertNil(gpsDictionary(of: encoded!),
                     "GPS survived into the on-disk copy")
    }

    // MARK: Nothing else rides along either

    func test_noExifUserCommentSurvives() async {
        let source = UIImage(data: geotaggedJPEG(width: 800, height: 600))!
        let encoded = await ScanAPIClient.encodeForUpload(source)!
        guard let src = CGImageSourceCreateWithData(encoded as CFData, nil),
              let props = CGImageSourceCopyPropertiesAtIndex(src, 0, nil)
                as? [String: Any] else {
            return XCTFail("could not read back encoded image properties")
        }
        let exif = props[kCGImagePropertyExifDictionary as String] as? [String: Any]
        let comment = exif?[kCGImagePropertyExifUserComment as String] as? String
        XCTAssertNil(comment, "an EXIF user comment survived re-encoding")
    }
}

// MARK: - SwiftData store protection
//
// The store holds every scan a user has ever taken — item names, valuations,
// timestamps and a photo of each item. Nothing set a protection class
// explicitly, so the guarantee was whatever the platform happened to default
// to rather than something this app had decided.
//
// `.completeUntilFirstUserAuthentication` rather than `.complete`: the stronger
// class makes files unreadable whenever the device is locked, which would break
// any work happening with the screen off. This one still leaves the store
// encrypted at rest and unreadable until the first unlock after boot, which is
// the lost-or-stolen-phone case that actually matters.

final class StoreProtectionTests: XCTestCase {

    private var directory: URL!

    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(
            at: directory, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: directory)
    }

    private func makeStoreFiles(_ names: [String]) throws -> URL {
        let store = directory.appendingPathComponent("default.store")
        for name in names {
            let url = URL(fileURLWithPath: store.path + name)
            try Data("x".utf8).write(to: url)
        }
        return store
    }

    // NOTE ON WHAT THESE CAN AND CANNOT PROVE
    //
    // The resulting protection class is NOT asserted, because it cannot be
    // observed here. The Simulator runs on the host's APFS volume, which has no
    // iOS data-protection classes: `setAttributes(_:ofItemAtPath:)` reports
    // success and the attribute is then silently dropped, so reading it back
    // returns nil. An assertion on the class would fail on the Simulator while
    // the code is correct — and, worse, an assertion written to pass here would
    // prove nothing about a device.
    //
    // What is asserted instead: that the right set of files is targeted, and
    // that setting the attribute succeeds on each of them (`apply` only returns
    // URLs for which `setAttributes` did not throw). The class itself is
    // verifiable only on real hardware — see the class doc for how.

    func test_targetsTheStoreAndBothSqliteSiblings() {
        let store = URL(fileURLWithPath: "/tmp/x/default.store")
        let targets = StoreProtection.siblings(of: store).map(\.lastPathComponent)
        XCTAssertEqual(targets, ["default.store", "default.store-wal", "default.store-shm"])
    }

    func test_appliesToTheStoreFileWithoutError() throws {
        let store = try makeStoreFiles([""])
        XCTAssertEqual(StoreProtection.apply(to: store), [store])
    }

    func test_appliesToWalAndShmSiblings() throws {
        // The -wal holds the most recent writes — the newest scans. Protecting
        // only the .store file would leave exactly those readable.
        let store = try makeStoreFiles(["", "-wal", "-shm"])
        let updated = StoreProtection.apply(to: store)
        XCTAssertEqual(updated.count, 3, "expected store, -wal and -shm")
        XCTAssertEqual(Set(updated.map(\.lastPathComponent)),
                       ["default.store", "default.store-wal", "default.store-shm"])
    }

    func test_missingSiblingsAreSkippedNotFatal() throws {
        // A freshly created store has no -wal until the first write.
        let store = try makeStoreFiles([""])
        XCTAssertEqual(StoreProtection.apply(to: store).count, 1)
    }

    func test_applyIsSafeWhenNothingExists() {
        let ghost = directory.appendingPathComponent("absent.store")
        XCTAssertEqual(StoreProtection.apply(to: ghost), [],
                       "must not throw or crash when the store is absent")
    }

    func test_levelIsNotCompleteWhichWouldBreakBackgroundWork() {
        // Pins the choice rather than the mechanism: if someone later tightens
        // this to `.complete`, that is a behavioural decision that should fail
        // a test and be argued for, not slip through.
        XCTAssertEqual(StoreProtection.level, .completeUntilFirstUserAuthentication)
        XCTAssertNotEqual(StoreProtection.level, .complete)
    }
}


// MARK: - 422 routing
//
// The backend returns 422 when it looked at the photo and could not use it —
// a safety block, or an image it cannot read. That reply carries copy telling
// the user what to change ("try a clear photo of a single item").
//
// Two bugs met here. Server-side, safety blocks were never detected at all,
// because the finish-reason helper could not read the SDK's proto container,
// so blocks surfaced as 502 "AI unavailable". Client-side, 422 fell through to
// `.unknown` and printed "Something went wrong" — so even once the server said
// the right thing, the user was told nothing and retried the same photo.

final class UnusablePhotoMappingTests: XCTestCase {
    private let blocked = "This photo couldn't be analysed. Try a clear photo of a single item."

    func test_422_surfacesTheServerExplanation() {
        let mapped = AppError.from(ScanAPIError.serverError(422, blocked))
        XCTAssertEqual(mapped, .unusablePhoto(blocked))
        XCTAssertEqual(mapped.errorDescription, blocked)
    }

    func test_422_doesNotSaySomethingWentWrong() {
        // The regression this exists to prevent.
        let message = AppError.from(ScanAPIError.serverError(422, blocked)).errorDescription ?? ""
        XCTAssertFalse(message.contains("Something went wrong"))
    }

    func test_422_isNotTreatedAsAnOutage() {
        // A blocked photo is actionable by the user; "our AI is unavailable" is
        // neither true nor actionable, and invites an identical retry.
        let mapped = AppError.from(ScanAPIError.serverError(422, blocked))
        XCTAssertNotEqual(mapped, .serverUnavailable)
    }

    func test_differentExplanationsAreNotEqual() {
        // Compared by message, so a changed reason re-presents the alert.
        let a = AppError.from(ScanAPIError.serverError(422, blocked))
        let b = AppError.from(ScanAPIError.serverError(422, "Could not read this image."))
        XCTAssertNotEqual(a, b)
    }

    // ── 502 detail is surfaced, not replaced (issue #55) ─────────────────
    //
    // The backend raises 502 for four different reasons and writes distinct,
    // user-safe copy for each. The client used to collapse all of them into
    // "Our AI is temporarily unavailable" — so a user who photographed
    // something unpriceable was told the service was down, retried the
    // identical photo, and failed identically. These four strings are the
    // ones main.py actually sends; the assertions are the issue's acceptance
    // criteria.

    func test_502_unpriceableItem_doesNotClaimAnOutage() {
        let mapped = AppError.from(
            ScanAPIError.serverError(502, "The AI couldn't price this item."))
        let msg = mapped.errorDescription ?? ""
        XCTAssertEqual(msg, "The AI couldn't price this item.")
        XCTAssertFalse(msg.lowercased().contains("unavailable"),
                       "Nothing is down — the copy must not claim an outage")
        XCTAssertNotEqual(mapped, .serverUnavailable)
    }

    func test_502_genuineOutage_stillReadsAsAnOutage() {
        // The backend's own outage copy says "temporarily unavailable", so
        // surfacing it verbatim keeps the outage reading as one.
        let msg = AppError.from(
            ScanAPIError.serverError(502, "The AI service is temporarily unavailable."))
            .errorDescription ?? ""
        XCTAssertTrue(msg.lowercased().contains("temporarily unavailable"))
    }

    func test_502_unreadableResponse_surfacesTheRetryableExplanation() {
        let msg = AppError.from(
            ScanAPIError.serverError(502, "The AI response couldn't be read."))
            .errorDescription ?? ""
        XCTAssertEqual(msg, "The AI response couldn't be read.")
    }

    func test_502_emptyDetail_fallsBackToTheGenericString() {
        XCTAssertEqual(AppError.from(ScanAPIError.serverError(502, "")),
                       .serverUnavailable)
    }

    func test_503_stillReadsAsAnOutage() {
        // A 503 really is the service refusing traffic; the detail-surfacing
        // change is scoped to 502 only.
        XCTAssertEqual(AppError.from(ScanAPIError.serverError(503, "anything")),
                       .serverUnavailable)
    }

    func test_aiFailed_equalsItselfAndComparesByMessage() {
        // The manual == has already silently dropped one newly added case
        // (.sessionExpired); every new case gets pinned here so it cannot
        // happen again.
        XCTAssertEqual(AppError.aiFailed("a"), AppError.aiFailed("a"))
        XCTAssertNotEqual(AppError.aiFailed("a"), AppError.aiFailed("b"))
        XCTAssertNotEqual(AppError.aiFailed("a"), .serverUnavailable)
    }
}

// MARK: - 401 recovery
//
// A 401 means the token we attached is no longer acceptable. `accessToken()`
// returns the cached token whenever it is not within a minute of expiry, so
// without clearing it first every retry re-sends the same dead credential and
// fails identically for the full hour of the token's lifetime.
//
// This was not theoretical: the backend ran with an ephemeral TOKEN_KEYS, so
// each deploy rotated the signing key and signed out every active user for an
// hour, while the alert told them to "pull to retry — it should reconnect
// automatically". It did not.

final class SessionExpiredCopyTests: XCTestCase {
    func test_doesNotPromiseAnAutomaticReconnect() {
        // By the time this message is shown, sendRetryingAuth has already
        // re-minted and retried. Telling the user to retry for an automatic
        // reconnect describes something that has just failed.
        let message = AppError.sessionExpired.errorDescription ?? ""
        XCTAssertFalse(message.lowercased().contains("automatically"),
                       "copy still promises a reconnect the client already attempted")
    }

    func test_offersAnActionableRemedy() {
        let message = AppError.sessionExpired.errorDescription ?? ""
        XCTAssertFalse(message.isEmpty)
        #if targetEnvironment(simulator)
        // App Attest does not exist here, so the only remedy is a real device;
        // suggesting a reinstall in the Simulator would be a promise that
        // cannot be kept.
        XCTAssertTrue(message.contains("real iPhone"), message)
        XCTAssertFalse(message.lowercased().contains("reinstall"), message)
        #else
        XCTAssertTrue(message.lowercased().contains("reinstall"),
                      "should name the remedy that actually clears a bad credential")
        #endif
    }

    func test_401_stillMapsToSessionExpired() {
        XCTAssertEqual(AppError.from(ScanAPIError.serverError(401, "unauthorized")),
                       .sessionExpired)
    }

    func test_everyPayloadFreeCaseEqualsItself() {
        // How the .sessionExpired bug was found: it was left out of the `==`
        // switch when the case was added, so it fell to `default: false` and
        // did not equal itself, while still *printing* identically. Enumerated
        // here so the next case added cannot repeat it.
        let cases: [AppError] = [
            .network, .timeout, .rateLimit(retryAfter: nil), .serverUnavailable, .sessionExpired,
            .imageEncodingFailed, .purchaseCancelled, .persistence,
        ]
        for value in cases {
            XCTAssertEqual(value, value, "\(value) does not equal itself")
        }
    }

    func test_401_isDistinctFromAnOutageAndFromABlockedPhoto() {
        let expired = AppError.from(ScanAPIError.serverError(401, "unauthorized"))
        XCTAssertNotEqual(expired, .serverUnavailable)
        XCTAssertNotEqual(expired, .unusablePhoto("nope"))
    }
}

// MARK: - Device identity

/// `DeviceIdentity` is what lets the server count phones rather than installs.
/// Its contract: the same store always yields the same id, an existing install's
/// `UserDefaults` id is adopted rather than replaced, and a store that cannot
/// persist still produces a usable id.
final class DeviceIdentityTests: XCTestCase {
    private final class MemoryStore: DeviceIdentityStore {
        var value: String?
        var writable = true
        func read() -> String? { value }
        func write(_ new: String) -> Bool {
            guard writable else { return false }
            value = new
            return true
        }
    }

    private func freshDefaults() -> UserDefaults {
        let suite = "DeviceIdentityTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defaults.removePersistentDomain(forName: suite)
        return defaults
    }

    func test_idIsAUUIDAndStableAcrossInstances() {
        let store = MemoryStore()
        let defaults = freshDefaults()
        let first = DeviceIdentity(store: store, defaults: defaults).id
        let second = DeviceIdentity(store: store, defaults: defaults).id

        XCTAssertNotNil(UUID(uuidString: first))
        XCTAssertEqual(first, second, "a new instance over the same store must not mint a new id")
        XCTAssertEqual(store.value, first, "the id is persisted where reinstall cannot delete it")
    }

    func test_adoptsTheLegacyUserDefaultsId() {
        // An install upgrading from 1.3.3 already has a UserDefaults id that the
        // server knows for rate limiting. Minting a new one would reset that.
        let store = MemoryStore()
        let defaults = freshDefaults()
        defaults.set("LEGACY-ID-1234", forKey: DeviceIdentity.legacyDefaultsKey)

        XCTAssertEqual(DeviceIdentity(store: store, defaults: defaults).id, "LEGACY-ID-1234")
        XCTAssertEqual(store.value, "LEGACY-ID-1234", "migrated into the durable store")
    }

    func test_storedIdWinsOverUserDefaults() {
        // After migration the durable store is authoritative; a stale or
        // differing UserDefaults value must not flip the identity.
        let store = MemoryStore()
        store.value = "KEYCHAIN-ID"
        let defaults = freshDefaults()
        defaults.set("OTHER-ID", forKey: DeviceIdentity.legacyDefaultsKey)

        XCTAssertEqual(DeviceIdentity(store: store, defaults: defaults).id, "KEYCHAIN-ID")
    }

    func test_unwritableStoreStillYieldsAnIdAndKeepsItInDefaults() {
        // The Keychain can refuse a write before first unlock. The request
        // still needs a device id, and the next launch must find the same one.
        let store = MemoryStore()
        store.writable = false
        let defaults = freshDefaults()

        let id = DeviceIdentity(store: store, defaults: defaults).id
        XCTAssertNotNil(UUID(uuidString: id))
        XCTAssertEqual(defaults.string(forKey: DeviceIdentity.legacyDefaultsKey), id)
        XCTAssertEqual(DeviceIdentity(store: store, defaults: defaults).id, id)
    }

    func test_isCachedAfterFirstRead() {
        let store = MemoryStore()
        let identity = DeviceIdentity(store: store, defaults: freshDefaults())
        let first = identity.id
        store.value = "CHANGED-UNDERNEATH"
        XCTAssertEqual(identity.id, first, "read once per process; the store is not re-queried")
    }

    // ── The mirror must not outlive its purpose ──────────────────────────────

    func test_theDefaultsMirrorIsRemovedOnceTheKeychainHoldsIt() {
        // The two stores migrate in opposite directions, and the whole design
        // rests on the Keychain's: `ThisDeviceOnly` is excluded from encrypted
        // backups and Quick Start, while Library/Preferences/…plist is
        // included in both. A copy left in UserDefaults is therefore exactly
        // the carrier this store exists to prevent.
        let store = MemoryStore()
        let defaults = freshDefaults()

        let id = DeviceIdentity(store: store, defaults: defaults).id
        XCTAssertEqual(store.value, id, "the durable copy is the one that stays")
        XCTAssertNil(defaults.string(forKey: DeviceIdentity.legacyDefaultsKey),
                     "a surviving mirror migrates this identity to a restored " +
                     "phone, collapsing every restored device onto one binding " +
                     "slot and bypassing the subscription device cap without bound")
    }

    func test_aRestoredBackupYieldsADistinctIdentity() {
        // The attack, played out. "Restoring" carries the UserDefaults plist
        // and not the ThisDeviceOnly Keychain item, so the new phone starts
        // with an empty store and whatever defaults migrated.
        let original = freshDefaults()
        let id = DeviceIdentity(store: MemoryStore(), defaults: original).id

        let restoredDefaults = freshDefaults()
        for (key, value) in original.dictionaryRepresentation() {
            restoredDefaults.set(value, forKey: key)     // the backup
        }
        let restoredID = DeviceIdentity(store: MemoryStore(),   // Keychain did not travel
                                        defaults: restoredDefaults).id

        XCTAssertNotEqual(restoredID, id,
                          "two phones restored from one backup are two devices")
    }
}
// The paired case — the mirror staying when it is the *only* copy — is already
// covered by `test_unwritableStoreStillYieldsAnIdAndKeepsItInDefaults` above,
// which is why the removal is conditional rather than the write being dropped.


// MARK: - Free-scan reminder and streak (#94)

final class ScanStreakTests: XCTestCase {
    private var defaults: UserDefaults!
    private let cal = Calendar(identifier: .gregorian)

    override func setUp() {
        super.setUp()
        defaults = UserDefaults(suiteName: "ScanStreakTests-\(UUID().uuidString)")
    }

    private func day(_ n: Int, hour: Int = 12) -> Date {
        cal.date(from: DateComponents(year: 2026, month: 9, day: n, hour: hour))!
    }

    func test_consecutiveDaysGrowTheStreak_sameDayDoesNot() {
        XCTAssertEqual(ScanStreak.record(now: day(1), defaults: defaults, calendar: cal), 1)
        XCTAssertEqual(ScanStreak.record(now: day(1, hour: 20), defaults: defaults, calendar: cal), 1,
                       "a second scan the same day is not a second day")
        XCTAssertEqual(ScanStreak.record(now: day(2), defaults: defaults, calendar: cal), 2)
        XCTAssertEqual(ScanStreak.record(now: day(3), defaults: defaults, calendar: cal), 3)
        XCTAssertEqual(ScanStreak.current(now: day(3, hour: 23), defaults: defaults, calendar: cal), 3)
    }

    func test_streakSurvivesUntilTheEndOfTheNextDay_thenRestarts() {
        ScanStreak.record(now: day(1), defaults: defaults, calendar: cal)
        ScanStreak.record(now: day(2), defaults: defaults, calendar: cal)
        // The morning after, before today's scan: still alive.
        XCTAssertEqual(ScanStreak.current(now: day(3, hour: 8), defaults: defaults, calendar: cal), 2)
        // Two days later with no scan: gone, quietly.
        XCTAssertEqual(ScanStreak.current(now: day(4), defaults: defaults, calendar: cal), 0)
        // A scan after the gap starts over at one.
        XCTAssertEqual(ScanStreak.record(now: day(4), defaults: defaults, calendar: cal), 1)
    }

    func test_scannedTodayIsTierAgnostic() {
        XCTAssertFalse(ScanStreak.scannedToday(now: day(1), defaults: defaults, calendar: cal))
        ScanStreak.record(now: day(1), defaults: defaults, calendar: cal)
        XCTAssertTrue(ScanStreak.scannedToday(now: day(1, hour: 23), defaults: defaults, calendar: cal))
        XCTAssertFalse(ScanStreak.scannedToday(now: day(2), defaults: defaults, calendar: cal))
    }

    func test_bucketsNeverLeakTheExactCount() {
        XCTAssertEqual(ScanStreak.bucket(0), "1")
        XCTAssertEqual(ScanStreak.bucket(1), "1")
        XCTAssertEqual(ScanStreak.bucket(3), "2-3")
        XCTAssertEqual(ScanStreak.bucket(6), "4-6")
        XCTAssertEqual(ScanStreak.bucket(40), "7+")
        XCTAssertEqual(AnalyticsEvent.scanStreak(bucket: "2-3").name, "scan_streak")
        XCTAssertEqual(AnalyticsEvent.scanStreak(bucket: "2-3").parameters, ["bucket": "2-3"])
    }
}

final class FreeScanReminderTests: XCTestCase {
    private let cal = Calendar(identifier: .gregorian)
    private func at(_ day: Int, _ hour: Int, _ minute: Int = 0) -> Date {
        cal.date(from: DateComponents(year: 2026, month: 9, day: day, hour: hour, minute: minute))!
    }

    func test_todayAtTheChosenTimeWhenStillAheadAndUnscanned() {
        let fire = NotificationManager.nextFreeScanDate(after: at(3, 9), hour: 18, minute: 30,
                                                        scannedToday: false, calendar: cal)
        XCTAssertEqual(fire, at(3, 18, 30))
    }

    func test_tomorrowWhenTheTimeHasPassed() {
        let fire = NotificationManager.nextFreeScanDate(after: at(3, 19), hour: 18, minute: 0,
                                                        scannedToday: false, calendar: cal)
        XCTAssertEqual(fire, at(4, 18))
    }

    func test_tomorrowWhenTodayIsAlreadyScanned() {
        // 09:00, reminder at 18:00, but the free scan is spent: no nudge today.
        let fire = NotificationManager.nextFreeScanDate(after: at(3, 9), hour: 18, minute: 0,
                                                        scannedToday: true, calendar: cal)
        XCTAssertEqual(fire, at(4, 18))
    }

    func test_copyNamesTheStreakOnlyWhenThereIsOne() {
        XCTAssertEqual(NotificationManager.freeScanBody(streak: 0),
                       "Your free scan is back. What did you find today?")
        XCTAssertEqual(NotificationManager.freeScanBody(streak: 1),
                       "Your free scan is back. What did you find today?")
        XCTAssertEqual(NotificationManager.freeScanBody(streak: 4),
                       "Day 5 of your streak is waiting — your free scan is back.")
    }

    func test_categoryIsOptInAndSitsBelowTheWeeklyDigest() {
        XCTAssertFalse(NotificationManager.Category.freeScan.defaultEnabled)
        for other in NotificationManager.Category.allCases where other != .freeScan {
            XCTAssertTrue(other.defaultEnabled, "\(other) must stay on by default")
        }
        XCTAssertLessThan(NotificationManager.Category.freeScan.priority,
                          NotificationManager.Category.portfolio.priority)
        XCTAssertGreaterThan(NotificationManager.Category.freeScan.priority,
                             NotificationManager.Category.recap.priority)
        XCTAssertEqual(NotificationManager.Category.freeScan.toggleKey, "notif_freeScan_enabled")
    }
}


final class FreeScanReminderIdentifierTests: XCTestCase {
    /// The daily cap recovers a request's category from its identifier prefix,
    /// so the free-scan id must start with the category's raw value exactly.
    func test_identifierPrefixMatchesTheCategory() {
        XCTAssertEqual(NotificationManager.Category.freeScan.rawValue, "freeScan")
        XCTAssertEqual(NotificationManager.Category(rawValue: "freeScan.daily".components(separatedBy: ".")[0]),
                       .freeScan)
    }
}


// MARK: - Guess the price (#95)

final class GuessScoringTests: XCTestCase {
    func test_insideTheRangeIsAWin() {
        XCTAssertEqual(GuessScoring.verdict(guess: 60, low: 45, high: 90),
                       "Spot on — your guess is inside the estimate.")
        XCTAssertEqual(GuessScoring.verdict(guess: 45, low: 45, high: 90),
                       "Spot on — your guess is inside the estimate.", "the ends count")
    }

    func test_outsideSaysHowFarFromTheNearerEnd() {
        XCTAssertEqual(GuessScoring.verdict(guess: 20, low: 45, high: 90), "$25 under the low end.")
        XCTAssertEqual(GuessScoring.verdict(guess: 130, low: 45, high: 90), "$40 over the high end.")
        // A swapped range is handled rather than trusted.
        XCTAssertEqual(GuessScoring.verdict(guess: 20, low: 90, high: 45), "$25 under the low end.")
    }

    func test_aNonZeroMissIsNeverReportedAsZero() {
        // The bounds are fractional as a matter of course — `priceRange(for:)`
        // scales stored values by a condition factor — while the range on
        // screen is whole dollars. Scored raw, a guess of $45 against a
        // printed "$45–$90" whose real low is 45.40 was outside the range by
        // 40 cents, and 40 cents through a 0-decimal formatter is "$0 under
        // the low end.": a non-zero miss reported as zero, directly under a
        // range the guess appears to match exactly.
        XCTAssertEqual(GuessScoring.verdict(guess: 45, low: 45.40, high: 90.20),
                       "Spot on — your guess is inside the estimate.")
        XCTAssertFalse(
            GuessScoring.verdict(guess: 45, low: 45.40, high: 90.20).contains("$0"),
            "no verdict may ever say $0")
    }

    func test_twoGuessesTheUserCannotTellApartGetTheSameVerdict() {
        // Both print "$45–$90". Scored raw, the first was outside and the
        // second inside — a difference the user has no way to see.
        let above = GuessScoring.verdict(guess: 45, low: 45.40, high: 90.20)
        let below = GuessScoring.verdict(guess: 45, low: 44.60, high: 90.20)
        XCTAssertEqual(above, below)
    }

    func test_aRealMissIsStillReportedAsAMiss() {
        // The guard against over-correcting: rounding the bounds must not
        // swallow a miss the user can see.
        XCTAssertEqual(GuessScoring.verdict(guess: 44, low: 45.40, high: 90.20),
                       "$1 under the low end.")
        XCTAssertEqual(GuessScoring.verdict(guess: 91, low: 45.40, high: 90.20),
                       "$1 over the high end.")
        XCTAssertEqual(GuessScoring.verdict(guess: 20, low: 45.40, high: 90.20),
                       "$25 under the low end.")
    }

    func test_parseIsForgivingAboutWhatPeopleType() {
        XCTAssertEqual(GuessScoring.parse("$1,250"), 1250)
        XCTAssertEqual(GuessScoring.parse(" 45.5 "), 45.5)
        XCTAssertNil(GuessScoring.parse(""))
        XCTAssertNil(GuessScoring.parse("lots"))
        XCTAssertNil(GuessScoring.parse("-5"), "a negative guess is not a guess")
        XCTAssertNil(GuessScoring.parse("."))
    }

    func test_analyticsCarryOnlyStyleAndABoolean() {
        XCTAssertEqual(AnalyticsEvent.guessRevealed(withGuess: true).name, "guess_revealed")
        XCTAssertEqual(AnalyticsEvent.guessRevealed(withGuess: false).parameters, ["with_guess": "false"])
        XCTAssertEqual(AnalyticsEvent.guessCardShared(style: "pair").name, "guess_card_shared")
        XCTAssertEqual(AnalyticsEvent.guessCardShared(style: "pair").parameters, ["style": "pair"])
    }
}


// MARK: - Why this price (#87)

final class ValuationDetailTests: XCTestCase {
    private let v1 = """
    {"item_name":"Patagonia Better Sweater","brand":"Patagonia","category":"clothing",
     "condition_notes":"Good","est_value_low_usd":45.0,"est_value_high_usd":90.0,
     "confidence":"High","listing_title":"T","listing_description":"D"}
    """

    private var v2: String {
        v1.replacingOccurrences(of: #""confidence":"High","#, with: #"""
        "confidence":"High","confidence_score":72,"confidence_summary":"Brand and model are legible.",
        "confidence_reasons":["Logo visible","Common item","Clear photo","Fourth reason"],
        "quick_sale_price_usd":45,"expected_price_usd":58,"best_case_price_usd":90,"worst_case_price_usd":40,
        "value_drivers":["Classic colourway"],"assumptions":["Size M"],"uncertainty_factors":["Pilling not visible"],
        "improve_estimate":["Photograph the tag"],"authenticity_assessment":"Consistent with genuine",
        "authenticity_reasoning":"Stitching and label match","demand":"steady","supply":"plentiful",
        "condition_grade":"Good","size":"M","era":"2019","material":"fleece",
        """#)
    }

    func test_v1ResponseYieldsNoDetail() throws {
        let response = try JSONDecoder().decode(ScanAPIResponse.self, from: v1.data(using: .utf8)!)
        XCTAssertNil(response.confidenceScore)
        XCTAssertEqual(response.confidenceReasons, [])
        XCTAssertNil(ValuationDetail(response: response), "an old server must not produce an empty panel")
    }

    func test_v2ResponseDecodesAndRoundTripsThroughTheStoredBlob() throws {
        let response = try JSONDecoder().decode(ScanAPIResponse.self, from: v2.data(using: .utf8)!)
        XCTAssertEqual(response.confidenceScore, 72)
        XCTAssertEqual(response.expectedPriceUsd, 58)
        XCTAssertEqual(response.improveEstimate, ["Photograph the tag"])

        let detail = try XCTUnwrap(ValuationDetail(response: response))
        XCTAssertEqual(detail.ladder.map(\.label), ["Floor", "Quick sale", "Expected", "Best case"])
        XCTAssertEqual(detail.ladder.map(\.value), [40, 45, 58, 90])
        XCTAssertEqual(detail.facts, ["Good", "M", "2019", "fleece"])

        let data = try XCTUnwrap(detail.encoded())
        XCTAssertEqual(ValuationDetail.decode(data), detail)
        XCTAssertNil(ValuationDetail.decode(nil))
        XCTAssertNil(ValuationDetail.decode(Data("garbage".utf8)))
    }

    func test_ladderSkipsMissingAndZeroPoints() {
        var detail = ValuationDetail()
        detail.expected = 58
        detail.bestCase = 0
        XCTAssertEqual(detail.ladder.map(\.label), ["Expected"])
        XCTAssertFalse(detail.isEmpty)
    }

    func test_scanResultStoresTheBlobAdditively() throws {
        var detail = ValuationDetail()
        detail.confidenceScore = 61
        let result = ScanResult(itemName: "x", brand: "x", category: "x", conditionNotes: "x",
                                valueLow: 1, valueHigh: 2, confidence: "High", soldListingsCount: 0,
                                listingTitle: "", listingDescription: "",
                                valuationDetailData: detail.encoded())
        XCTAssertEqual(result.valuationDetail?.confidenceScore, 61)
        let legacy = ScanResult(itemName: "x", brand: "x", category: "x", conditionNotes: "x",
                                valueLow: 1, valueHigh: 2, confidence: "High", soldListingsCount: 0,
                                listingTitle: "", listingDescription: "")
        XCTAssertNil(legacy.valuationDetailData)
        XCTAssertNil(legacy.valuationDetail)
    }

    func test_paywallTriggerExists() {
        XCTAssertEqual(PaywallTrigger.valuationDetail.rawValue, "valuation_detail")
    }
}

final class GuessFirstPreferenceTests: XCTestCase {
    func test_defaultsOnAndHasAStableKey() {
        XCTAssertTrue(GuessFirst.defaultOn)
        XCTAssertEqual(GuessFirst.key, "snapworth_guess_first")
    }
}


// MARK: - Trending at the thrift (#96)

final class TrendsDecodingTests: XCTestCase {
    private let free = """
    {"days":7,"scans":128,
     "categories":[{"name":"clothing","count":54,"change_pct":18},
                   {"name":"shoes","count":31,"change_pct":-7},
                   {"name":"home","count":22}],
     "brands":[{"name":"Carhartt","count":14,"change_pct":40}],
     "notable_finds":[]}
    """

    private let pro = """
    {"days":7,"scans":128,
     "categories":[{"name":"home","count":22,"change_pct":9,"average_estimate":58}],
     "brands":[{"name":"Le Creuset","count":7}],
     "notable_finds":[{"name":"Le Creuset Dutch Oven 5.5qt","category":"home","low":120,"high":220}]}
    """

    func test_freeShapeDecodesWithoutAveragesOrFinds() throws {
        let trends = try JSONDecoder().decode(Trends.self, from: free.data(using: .utf8)!)
        XCTAssertEqual(trends.scans, 128)
        XCTAssertEqual(trends.categories.map(\.name), ["clothing", "shoes", "home"])
        XCTAssertEqual(trends.categories[0].changePct, 18)
        XCTAssertNil(trends.categories[2].changePct, "a row without both weeks has no direction")
        XCTAssertNil(trends.categories[0].averageEstimate)
        XCTAssertTrue(trends.notableFinds.isEmpty)
        XCTAssertFalse(trends.isEmpty)
    }

    func test_proShapeDecodes() throws {
        let trends = try JSONDecoder().decode(Trends.self, from: pro.data(using: .utf8)!)
        XCTAssertEqual(trends.categories[0].averageEstimate, 58)
        XCTAssertEqual(trends.notableFinds.first?.name, "Le Creuset Dutch Oven 5.5qt")
        XCTAssertEqual(trends.notableFinds.first?.high, 220)
    }

    func test_aQuietWeekIsEmptyRatherThanAnEmptyHeading() throws {
        let quiet = try JSONDecoder().decode(
            Trends.self, from: #"{"days":7,"scans":2,"categories":[],"brands":[]}"#.data(using: .utf8)!)
        XCTAssertTrue(quiet.isEmpty)
        XCTAssertTrue(quiet.notableFinds.isEmpty, "a missing key decodes as empty, not a throw")
    }

    func test_voiceOverReadsARowAsOneSentence() {
        let row = TrendRow(name: "clothing", count: 54, changePct: 18, averageEstimate: 46)
        XCTAssertEqual(TrendingCard.rowLabel(row, isPro: true),
                       "Clothing, 54 scans, average estimate $46, up 18 percent")
        XCTAssertEqual(TrendingCard.rowLabel(row, isPro: false),
                       "Clothing, 54 scans, up 18 percent", "free never hears the average")
        let down = TrendRow(name: "shoes", count: 9, changePct: -7, averageEstimate: nil)
        XCTAssertEqual(TrendingCard.rowLabel(down, isPro: true), "Shoes, 9 scans, down 7 percent")
    }

    func test_paywallTriggerExists() {
        XCTAssertEqual(PaywallTrigger.trends.rawValue, "trends")
    }
}


// MARK: - Add the tag (#88)

final class SharpenedResultTests: XCTestCase {
    private func result() -> ScanResult {
        let r = ScanResult(itemName: "Fleece", brand: "Unknown", category: "clothing",
                           conditionNotes: "Good", valueLow: 20, valueHigh: 90,
                           confidence: "Low", soldListingsCount: 0,
                           listingTitle: "Old title", listingDescription: "Old body",
                           imageData: Data([0xFF, 0xD8]), paidPrice: 4)
        r.statusRaw = "listed"
        r.conditionRaw = "likeNew"
        return r
    }

    private func response() -> ScanAPIResponse {
        ScanAPIResponse(
            itemName: "Patagonia Better Sweater 1/4-Zip, Size M", brand: "Patagonia",
            category: "clothing", conditionNotes: "Good — light pilling",
            estValueLowUsd: 45, estValueHighUsd: 85, confidence: "High",
            listingTitle: "New title", listingDescription: "New body",
            confidenceScore: 88, confidenceSummary: "The label confirms the model.",
            confidenceReasons: ["Care tag read"], improveEstimate: [])
    }

    func test_theModelsFieldsAreReplaced() {
        let r = result()
        r.applySharpened(response())
        XCTAssertEqual(r.itemName, "Patagonia Better Sweater 1/4-Zip, Size M")
        XCTAssertEqual(r.brand, "Patagonia")
        XCTAssertEqual(r.valueLow, 45)
        XCTAssertEqual(r.valueHigh, 85)
        XCTAssertEqual(r.confidence, "High")
        XCTAssertEqual(r.listingTitle, "New title")
        XCTAssertEqual(r.valuationDetail?.confidenceScore, 88)
    }

    func test_whatTheUserOwnsSurvives() {
        let r = result()
        r.applySharpened(response())
        XCTAssertEqual(r.paidPrice, 4, "what they paid is theirs")
        XCTAssertEqual(r.statusRaw, "listed", "the ledger is theirs")
        XCTAssertEqual(r.conditionRaw, "likeNew", "a hand-made correction is never undone")
        XCTAssertNotNil(r.imageData, "the photo they took stays")
    }

    func test_aThinResponseDoesNotWipeTheDetailPanel() {
        let r = result()
        r.applySharpened(response())
        // A later re-read from a server that sends no v2 fields must not
        // blank what the first one produced.
        r.applySharpened(ScanAPIResponse(
            itemName: "x", brand: "x", category: "clothing", conditionNotes: "x",
            estValueLowUsd: 1, estValueHighUsd: 2, confidence: "Low",
            listingTitle: "t", listingDescription: "d"))
        XCTAssertEqual(r.valuationDetail?.confidenceScore, 88)
    }

    func test_analyticsAndTriggerAreBounded() {
        XCTAssertEqual(AnalyticsEvent.tagPhotoAdded(succeeded: true).name, "tag_photo_added")
        XCTAssertEqual(AnalyticsEvent.tagPhotoAdded(succeeded: false).parameters,
                       ["succeeded": "false"])
        XCTAssertEqual(PaywallTrigger.addTag.rawValue, "add_tag")
    }
}

// MARK: - Condition baseline
//
// The estimate is quoted against a condition, and until now the app recovered
// that condition by keyword-matching the model's prose. `prompts.py` tells the
// model to write notes like "Light pilling at cuffs and collar; no stains or
// holes visible" — its own canonical example — and a plain `contains("stain")`
// reads that clean item as damaged. `.used` carries a 0.78 multiplier and
// `priceRange` divides by this baseline, so correcting the wrong chip jumped
// the estimate 28% for a correction that should have moved nothing.
//
// The real fix is that the model already returns `condition_grade` in a closed
// vocabulary. These pin both: that the grade wins, and that the prose fallback
// no longer reads a denial as an assertion.

final class ConditionBaselineTests: XCTestCase {

    // MARK: The bug

    func test_negatedDamageIsNotDamage() {
        // The exact note prompts.py teaches the model to write.
        XCTAssertEqual(
            Condition.inferred(from: "Light pilling at cuffs and collar; no stains or holes visible"),
            .good)
        XCTAssertEqual(Condition.inferred(from: "No stains, no damage"), .good)
        XCTAssertEqual(Condition.inferred(from: "Great condition, no flaws"), .good)
        XCTAssertEqual(Condition.inferred(from: "Clean throughout, without tears"), .good)
        XCTAssertEqual(Condition.inferred(from: "Free of stains or damage"), .good)
    }

    // MARK: The other half of the same bug — substrings

    func test_aDefectTermInsideALongerWordIsNotADefect() {
        // The term list carried a comment warning that "rip" matches "striped"
        // and "wear" matches "menswear". It was a list of the traps someone had
        // noticed, and it was incomplete. Each of these graded `.used`, which
        // carries a 0.78 multiplier — so the estimate came back 22% under.
        XCTAssertEqual(Condition.inferred(from: "Stainless steel case, keeps time"), .good,
                       "stainless → stain")
        XCTAssertEqual(Condition.inferred(from: "Flawless condition throughout"), .good,
                       "flawless → flaw")
        XCTAssertEqual(Condition.inferred(from: "Undamaged, light patina"), .good,
                       "undamaged → damage")
        XCTAssertEqual(Condition.inferred(from: "Heavyweight cotton, holds shape"), .good,
                       "heavyweight → heavy")
        XCTAssertEqual(Condition.inferred(from: "Teardrop earrings, sterling silver"), .good,
                       "teardrop → tear")
        XCTAssertEqual(Condition.inferred(from: "Fairisle knit, classic pattern"), .good,
                       "fairisle → fair")
    }

    func test_unwornIsTheOppositeOfWorn() {
        // The worst of them: the term matched inside the word that negates it.
        XCTAssertEqual(Condition.inferred(from: "Unworn, still in the box"), .new)
    }

    func test_theTrapsTheCommentAlreadyNamedAreNowStructural() {
        // Not in the term list, but they would be safe to add now, which is
        // the point of the change.
        XCTAssertEqual(Condition.inferred(from: "Classic striped oxford"), .good)
        XCTAssertEqual(Condition.inferred(from: "Menswear, size large"), .good)
        XCTAssertEqual(Condition.inferred(from: "Outerwear for winter"), .good)
    }

    func test_inflectionsStillCount() {
        // A word boundary alone would have lost these.
        XCTAssertEqual(Condition.inferred(from: "Stains at the hem"), .used)
        XCTAssertEqual(Condition.inferred(from: "Stained collar"), .used)
        XCTAssertEqual(Condition.inferred(from: "Tearing along the seam"), .used)
        XCTAssertEqual(Condition.inferred(from: "Damaged zip"), .used)
        XCTAssertEqual(Condition.inferred(from: "Several flaws"), .used)
    }

    func test_aRealDefectAfterAFalseOneIsStillFound() {
        // Every occurrence is considered, not just the first — otherwise the
        // "stainless" hit would shadow the real one behind it.
        XCTAssertEqual(
            Condition.inferred(from: "Stainless steel with a stain on the strap"),
            .used)
    }

    func test_realDamageStillReadsAsUsed() {
        XCTAssertEqual(Condition.inferred(from: "Heavy pilling, stains at the cuffs"), .used)
        XCTAssertEqual(Condition.inferred(from: "Visible damage to the zipper"), .used)
        XCTAssertEqual(Condition.inferred(from: "Worn, fading throughout"), .used)
    }

    func test_aNegationStopsAtTheContrast() {
        // "no stains BUT heavy wear" is a worn item. Scoping the "no" to the
        // whole sentence would have read it as a clean one — which is the
        // failure mode opposite to the bug, and just as wrong.
        XCTAssertEqual(Condition.inferred(from: "No stains but heavy wear at the cuffs"), .used)
        // "and" ends the negated span here too, and used not to: the "no"
        // reached across the whole sentence and graded a worn item `.good`,
        // understating the estimate by the 0.78 `.used` multiplier.
        XCTAssertEqual(Condition.inferred(from: "No stains and heavy wear at the cuffs"), .used)
        XCTAssertEqual(Condition.inferred(from: "No holes, though the hem is torn"), .used)
        XCTAssertEqual(Condition.inferred(from: "No damage apart from a faint stain"), .used)
    }

    func test_theStrongerSignalsStillWinAndAreAlsoNegationAware() {
        XCTAssertEqual(Condition.inferred(from: "New with tags"), .new)
        XCTAssertEqual(Condition.inferred(from: "Like new, barely used"), .likeNew)
        XCTAssertEqual(Condition.inferred(from: "Not new, some pilling"), .good)
    }

    func test_unremarkableNotesAreGoodNotUsed() {
        XCTAssertEqual(Condition.inferred(from: "Solid secondhand piece"), .good)
    }

    func test_andDoesNotEndANegatedListOfBareNouns() {
        // The other half, and the reason " and " is not simply added to
        // `clauseBreaks`: doing that fixes "no stains and heavy wear" and
        // breaks these, which is an even trade rather than a fix.
        //
        // A bare noun after "and" is the tail of a list the negation still
        // covers; two or more words are a fresh claim. Measured against the
        // whole suite before it was written — the naive split fails "no rips
        // and tears", and adding " with " also fails "New with tags".
        XCTAssertEqual(Condition.inferred(from: "No rips and tears"), .good)
        XCTAssertEqual(Condition.inferred(from: "No holes and no damage"), .good)
        XCTAssertEqual(Condition.inferred(from: "No damage and no stains"), .good)
        XCTAssertEqual(Condition.inferred(from: "Without holes and light fading"), .good)
        // The negation restated after the break still holds.
        XCTAssertEqual(Condition.inferred(from: "No stains and no heavy wear"), .good)
        // And the case the naive split would have broken, kept as a guard.
        XCTAssertEqual(Condition.inferred(from: "New with tags"), .new)
        XCTAssertEqual(Condition.inferred(from: ""), .good)
    }

    // MARK: The real fix — prefer the grade the model returned

    func test_serverGradeParsesTheClosedVocabulary() {
        XCTAssertEqual(Condition(serverGrade: "new"), .new)
        XCTAssertEqual(Condition(serverGrade: "likeNew"), .likeNew)
        XCTAssertEqual(Condition(serverGrade: "good"), .good)
        XCTAssertEqual(Condition(serverGrade: "used"), .used)
        // Model-generated and stored in old records, so tolerate the variants.
        XCTAssertEqual(Condition(serverGrade: "Good"), .good)
        XCTAssertEqual(Condition(serverGrade: " like new "), .likeNew)
        XCTAssertNil(Condition(serverGrade: "pristine"))
        XCTAssertNil(Condition(serverGrade: ""))
    }

    private func result(notes: String, grade: String?) -> ScanResult {
        var detail = ValuationDetail()
        detail.conditionGrade = grade
        return ScanResult(itemName: "Better Sweater", brand: "Patagonia",
                          category: "clothing", conditionNotes: notes,
                          valueLow: 45, valueHigh: 90, confidence: "High",
                          soldListingsCount: 0,
                          listingTitle: "T", listingDescription: "D",
                          valuationDetailData: grade == nil ? nil : detail.encoded())
    }

    func test_theGradeBeatsTheProse() {
        // Prose that trips the old matcher, and a grade that does not.
        let r = result(notes: "Light pilling; no stains or holes visible", grade: "good")
        XCTAssertEqual(r.baselineCondition, .good)
    }

    func test_theProseIsTheFallbackWhenNoGradeWasSent() {
        let r = result(notes: "Heavy staining at the hem", grade: nil)
        XCTAssertEqual(r.baselineCondition, .used)
    }

    func test_anUntouchedRecordPricesExactlyAsTheAIReturnedIt() {
        // The invariant that makes one baseline property necessary: if the
        // `condition` getter defaults to one baseline and `priceRange` divides
        // by another, an untouched record is silently mispriced.
        for grade in ["new", "likeNew", "good", "used", "pristine"] {
            let r = result(notes: "Some wear", grade: grade)
            let range = r.priceRange(for: r.condition)
            XCTAssertEqual(range.low, 45, "baseline drifted for grade \(grade)")
            XCTAssertEqual(range.high, 90, "baseline drifted for grade \(grade)")
        }
    }

    func test_correctingACleanItemNoLongerInflatesThePrice() {
        // The user-visible bug: the chip defaulted to `.used` for an item the
        // model graded `good`, and putting it back jumped the estimate 28%.
        let r = result(notes: "Light pilling; no stains or holes visible", grade: "good")
        XCTAssertEqual(r.condition, .good, "defaulted to the wrong chip")
        let corrected = r.priceRange(for: .good)
        XCTAssertEqual(corrected.low, 45)
        XCTAssertEqual(corrected.high, 90)
    }

    func test_agenuineDowngradeStillRescales() {
        // Not a claim that corrections never move the price — only that a
        // correction to the baseline does not.
        let r = result(notes: "Light pilling; no stains or holes visible", grade: "good")
        let worse = r.priceRange(for: .used)
        XCTAssertEqual(worse.low, Decimal(45) * Decimal(string: "0.78")!)
    }
}

// MARK: - One item, one value
//
// Four surfaces disagreed about what an item was worth, all for the same
// reason: some readers used the raw AI baseline (`valueLow`/`valueHigh`) and
// others the condition-adjusted value. On anything the user had re-graded, the
// widget's total, the share card's badge and the portfolio total each said
// something the result sheet did not.

final class ValueConsistencyTests: XCTestCase {

    private func item(low: Double = 100, high: Double = 200,
                      condition: Condition? = nil) -> ScanResult {
        let r = ScanResult(itemName: "Better Sweater", brand: "Patagonia",
                           category: "clothing", conditionNotes: "Solid piece",
                           valueLow: low, valueHigh: high, confidence: "High",
                           soldListingsCount: 0,
                           listingTitle: "T", listingDescription: "D")
        if let condition { r.condition = condition }
        return r
    }

    func test_widgetHaulIsConditionAdjusted() {
        // `.used` is 0.78 against a `.good` baseline, so a re-graded item must
        // not contribute its full un-adjusted range to the haul.
        let r = item(condition: .used)
        WidgetDataStore.writeHaul(results: [r])
        guard let suite = UserDefaults(suiteName: WidgetDataStore.appGroupID),
              let raw = suite.data(forKey: WidgetDataStore.haulKey),
              let haul = try? JSONDecoder().decode(WidgetHaulData.self, from: raw) else {
            return XCTFail("haul was not written")
        }
        XCTAssertEqual(haul.totalLow, r.displayValueLow, accuracy: 0.01)
        XCTAssertEqual(haul.totalHigh, r.displayValueHigh, accuracy: 0.01)
        XCTAssertNotEqual(haul.totalLow, r.valueLow, accuracy: 0.01,
                          "still summing the raw AI baseline")
    }

    func test_widgetTotalAgreesWithTheRangeBesideIt() {
        // The medium widget prints `lastItemRange` inches from the total. On a
        // one-item library they are the same item and must not disagree.
        let r = item(condition: .used)
        WidgetDataStore.writeHaul(results: [r])
        guard let suite = UserDefaults(suiteName: WidgetDataStore.appGroupID),
              let raw = suite.data(forKey: WidgetDataStore.haulKey),
              let haul = try? JSONDecoder().decode(WidgetHaulData.self, from: raw) else {
            return XCTFail("haul was not written")
        }
        XCTAssertEqual(haul.lastItemRange, r.formattedRange)
        XCTAssertEqual(haul.totalLow, r.displayValueLow, accuracy: 0.01,
                       "the total and the range printed beside it disagree")
        XCTAssertEqual(haul.totalHigh, r.displayValueHigh, accuracy: 0.01)
    }

    func test_shareBadgeUsesTheSameNumberAsTheHeadlineAboveIt() {
        // $25 paid on an item the model put at $100–200 that the user graded
        // `.used`. Adjusted low is $78, so it is a 3x find; off the raw
        // baseline it would claim 4x — a number the headline above it does not
        // support, on the one artefact that leaves the app.
        //
        // $25 rather than $30 deliberately: at $30 both bases round to 3x and
        // the test would pass against the bug.
        let r = item(condition: .used)
        r.paidPrice = 25
        let badge = ShareCardView(result: r, photo: nil).findBadge(paid: 25)
        XCTAssertEqual(badge, "3x find")
        XCTAssertNotEqual(badge, "4x find", "badge still divides the raw AI baseline")
    }

    func test_theBadgeNeverClaimsAMultipleTheRatioDoesNotReach() {
        // `round` made the threshold for an "Nx find" claim
        // `low/paid >= N - 0.5`, so the very first badge a user can earn was
        // already wrong. The badge sits 6pt under the headline range and 6pt
        // under the "Paid $X" line, so the card printed the two numbers that
        // disprove its own claim — on the artefact that leaves the app.
        // Explicit values: `item()` defaults to $100–$200, and the ratios below
        // are what the test is about.
        let r = item(low: 45, high: 90)
        XCTAssertEqual(r.displayValueLow, 45, "the number the badge divides")

        // 1.5x is not a 2x find, and 1.5x is the first ratio round() inflated.
        XCTAssertNil(ShareCardView(result: r, photo: nil).findBadge(paid: 30),
                     "45/30 is 1.5x — below the 2x the badge would have claimed")
        // 2.5x is not 3x. Swift rounds half away from zero, so this was "3x".
        XCTAssertEqual(ShareCardView(result: r, photo: nil).findBadge(paid: 18),
                       "2x find")
        // And an exact multiple still reads as itself.
        XCTAssertEqual(ShareCardView(result: r, photo: nil).findBadge(paid: 15),
                       "3x find")
        XCTAssertEqual(ShareCardView(result: r, photo: nil).findBadge(paid: 22.5),
                       "2x find")
    }

    func test_theBadgeDividesWhatTheCardPrints() {
        // `snapCurrency` has `maximumFractionDigits = 0`, so a badge divided
        // out of the stored values is a claim about figures that appear
        // nowhere on the card. 50c paid printed "Paid $0" next to a "90x
        // find"; $1.50 printed "Paid $2" next to the 30x taken from 1.50.
        let r = item(low: 45, high: 90)
        XCTAssertEqual(ShareCardView(result: r, photo: nil).findBadge(paid: 0.50),
                       "Free find",
                       "a paid price the card prints as $0 is a free find, not a divisor")
        XCTAssertEqual(ShareCardView(result: r, photo: nil).findBadge(paid: 1.50),
                       "22x find",
                       "1.50 prints as $2, and 45/2 floors to 22 — not the 30 from 1.50")
        // Half-even, because that is NumberFormatter's own default: $22.50
        // prints as $22, so the badge must divide by 22.
        XCTAssertEqual(ShareCardView(result: r, photo: nil).findBadge(paid: 22.50),
                       "2x find")
    }

    func test_aFreeFindIsStillAFreeFind() {
        XCTAssertEqual(ShareCardView(result: item(), photo: nil)
                        .findBadge(paid: 0), "Free find")
    }

    func test_aTagReReadRefreshesThePortfolioValue() throws {
        // #88's re-read replaced the estimate and left the portfolio total and
        // the value history on the old number — the only path that moved a
        // value without recording it.
        let r = item(low: 100, high: 200)
        let before = r.portfolioValueRaw
        r.applySharpened(ScanAPIResponse(
            itemName: "Better Sweater", brand: "Patagonia", category: "clothing",
            conditionNotes: "Solid piece", estValueLowUsd: 300, estValueHighUsd: 400,
            confidence: "High", listingTitle: "T", listingDescription: "D"))
        XCTAssertNotEqual(r.portfolioValueRaw, before, "portfolio kept the old number")
        let after = try XCTUnwrap(r.portfolioValueRaw)
        XCTAssertEqual(after,
                       NSDecimalNumber(decimal: r.priceRange(for: r.condition).likely)
                        .doubleValue, accuracy: 0.01)
        XCTAssertFalse(r.valueHistory.isEmpty, "the move was never recorded")
    }

    func test_theRefreshRunsAfterTheGradeItPricesAgainst() throws {
        // Ordering, not decoration: `baselineCondition` reads `conditionGrade`
        // out of `valuationDetailData`, and `priceRange` divides by that
        // multiplier. Refreshing before the blob is stored prices the new
        // estimate against the previous read's grade.
        let r = item(low: 100, high: 200)
        var detail = ValuationDetail()
        detail.conditionGrade = "used"
        r.valuationDetailData = detail.encoded()
        XCTAssertEqual(r.baselineCondition, .used)

        r.applySharpened(ScanAPIResponse(
            itemName: "Better Sweater", brand: "Patagonia", category: "clothing",
            conditionNotes: "Solid piece", estValueLowUsd: 100, estValueHighUsd: 200,
            confidence: "High", listingTitle: "T", listingDescription: "D",
            conditionGrade: "good"))
        XCTAssertEqual(r.baselineCondition, .good, "the new grade did not take")
        // Untouched record: condition == baseline, so the factor is 1 and the
        // stored value is the midpoint of the AI range exactly.
        XCTAssertEqual(try XCTUnwrap(r.portfolioValueRaw), 150, accuracy: 0.01)
    }
}

// ── The widget blob's Pro flag ───────────────────────────────────────────────
//
// `writeHaul(results:isPro:)` resolved a nil `isPro` as "carry forward
// whatever is already stored". No production caller ever passed the argument,
// `WidgetHaulData.empty.isPro` is false, and a v1 blob has no `isPro` key to
// seed from — so the flag could never become true. Two of the six widgets were
// permanently wrong for subscribers: "Profit this month" rendered its free-tier
// upsell however many flips they sold, and "Scans left" told a paying customer
// they had one free scan left today.

final class WidgetEntitlementTests: XCTestCase {

    private func soldItem(paid: Double, sold: Double, soldDate: Date) -> ScanResult {
        let r = ScanResult(itemName: "Better Sweater", brand: "Patagonia",
                           category: "clothing", conditionNotes: "Solid piece",
                           valueLow: 100, valueHigh: 200, confidence: "High",
                           soldListingsCount: 0,
                           listingTitle: "T", listingDescription: "D")
        r.paidPrice = paid
        r.soldPrice = sold
        r.soldDate = soldDate
        r.status = .sold
        return r
    }

    private func readBack() throws -> WidgetHaulData {
        guard let suite = UserDefaults(suiteName: WidgetDataStore.appGroupID),
              let raw = suite.data(forKey: WidgetDataStore.haulKey) else {
            throw XCTSkip("no App Group container in this test environment")
        }
        return try JSONDecoder().decode(WidgetHaulData.self, from: raw)
    }

    override func tearDown() {
        UserDefaults.standard.removeObject(forKey: "snapworth_is_subscribed")
        super.tearDown()
    }

    func test_aProWriteReachesTheBlob() throws {
        WidgetDataStore.writeHaul(results: [], isPro: true)
        XCTAssertTrue(try readBack().isPro)
    }

    func test_aProWriteAfterAFreeOneIsNotSwallowed() throws {
        // The shape of the original defect: whatever was stored won.
        WidgetDataStore.writeHaul(results: [], isPro: false)
        WidgetDataStore.writeHaul(results: [], isPro: true)
        XCTAssertTrue(try readBack().isPro)
    }

    func test_aLapseClearsTheProFiguresOnTheNextWrite() throws {
        let item = soldItem(paid: 20, sold: 120, soldDate: .now)
        WidgetDataStore.writeHaul(results: [item], isPro: true)
        XCTAssertNotNil(try readBack().monthProfit)

        WidgetDataStore.writeHaul(results: [item], isPro: false)
        let after = try readBack()
        XCTAssertFalse(after.isPro)
        XCTAssertNil(after.monthProfit, "a paid figure outlived the subscription")
        XCTAssertEqual(after.monthFlips, 0)
    }

    func test_proGetsNoFreeScanCountAndFreeDoes() throws {
        WidgetDataStore.writeHaul(results: [], isPro: true)
        XCTAssertNil(try readBack().freeScansRemaining,
                     "a subscriber was handed a free-scan count")
        // The case, not the payload: the streak in the blob depends on whatever
        // the shared `ScanStreak` store holds when this test runs.
        if case .pro = try readBack().scansLeft(at: .now) {} else {
            XCTFail("a subscriber is not in the Pro state")
        }

        WidgetDataStore.writeHaul(results: [], isPro: false)
        XCTAssertNotNil(try readBack().freeScansRemaining)
    }

    func test_theMonthsProfitIsWrittenForAPaidUser() throws {
        // $120 sold on a $20 find, this month: $100 from one flip.
        WidgetDataStore.writeHaul(results: [soldItem(paid: 20, sold: 120, soldDate: .now)],
                                  isPro: true)
        let haul = try readBack()
        XCTAssertEqual(haul.monthProfit ?? 0, 100, accuracy: 0.01)
        XCTAssertEqual(haul.monthFlips, 1)
    }

    func test_anOmittedFlagReadsThePersistedEntitlement() throws {
        // Every production caller omits `isPro`, so this is the path that
        // matters. Setting the raw key also pins its name: if the purchase
        // service renames it, this fails loudly instead of quietly reading
        // false forever, which is how the original defect hid.
        UserDefaults.standard.set(true, forKey: "snapworth_is_subscribed")
        XCTAssertTrue(StoreKitPurchaseService.cachedIsSubscribed,
                      "the cache key this test writes is no longer the one the app reads")

        WidgetDataStore.writeHaul(results: [])
        XCTAssertTrue(try readBack().isPro)
    }

    func test_anOmittedFlagOnAFreeAccountStaysFree() throws {
        UserDefaults.standard.set(false, forKey: "snapworth_is_subscribed")
        WidgetDataStore.writeHaul(results: [])
        XCTAssertFalse(try readBack().isPro)
    }
}

// ── Introductory-offer eligibility ───────────────────────────────────────────
//
// `product.subscription?.introductoryOffer` is the offer *configured on the
// product* in App Store Connect. It says nothing about the customer in front of
// you, and Apple grants one introductory offer per subscription group, once.
//
// `isEligibleForIntroOffer` appeared nowhere in the app. So anyone who had
// already taken the 3-day trial — cancelled, or simply lapsed — reopened the
// app to a headline reading "Try SnapWorth free for 3 days", a card detail
// reading "3-day free trial", and a button reading "Start Free Trial". Apple's
// sheet then charged $39.99 today with no trial.
//
// This is the third axis of one mistake. The paywall first read "an offer
// exists" as "a free trial exists", which is wrong for the two paid payment
// modes. "An offer exists" is not "this person gets it" either.

final class IntroOfferEligibilityTests: XCTestCase {

    private let yearly = Config.yearlyProductID
    private let monthly = Config.monthlyProductID

    func test_anEligibleProductMayAdvertiseItsOffer() {
        XCTAssertTrue(StoreKitPurchaseService.isOfferEligible(
            yearly, in: [yearly: true]))
    }

    func test_anIneligibleProductMayNot() {
        XCTAssertFalse(StoreKitPurchaseService.isOfferEligible(
            yearly, in: [yearly: false]))
    }

    func test_noAnswerMeansNo() {
        // The direction that matters. Defaulting the other way would advertise
        // a free trial on every path where the check did not run — which is
        // precisely the state the app shipped in.
        XCTAssertFalse(StoreKitPurchaseService.isOfferEligible(yearly, in: [:]))
    }

    func test_oneProductsAnswerIsNotAnotherProducts() {
        // Eligibility is per subscription group, so in practice both plans get
        // the same answer — but the lookup must not borrow one for the other,
        // because a second group later would make that silently wrong.
        let onlyMonthly = [monthly: true]
        XCTAssertTrue(StoreKitPurchaseService.isOfferEligible(monthly, in: onlyMonthly))
        XCTAssertFalse(StoreKitPurchaseService.isOfferEligible(yearly, in: onlyMonthly))
    }

    func test_anIneligibleCustomerSeesNoFreeCopyAnywhere() {
        // What the gate buys: with no offer, every copy path degrades to the
        // plain subscribe wording. `PaywallCopy` is already tested against a
        // nil offer; this states the connection between the two.
        let headline = PaywallCopy.headline(isYearly: true, offer: nil)
        XCTAssertFalse(headline.lowercased().contains("free"))
        XCTAssertFalse(PaywallCopy.ctaTitle(isYearly: true, offer: nil)
                        .lowercased().contains("trial"))
    }
}

// ── The analytics opt-out has to reach the SDK ────────────────────────────────
//
// The Settings toggle is `@AppStorage(Analytics.enabledKey)`, which writes
// `UserDefaults` directly and therefore never runs `Analytics.isEnabled`'s
// setter — the one line that calls `backend.setEnabled`. `track` guards on the
// flag, so custom events stopped. What did not stop is the SDK's own automatic
// session and install signals, which carry an identifier and are silenced only
// by `setEnabled`. A user who turned "Share anonymous analytics" off kept
// sending them, and the doc comment on the flag claimed otherwise.

private final class AnalyticsBackendSpy: AnalyticsService {
    var enabledCalls: [Bool] = []
    var tracked: [String] = []
    func track(_ event: AnalyticsEvent) { tracked.append(event.name) }
    func setEnabled(_ enabled: Bool) { enabledCalls.append(enabled) }
}

final class AnalyticsOptOutTests: XCTestCase {

    private var spy = AnalyticsBackendSpy()

    override func setUp() {
        super.setUp()
        spy = AnalyticsBackendSpy()
        Analytics.shared.configure(spy)
    }

    override func tearDown() {
        UserDefaults.standard.removeObject(forKey: Analytics.enabledKey)
        super.tearDown()
    }

    func test_theKeyTheToggleWritesIsTheKeyAnalyticsReads() {
        // Pins the coupling. `@AppStorage(Analytics.enabledKey)` and this flag
        // must be the same key, and a rename that broke it would be silent.
        UserDefaults.standard.set(false, forKey: Analytics.enabledKey)
        XCTAssertFalse(Analytics.shared.isEnabled)
        UserDefaults.standard.set(true, forKey: Analytics.enabledKey)
        XCTAssertTrue(Analytics.shared.isEnabled)
    }

    func test_optingOutThroughTheRawKeyStillReachesTheSDK() {
        // Exactly what the toggle does, followed by what the view now does.
        UserDefaults.standard.set(false, forKey: Analytics.enabledKey)
        Analytics.shared.syncBackendToPersistedFlag()

        XCTAssertEqual(spy.enabledCalls, [false],
                       "the SDK was never told to stop sending session signals")
    }

    func test_optingBackInReachesTheSDKToo() {
        UserDefaults.standard.set(true, forKey: Analytics.enabledKey)
        Analytics.shared.syncBackendToPersistedFlag()
        XCTAssertEqual(spy.enabledCalls, [true])
    }

    func test_theSyncIsIdempotent() {
        // The view calls it on every change; calling it twice must be harmless.
        UserDefaults.standard.set(false, forKey: Analytics.enabledKey)
        Analytics.shared.syncBackendToPersistedFlag()
        Analytics.shared.syncBackendToPersistedFlag()
        XCTAssertEqual(spy.enabledCalls, [false, false])
    }

    func test_customEventsStopWhenOptedOut() {
        // This half always worked — asserted so the two halves are visibly
        // separate things.
        UserDefaults.standard.set(false, forKey: Analytics.enabledKey)
        Analytics.shared.track(.appOpened)
        XCTAssertTrue(spy.tracked.isEmpty)
    }

    func test_defaultIsOptedIn() {
        UserDefaults.standard.removeObject(forKey: Analytics.enabledKey)
        XCTAssertTrue(Analytics.shared.isEnabled)
    }
}
