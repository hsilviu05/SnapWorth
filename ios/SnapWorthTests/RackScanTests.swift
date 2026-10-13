import XCTest
@testable import SnapWorth

// Rack scan (#89): the wire shape, where the pins land, and what the app
// says and reports about a read.

final class RackScanContractTests: XCTestCase {

    func test_theServersBodyDecodes() throws {
        let data = try ScanContractTests.contractData("rack-response.json")
        let response = try JSONDecoder().decode(RackAPIResponse.self, from: data)
        XCTAssertFalse(response.candidates.isEmpty)
        XCTAssertLessThanOrEqual(response.candidates.count, 3)
        XCTAssertEqual(response.candidates.map(\.rank), Array(1...response.candidates.count))
        for c in response.candidates {
            XCTAssertTrue((0...1).contains(c.box.x) && (0...1).contains(c.box.y))
            XCTAssertGreaterThan(c.box.width, 0)
            XCTAssertGreaterThanOrEqual(c.highUsd, c.lowUsd)
        }
    }

    func test_anEmptyReadDecodes() throws {
        let data = Data(#"{"candidates": [], "prompt_version": "rack-v1"}"#.utf8)
        XCTAssertEqual(try JSONDecoder().decode(RackAPIResponse.self, from: data).candidates, [])
    }
}

final class RackPinGeometryTests: XCTestCase {

    private func candidate(x: Double, y: Double, w: Double, h: Double) -> RackCandidate {
        let json = """
        {"rank": 1, "box": {"x": \(x), "y": \(y), "width": \(w), "height": \(h)},
         "position": "", "name": "Shirt", "brand": "Unknown", "category": "clothing",
         "low_usd": 10, "high_usd": 30, "why": ""}
        """
        return try! JSONDecoder().decode(RackCandidate.self, from: Data(json.utf8))
    }

    func test_aBoxIsPlacedInTheRectangleThePhotoIsDrawnIn() {
        let c = candidate(x: 0.25, y: 0.5, w: 0.5, h: 0.25)
        XCTAssertEqual(c.box.rect(in: CGRect(x: 10, y: 20, width: 200, height: 400)),
                       CGRect(x: 60, y: 220, width: 100, height: 100))
    }

    func test_aPortraitPhotoFitsCentredInALandscapeContainer() {
        let fitted = RackGeometry.fittedRect(imageSize: CGSize(width: 300, height: 400),
                                             in: CGSize(width: 400, height: 200))
        XCTAssertEqual(fitted, CGRect(x: 125, y: 0, width: 150, height: 200))
        XCTAssertEqual(RackGeometry.fittedRect(imageSize: .zero, in: CGSize(width: 1, height: 1)), .zero)
    }

    func test_anUnreadBrandIsNotShown() {
        XCTAssertNil(candidate(x: 0, y: 0, w: 0.1, h: 0.1).shownBrand)
    }

    func test_theRangeIsInDollars() {
        XCTAssertEqual(candidate(x: 0, y: 0, w: 0.1, h: 0.1).formattedRange, "$10–$30")
    }
}

final class RackAnalyticsAndPaywallTests: XCTestCase {

    func test_theEventsCarryCountsAndRanksOnly() {
        let done = AnalyticsEvent.rackScanCompleted(candidates: RackGeometry.countBucket(2))
        XCTAssertEqual(done.name, "rack_scan_completed")
        XCTAssertEqual(done.parameters, ["candidates": "2"])
        let chosen = AnalyticsEvent.rackCandidateChosen(rank: "1")
        XCTAssertEqual(chosen.name, "rack_candidate_chosen")
        XCTAssertEqual(chosen.parameters, ["rank": "1"])
        XCTAssertEqual(RackGeometry.countBucket(9), "3")
        XCTAssertEqual(RackGeometry.countBucket(-1), "0")
    }

    func test_theTriggerLeadsWithTheRackRow() throws {
        XCTAssertEqual(PaywallTrigger.rackScan.rawValue, "rack_scan")
        let pitch = try XCTUnwrap(PaywallCopy.pitch(for: .rackScan))
        let lead = try XCTUnwrap(PaywallCopy.benefits(pitch: pitch).first)
        XCTAssertTrue(lead.text.localizedCaseInsensitiveContains("rack scan"))
    }

    func test_anEmptyReadIsAnnouncedAsAnAnswer() {
        XCTAssertEqual(RackScanFlow.announcement(count: 0), "Nothing stood out in this photo.")
        XCTAssertEqual(RackScanFlow.announcement(count: 2), "2 items worth a closer look.")
    }
}
