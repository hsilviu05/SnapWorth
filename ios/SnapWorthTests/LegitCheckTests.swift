import XCTest
@testable import SnapWorth

// Legit check (#90): the closed verdict set, the brands it is offered for,
// the request the app sends, and the rule that nothing it says certifies.

final class LegitCheckContractTests: XCTestCase {

    func test_theServersBodyDecodes() throws {
        let data = try ScanContractTests.contractData("legit-response.json")
        let response = try JSONDecoder().decode(LegitAPIResponse.self, from: data)
        XCTAssertFalse(response.reasons.isEmpty)
        let read = LegitRead(response: response)
        // The fixture's verdict is one this build words, not the fallback.
        XCTAssertEqual(read.verdict.rawValue, response.verdict)
    }

    /// `legit.VERDICTS`, in the server's spelling.
    func test_everyServerVerdictHasACase() {
        for raw in ["consistent", "some_concerns", "likely_replica", "cannot_tell"] {
            XCTAssertEqual(LegitVerdict(serverValue: raw).rawValue, raw)
        }
        XCTAssertEqual(LegitVerdict(serverValue: " Likely_Replica "), .likelyReplica)
    }

    func test_aVerdictThisBuildDoesNotKnowIsCannotTell() {
        for raw in ["authentic", "fake", "", "consistent_with_genuine"] {
            XCTAssertEqual(LegitVerdict(serverValue: raw), .cannotTell, raw)
        }
    }

    func test_unknownPhotoSlotsAreDropped() {
        let response = LegitAPIResponse(verdict: "cannot_tell", reasons: ["Too dark."],
                                        unclearPhotos: ["tag", "sleeve", "hardware"],
                                        promptVersion: "legit-v1")
        XCTAssertEqual(LegitRead(response: response).unclearPhotos, [.tag, .hardware])
    }

    func test_theSlotsAreTheServersFieldNames() {
        XCTAssertEqual(LegitPhotoSlot.allCases.map(\.rawValue),
                       ["logo", "tag", "stitching", "hardware"])
        XCTAssertEqual(LegitPhotoSlot.minimumPhotos, 2)
    }
}

final class LegitCheckWordingTests: XCTestCase {

    /// A headline describes the photos. It never says what the item is.
    func test_noHeadlineCertifies() {
        let forbidden = ["authentic", "genuine", "real", "legit", "fake", "counterfeit",
                         "verified", "certified", "guarantee", "100%"]
        for verdict in LegitVerdict.allCases {
            let title = verdict.title.lowercased()
            for word in forbidden {
                XCTAssertFalse(title.contains(word), "\(verdict.rawValue): \(verdict.title)")
            }
        }
    }

    func test_theDisclaimerSaysItIsNotACertification() {
        XCTAssertTrue(LegitVerdict.disclaimer.localizedCaseInsensitiveContains("not a certification"))
    }

    /// Checks have their own hourly bucket on the server, so "You've hit the
    /// scan limit" would be wrong here.
    func test_aRateLimitIsNotCalledTheScanLimit() {
        let message = LegitCheckFlow.message(for: .rateLimit(retryAfter: 600))
        XCTAssertFalse(message.localizedCaseInsensitiveContains("scan limit"))
        XCTAssertTrue(message.localizedCaseInsensitiveContains("legit check"))
    }
}

final class LegitBrandsTests: XCTestCase {

    func test_commonlyFakedBrandsAreOffered() {
        for brand in ["Nike", "The North Face", "Levi's", "Off-White", "Hermès",
                      "COACH", "Ray-Ban", "Dr. Martens", "Nike Air Jordan", "Arc'teryx"] {
            XCTAssertTrue(LegitBrands.isCommonlyFaked(brand), brand)
        }
    }

    func test_otherBrandsAreNot() {
        for brand in ["Unknown", "unknown", "", "Gap", "Old Navy", "Coachella Merch",
                      "Nikes Bakery", "Banana Republic"] {
            XCTAssertFalse(LegitBrands.isCommonlyFaked(brand), brand)
        }
    }
}

final class LegitReadStorageTests: XCTestCase {

    func test_aReadSurvivesTheRoundTrip() {
        let read = LegitRead(verdict: .someConcerns, reasons: ["Uneven logo stitching."],
                             unclearPhotos: [.hardware],
                             checkedAt: Date(timeIntervalSince1970: 1_790_000_000))
        XCTAssertEqual(LegitRead.decode(read.encoded()), read)
        XCTAssertNil(LegitRead.decode(nil))
        XCTAssertNil(LegitRead.decode(Data("not json".utf8)))
    }
}

final class LegitRequestTests: XCTestCase {

    func test_theBodyNamesEachPhotoBySlotAndSkipsEmptyFields() throws {
        let body = ScanAPIClient.legitMultipart(
            photos: [(.logo, Data("L".utf8)), (.hardware, Data("H".utf8))],
            fields: [("brand", "Nike"), ("item_name", ""), ("category", "shoes")],
            boundary: "B")
        let text = try XCTUnwrap(String(data: body, encoding: .utf8))
        XCTAssertTrue(text.contains("name=\"logo\"; filename=\"logo.jpg\""))
        XCTAssertTrue(text.contains("name=\"hardware\"; filename=\"hardware.jpg\""))
        XCTAssertFalse(text.contains("name=\"tag\""))
        XCTAssertTrue(text.contains("name=\"brand\"\r\n\r\nNike\r\n"))
        XCTAssertFalse(text.contains("name=\"item_name\""), "an empty field is left out")
        XCTAssertTrue(text.hasSuffix("--B--\r\n"))
    }
}

final class LegitAnalyticsAndPaywallTests: XCTestCase {

    func test_theEventCarriesTheClosedVerdictOnly() {
        let event = AnalyticsEvent.legitCheckCompleted(verdict: .likelyReplica)
        XCTAssertEqual(event.name, "legit_check_completed")
        XCTAssertEqual(event.parameters, ["verdict": "likely_replica"])
    }

    func test_theTriggerLeadsWithTheLegitCheckRow() throws {
        XCTAssertEqual(PaywallTrigger.legitCheck.rawValue, "legit_check")
        let pitch = try XCTUnwrap(PaywallCopy.pitch(for: .legitCheck))
        let lead = try XCTUnwrap(PaywallCopy.benefits(pitch: pitch).first)
        XCTAssertTrue(lead.text.localizedCaseInsensitiveContains("legit check"))
    }
}
