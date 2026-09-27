import XCTest
import SwiftUI
import UIKit
@testable import SnapWorth

// MARK: - Rare find: the matcher
//
// Pure, so these say exactly which readings fire the easter egg. The positives
// are the ways OCR mangles this print — the swash capitals above all — and the
// negatives are the shirts it must never mistake for this one.

final class RareFindMatcherTests: XCTestCase {

    private let tagline = "You met me at a very Chinese time in my life."

    func test_theExactTagline_matches() {
        XCTAssertTrue(RareFind.matches([tagline]))
    }

    /// Two lines, as the shirt prints it and as Vision reads the reference photo.
    func test_theTaglineAsPrinted_onTwoLines_matches() {
        XCTAssertTrue(RareFind.matches(["You met me at a very", "Chinese time in my life."]))
    }

    /// The swash C comes back as a lost letter, a wrong one, or a letter of its
    /// own; the swash Y as anything at all.
    func test_ocrErrorsOnTheSwashCapitals_stillMatch() {
        for middle in ["hinese", "Cninese", "cninese", "Ghinese", "(hinese", "Chmese",
                       "Chinesc", "C hinese", "Chine se"] {
            XCTAssertTrue(RareFind.matches(["You met me at a very \(middle) time in my life."]), middle)
        }
        XCTAssertTrue(RareFind.matches(["Vou met me at a very Chinese time in my life."]))
        XCTAssertTrue(RareFind.matches(["Yoil net me at a very Chinese time in my life."]))
    }

    func test_ocrNoiseAroundTheAnchors_stillMatches() {
        let readings = [
            "You  met   me at a  very Chinese   time in my life",          // doubled spaces
            "You met me, at a very Chinese time — in my life!!",            // stray punctuation
            "\"You met me at a very 'Chinese' time in my life.\"",          // quotes
            "You met meat a very Chinese time in my life.",                 // a space lost (a real Vision candidate)
            "You met me at a very Chinese time in my li fe.",               // a space gained
            "YOU MET ME AT A VERY CHINESE TIME IN MY LIFE",
            "you met me at à very chinese time in my life",                 // a stray accent
        ]
        for reading in readings {
            XCTAssertTrue(RareFind.matches([reading]), reading)
        }
    }

    func test_lineBreaksAnywhere_stillMatch() {
        let words = tagline.split(separator: " ").map(String.init)
        for cut in 1..<words.count {
            let lines = [words[..<cut].joined(separator: " "), words[cut...].joined(separator: " ")]
            XCTAssertTrue(RareFind.matches(lines), "\(lines)")
        }
        XCTAssertTrue(RareFind.matches(words), "one word per line")
        XCTAssertTrue(RareFind.matches(["You met me at a\nvery Chinese time\nin my life"]))
        XCTAssertTrue(RareFind.matches(["You met me at a very Chi", "nese time in my life."]),
                      "a break inside the middle word")
    }

    func test_otherTextOnTheShirt_doesNotStopAMatch() {
        XCTAssertTrue(RareFind.matches(["SIZE M", "You met me at a very", "Chinese time in my life.",
                                        "100% COTTON"]))
    }

    // ── Negatives ───────────────────────────────────────────────────────────

    /// The Fight Club line, which shirts are also printed with.
    func test_theFightClubLine_neverMatches() {
        XCTAssertFalse(RareFind.matches(["You met me at a very strange time in my life."]))
        XCTAssertFalse(RareFind.matches(["You met me at a very", "strange time in my life."]))
        XCTAssertFalse(RareFind.matches(["YOU MET ME AT A VERY STRANGE TIME IN MY LIFE"]))
        XCTAssertFalse(RareFind.readsAsChinese("strange"))
    }

    func test_genericPrints_neverMatch() {
        for slogan in ["Best time of my life", "The best time of my life", "Having the time of my life",
                       "Time in my life", "Good times", ""] {
            XCTAssertFalse(RareFind.matches([slogan]), slogan)
        }
        XCTAssertFalse(RareFind.matches([]))
    }

    func test_anchorsOutOfOrder_doNotMatch() {
        XCTAssertFalse(RareFind.matches(["Chinese time in my life.", "You met me at a very"]))
        XCTAssertFalse(RareFind.matches(["time in my life me at a very Chinese"]))
    }

    func test_aMissingAnchor_doesNotMatch() {
        XCTAssertFalse(RareFind.matches(["You met me at a very Chinese time"]))
        XCTAssertFalse(RareFind.matches(["Chinese time in my life."]))
        XCTAssertFalse(RareFind.matches(["at a very Chinese time in my life"]))
        XCTAssertFalse(RareFind.matches(["You met me at a very Chinese time in my"]))
    }

    /// The slot takes one word that reads as "chinese", and nothing else.
    func test_theMiddleSlot_mustReadAsChinese() {
        XCTAssertFalse(RareFind.matches(["You met me at a very time in my life."]))
        XCTAssertFalse(RareFind.matches(["You met me at a very happy time in my life."]))
        XCTAssertFalse(RareFind.matches(["You met me at a very busy time in my life."]))
    }

    func test_editDistance() {
        XCTAssertEqual(RareFind.editDistance("chinese", "chinese"), 0)
        XCTAssertEqual(RareFind.editDistance("cninese", "chinese"), 1)
        XCTAssertEqual(RareFind.editDistance("hinese", "chinese"), 1)
        XCTAssertEqual(RareFind.editDistance("chmese", "chinese"), 2)
        XCTAssertEqual(RareFind.editDistance("", "abc"), 3)
        XCTAssertGreaterThan(RareFind.editDistance("strange", "chinese"), 2)
    }
}

// MARK: - Rare find: detection on real pixels
//
// The whole path — downscale, orientation, Vision, matcher — on images, so a
// change to any link shows up here and not on a phone.

final class RareFindDetectionTests: XCTestCase {

    func test_theReferencePhoto_isARareFind() async throws {
        let matched = await RareFind.detect(in: try Self.referencePhoto())
        XCTAssertTrue(matched)
    }

    /// Detection takes the photo out of the box it is handed, so nothing in it
    /// holds the full-resolution capture once the reading copy exists.
    func test_detection_takesThePhotoItIsHanded() async throws {
        let photo = RareFindPhoto(try Self.referencePhoto())
        let matched = await RareFind.detect(photo)
        XCTAssertTrue(matched)
        XCTAssertNil(photo.take(), "detection left the capture in the box")
    }

    /// Cancellation is how a finished scan calls detection off. Cancelled
    /// before it starts, it answers no without reading anything — on the one
    /// photo that would otherwise match.
    func test_aDetectionWhoseScanIsOver_answersNo() async throws {
        let photo = RareFindPhoto(try Self.referencePhoto())
        let run = Task { () -> Bool in
            withUnsafeCurrentTask { $0?.cancel() }
            return await RareFind.detect(photo)
        }
        let matched = await run.value
        XCTAssertFalse(matched)
        XCTAssertNotNil(photo.take(), "Vision's input was prepared for a scan that was over")
    }

    /// The same photo as the camera delivers one: pixels on their side and the
    /// turn in `imageOrientation`. It is under the 1568px edge, so it is not
    /// redrawn upright and the orientation has to reach Vision.
    func test_theReferencePhoto_heldTheWayACameraDeliversIt_isARareFind() async throws {
        // Portrait, because the fixture is square: on a square image the
        // displayed size is the same whichever way the tag turns it, and the
        // check below could not fail.
        let upright = try XCTUnwrap(Self.portrait(try Self.referencePhoto()))
        XCTAssertNotEqual(upright.size.width, upright.size.height)
        let sideways = try XCTUnwrap(Self.storedSideways(upright))
        XCTAssertEqual(sideways.cgImage?.width, upright.cgImage?.height, "the pixels are on their side")
        XCTAssertEqual(sideways.size, upright.size, "the orientation tag stands them back up")
        // The size says a quarter turn, not which way: `.left` would pass it
        // upside down. What the tag displays has to be the picture itself.
        let shown = try XCTUnwrap(Self.displayedBytes(sideways))
        let original = try XCTUnwrap(Self.displayedBytes(upright))
        // The right tag redraws the picture exactly (0 here). The nearest wrong
        // one is `.leftMirrored`, a left-right flip of a near-symmetric shirt:
        // only the print differs, and it measured 1.17.
        let difference = Self.meanDifference(shown, original)
        XCTAssertLessThan(difference, 0.25, "the fixture displays turned or mirrored (mean difference \(difference))")
        let matched = await RareFind.detect(in: sideways)
        XCTAssertTrue(matched)
    }

    func test_anOrdinaryListingPhoto_isNot() async throws {
        let folder = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "ListingPhotos", withExtension: nil))
        let photo = try XCTUnwrap(UIImage(contentsOfFile: folder.appendingPathComponent("03-square.jpg").path))
        let matched = await RareFind.detect(in: photo)
        XCTAssertFalse(matched)
    }

    func test_theTaglineSetInType_isARareFind_andTheFightClubLineIsNot() async {
        let shirt = await RareFind.detect(in: Self.printed(["You met me at a very", "Chinese time in my life."]))
        XCTAssertTrue(shirt)
        let fightClub = await RareFind.detect(in: Self.printed(["You met me at a very", "strange time in my life."]))
        XCTAssertFalse(fightClub)
    }

    /// No bitmap at all: a "no", not a throw and not a crash.
    func test_anImageWithNothingInIt_isNot() async {
        let matched = await RareFind.detect(in: UIImage())
        XCTAssertFalse(matched)
    }

    // ── Fixtures ────────────────────────────────────────────────────────────

    static func referencePhoto() throws -> UIImage {
        let url = try XCTUnwrap(Bundle(for: RareFindDetectionTests.self)
            .url(forResource: "rare-find-reference-shirt", withExtension: "png"),
            "rare-find-reference-shirt.png missing from the test bundle")
        return try XCTUnwrap(UIImage(contentsOfFile: url.path))
    }

    /// `image` at the top of a canvas a quarter taller, on white — a product
    /// shot's ground — so its size says which way up it is. Still under the
    /// 1568px edge.
    private static func portrait(_ image: UIImage) -> UIImage? {
        guard let cg = image.cgImage else { return nil }
        let size = CGSize(width: cg.width, height: cg.height * 5 / 4)
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1
        format.opaque = true
        return UIGraphicsImageRenderer(size: size, format: format).image { ctx in
            UIColor.white.setFill()
            ctx.fill(CGRect(origin: .zero, size: size))
            UIImage(cgImage: cg).draw(in: CGRect(x: 0, y: 0, width: cg.width, height: cg.height))
        }
    }

    /// `image`'s pixels turned a quarter anticlockwise and tagged `.right`, which
    /// is how a portrait capture from the back camera is stored.
    private static func storedSideways(_ image: UIImage) -> UIImage? {
        guard let cg = image.cgImage,
              let ctx = CGContext(data: nil, width: cg.height, height: cg.width, bitsPerComponent: 8,
                                  bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
        else { return nil }
        // Core Graphics' origin is bottom-left: rotating by +90° about it and
        // shifting right by the new width turns the content anticlockwise.
        ctx.translateBy(x: CGFloat(cg.height), y: 0)
        ctx.rotate(by: .pi / 2)
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: cg.width, height: cg.height))
        guard let rotated = ctx.makeImage() else { return nil }
        return UIImage(cgImage: rotated, scale: 1, orientation: .right)
    }

    /// `image` as it displays — orientation applied — as RGBX bytes, one pixel
    /// per point.
    private static func displayedBytes(_ image: UIImage) -> [UInt8]? {
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1
        format.opaque = true
        let redrawn = UIGraphicsImageRenderer(size: image.size, format: format).image { _ in
            image.draw(at: .zero)
        }
        guard let cg = redrawn.cgImage else { return nil }
        var bytes = [UInt8](repeating: 0, count: cg.width * cg.height * 4)
        let drawn = bytes.withUnsafeMutableBytes { buffer -> Bool in
            guard let ctx = CGContext(data: buffer.baseAddress, width: cg.width, height: cg.height,
                                      bitsPerComponent: 8, bytesPerRow: cg.width * 4,
                                      space: CGColorSpaceCreateDeviceRGB(),
                                      bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
            else { return false }
            ctx.draw(cg, in: CGRect(x: 0, y: 0, width: cg.width, height: cg.height))
            return true
        }
        return drawn ? bytes : nil
    }

    /// Mean absolute difference per byte; `.infinity` when the sizes differ.
    private static func meanDifference(_ a: [UInt8], _ b: [UInt8]) -> Double {
        guard a.count == b.count, !a.isEmpty else { return .infinity }
        var total = 0
        for i in a.indices { total += abs(Int(a[i]) - Int(b[i])) }
        return Double(total) / Double(a.count)
    }

    /// White serif lines on the shirt's red, the way the print looks.
    private static func printed(_ lines: [String]) -> UIImage {
        let size = CGSize(width: 1200, height: 600)
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1
        format.opaque = true
        return UIGraphicsImageRenderer(size: size, format: format).image { ctx in
            UIColor(red: 0.72, green: 0.2, blue: 0.23, alpha: 1).setFill()
            ctx.fill(CGRect(origin: .zero, size: size))
            let attributes: [NSAttributedString.Key: Any] = [
                .font: UIFont(name: "Georgia", size: 64) ?? UIFont.systemFont(ofSize: 64),
                .foregroundColor: UIColor.white,
            ]
            var y: CGFloat = 200
            for line in lines {
                let text = NSAttributedString(string: line, attributes: attributes)
                text.draw(at: CGPoint(x: (size.width - text.size().width) / 2, y: y))
                y += 90
            }
        }
    }
}

// MARK: - Rare find: the draw

/// A seedable generator, so a draw can be reproduced. SplitMix64.
struct RareFindSeededGenerator: RandomNumberGenerator {
    var state: UInt64
    mutating func next() -> UInt64 {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }
}

final class RareFindRevealTests: XCTestCase {

    private func draw(seed: UInt64) -> RareFindReveal {
        var generator = RareFindSeededGenerator(state: seed)
        return RareFindReveal.draw(using: &generator)
    }

    func test_theSameSeed_drawsTheSameReveal() {
        XCTAssertEqual(draw(seed: 42), draw(seed: 42))
        XCTAssertEqual(draw(seed: 7), draw(seed: 7))
    }

    func test_differentSeeds_drawDifferentReveals() {
        let draws = (0..<20).map { draw(seed: $0) }
        XCTAssertGreaterThan(Set(draws.map(\.verdict)).count, 1)
        XCTAssertGreaterThan(Set(draws.map(\.value.countTo)).count, 1)
        XCTAssertGreaterThan(Set(draws.map { $0.statusLines.joined() }).count, 1)
    }

    /// Whatever is drawn escalates — two openers, two risers, a peak — and is
    /// all from the curated lists.
    func test_everyDraw_escalates_andComesFromTheLists() {
        for seed in 0..<200 {
            let reveal = draw(seed: UInt64(seed))
            XCTAssertEqual(reveal.statusLines.count, 5)
            XCTAssertEqual(Set(reveal.statusLines).count, 5)
            XCTAssertTrue(reveal.statusLines[0..<2].allSatisfy(RareFindCopy.openingLines.contains))
            XCTAssertTrue(reveal.statusLines[2..<4].allSatisfy(RareFindCopy.risingLines.contains))
            XCTAssertTrue(RareFindCopy.peakLines.contains(reveal.statusLines[4]))
            XCTAssertTrue(RareFindCopy.tiers.contains(reveal.tier))
            XCTAssertTrue(RareFindCopy.values.contains(reveal.value))
            XCTAssertEqual(reveal.stats.count, 3)
            XCTAssertEqual(Set(reveal.stats.map(\.label)).count, 3)
            XCTAssertTrue(reveal.stats.allSatisfy(RareFindCopy.stats.contains))
            XCTAssertTrue(RareFindCopy.verdicts.contains(reveal.verdict))
        }
    }

    func test_theCuratedLists_areTheSizesAskedFor_withNoRepeats() {
        let lines = RareFindCopy.openingLines + RareFindCopy.risingLines + RareFindCopy.peakLines
        XCTAssertEqual(lines.count, 15)
        XCTAssertEqual(Set(lines).count, 15)
        XCTAssertEqual(RareFindCopy.values.count, 15)
        XCTAssertEqual(Set(RareFindCopy.values.map(\.punchline)).count, 15)
        XCTAssertEqual(RareFindCopy.verdicts.count, 10)
        XCTAssertEqual(Set(RareFindCopy.verdicts).count, 10)
        XCTAssertGreaterThanOrEqual(RareFindCopy.tiers.count, 3)
        XCTAssertGreaterThanOrEqual(RareFindCopy.stats.count, 4)
    }

    func test_theOwnersFiveLines_areInThePool() {
        let pool = RareFindCopy.openingLines + RareFindCopy.risingLines + RareFindCopy.peakLines
        for line in ["Authenticating stitching…", "Cross-referencing auction records…",
                     "Consulting three fashion historians…", "Dragon detected. Recalibrating…",
                     "WARNING: value exceeds scale"] {
            XCTAssertTrue(pool.contains(line), line)
        }
    }

    /// VoiceOver hears "easter egg" before any number, and the summary stops
    /// at the lead-in: the tagline is the next element, in English.
    func test_theSpokenSummary_saysEasterEggFirst_andLeavesTheTaglineToItsOwnElement() {
        let reveal = draw(seed: 3)
        XCTAssertTrue(reveal.spokenSummary.hasPrefix(RareFindCopy.spokenLabel))
        XCTAssertTrue(reveal.spokenSummary.contains(reveal.verdict))
        XCTAssertTrue(reveal.spokenSummary.hasSuffix(RareFindCopy.spokenTaglineLead))
        XCTAssertFalse(reveal.spokenSummary.contains(RareFind.tagline))
    }

    /// A landing that ends on a full stop ("Priceless.") is set inside a
    /// sentence that brings its own, without doubling it.
    func test_spokenValues_neverDoubleTheirFullStops() {
        for value in RareFindCopy.values {
            let spoken = RareFindCopy.spokenValue(value)
            XCTAssertFalse(spoken.contains(".,") || spoken.contains(".."), spoken)
        }
    }

    /// "11/10" is read aloud as a date.
    func test_scores_areSpokenInWords() {
        let scores = RareFindCopy.stats.filter { $0.value.contains("/") }
        XCTAssertFalse(scores.isEmpty)
        for score in scores {
            XCTAssertFalse(score.spoken.contains("/"), score.spoken)
        }
    }

    func test_theTagline_isVerbatim_andNoBrandIsInvented() {
        XCTAssertEqual(RareFind.tagline, "You met me at a very Chinese time in my life.")
        XCTAssertNil(RareFind.brandName)
    }

    func test_money_isDollars_asEverywhereElse() {
        XCTAssertEqual(RareFind.money(48_000), "$48,000")
    }

    func test_theAppraisal_runsAboutFiveSeconds() {
        XCTAssertEqual(RareFind.appraisalDuration, .seconds(5))
    }
}

// MARK: - Rare find: the race against the estimate
//
// `startScan` itself needs the network. The rule it relies on is here: a match
// counts only while the window is open, and a detector that answers late, or
// says no, leaves the scan exactly as it was.

@MainActor
final class RareFindWatchTests: XCTestCase {

    /// Lets a scripted detector answer when the test says so.
    private actor Gate {
        private var isOpen = false
        private var waiting: [CheckedContinuation<Void, Never>] = []

        func wait() async {
            guard !isOpen else { return }
            await withCheckedContinuation { waiting.append($0) }
        }

        func open() {
            isOpen = true
            waiting.forEach { $0.resume() }
            waiting = []
        }
    }

    private static func seeded() -> RareFindReveal {
        var generator = RareFindSeededGenerator(state: 1)
        return RareFindReveal.draw(using: &generator)
    }

    func test_aMatchInsideTheWindow_isKept_andStartsTheAppraisal() async throws {
        var started: [RareFindMatch] = []
        let watch = RareFindWatch(hold: .seconds(5),
                                  detect: { true }, draw: Self.seeded,
                                  onMatch: { started.append($0) })
        await watch.detection?.value
        let match = try XCTUnwrap(watch.close())

        XCTAssertEqual(started, [match])
        XCTAssertEqual(match.reveal, Self.seeded())
        XCTAssertTrue(match.playsAppraisal)
        XCTAssertGreaterThan(match.appraisalEnds, ContinuousClock.now + .seconds(4),
                             "the result waits for the appraisal to play out")
    }

    func test_aMatchAfterTheEstimate_isDroppedSilently() async {
        let gate = Gate()
        var started = 0
        let watch = RareFindWatch(hold: .seconds(5),
                                  detect: { await gate.wait(); return true },
                                  onMatch: { _ in started += 1 })

        XCTAssertNil(watch.close(), "the estimate came back first")
        await gate.open()
        await watch.detection?.value

        XCTAssertEqual(started, 0, "no appraisal once the estimate is in")
        XCTAssertNil(watch.match)
        XCTAssertNil(watch.close())
    }

    func test_noMatch_leavesTheScanAlone() async {
        var started = 0
        let watch = RareFindWatch(hold: .seconds(5),
                                  detect: { false }, onMatch: { _ in started += 1 })
        await watch.detection?.value
        XCTAssertNil(watch.close())
        XCTAssertEqual(started, 0)
    }

    /// Reduce Motion: a match still reveals, but plays no appraisal and holds
    /// the result for no time at all.
    func test_withNoHold_theMatchPlaysNoAppraisal_andDoesNotWait() async throws {
        let watch = RareFindWatch(hold: .zero,
                                  detect: { true }, onMatch: { _ in })
        await watch.detection?.value
        let match = try XCTUnwrap(watch.close())
        XCTAssertFalse(match.playsAppraisal)
        XCTAssertLessThanOrEqual(match.appraisalEnds, ContinuousClock.now)
    }

    /// Closing is what tells the detector to stop: `RareFind.detect` checks
    /// for it before Vision, and lets go of the photo.
    func test_closingTheWindow_cancelsTheDetector() async {
        let gate = Gate()
        let sawCancellation = Flag()
        let watch = RareFindWatch(hold: .seconds(5),
                                  detect: {
                                      await gate.wait()
                                      await sawCancellation.set(Task.isCancelled)
                                      return false
                                  },
                                  onMatch: { _ in })
        watch.close()
        await gate.open()
        await watch.detection?.value
        let cancelled = await sawCancellation.value
        XCTAssertTrue(cancelled)
    }

    /// A screen cleared mid-scan: a match that had already landed is
    /// forgotten, so the scan finishes as one that never matched — its own
    /// haptic, no hold, no reveal.
    func test_abandoningTheWindow_forgetsAMatchThatAlreadyLanded() async throws {
        let watch = RareFindWatch(hold: .seconds(5), detect: { true }, onMatch: { _ in })
        await watch.detection?.value
        XCTAssertNotNil(watch.match)

        watch.abandon()
        XCTAssertNil(watch.match)
        XCTAssertNil(watch.close(), "the scan would hold its result for an appraisal nobody sees")
    }

    /// …and one still on its way never starts an appraisal over the next scan.
    func test_aMatchAfterTheWindowIsAbandoned_startsNothing() async {
        let gate = Gate()
        var started = 0
        let watch = RareFindWatch(hold: .seconds(5),
                                  detect: { await gate.wait(); return true },
                                  onMatch: { _ in started += 1 })
        watch.abandon()
        await gate.open()
        await watch.detection?.value

        XCTAssertEqual(started, 0)
        XCTAssertNil(watch.close())
    }

    private actor Flag {
        private(set) var value = false
        func set(_ newValue: Bool) { value = newValue }
    }
}

// MARK: - Rare find: what the joke leaves alone
//
// Wiring that only a whole scan exercises, and a scan needs the network — so
// these read the source, as ProductionHardeningTests does for the funnel.

final class RareFindWiringTests: XCTestCase {

    private func source(_ path: String) throws -> String {
        try String(contentsOf: URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent(path), encoding: .utf8)
    }

    /// The full result after a reveal is the full result: `coverPrice` also
    /// offers "Sharpen this estimate", so the reveal lifts the guess cover
    /// with a parameter of its own rather than by turning `coverPrice` off.
    func test_theFullResultAfterAReveal_stillOffersToSharpenTheEstimate() throws {
        let scanView = try source("SnapWorth/Views/ScanView.swift")
        XCTAssertTrue(scanView.contains("coverPrice: true,"))
        XCTAssertTrue(scanView.contains("priceAlreadyShown: vm.rareFindReveal != nil"))
        XCTAssertFalse(scanView.contains("coverPrice: vm.rareFindReveal"))
    }
}

// MARK: - Rare find: the card to VoiceOver

/// Reads the card's real accessibility tree rather than the strings that go
/// into it, because the fix it guards lives in how SwiftUI builds that tree:
/// a speech language set on a run inside a label is dropped, and so is one
/// inside a `.combine`d group. Only a separate element keeps it.
@MainActor
final class RareFindCardAccessibilityTests: XCTestCase {

    func test_theCard_isItsSummary_thenTheTaglineInAnEnglishVoice() throws {
        var generator = RareFindSeededGenerator(state: 5)
        let reveal = RareFindReveal.draw(using: &generator)
        let elements = try Self.accessibilityElements(
            of: RareFindCard(reveal: reveal).frame(width: 360))

        XCTAssertEqual(elements.map(\.accessibilityLabel), [reveal.spokenSummary, RareFind.tagline])
        guard elements.count == 2 else { return }
        XCTAssertNil(Self.speechLanguage(of: elements[0]), "the summary is spoken in the app's language")
        XCTAssertEqual(Self.speechLanguage(of: elements[1])?.hasPrefix("en"), true,
                       "the tagline is spoken in English whatever the app's language")
    }

    // ── Plumbing ────────────────────────────────────────────────────────────

    private typealias SetAutomation = @convention(c) (Int32) -> Void
    private typealias GetAutomation = @convention(c) () -> Int32

    /// The accessibility elements `view` produces, in reading order.
    ///
    /// SwiftUI builds its tree only while something is reading it. Automation
    /// mode — what UI tests and accessibility-snapshot tools switch on — is
    /// that something here. Its switch is in libAccessibility, which is
    /// private, so the test skips rather than fails where it is missing. Test
    /// bundle only: none of this is in the app.
    private static func accessibilityElements<V: View>(of view: V) throws -> [NSObject] {
        guard let library = dlopen("/usr/lib/libAccessibility.dylib", RTLD_NOW),
              let setSymbol = dlsym(library, "_AXSSetAutomationEnabled"),
              let getSymbol = dlsym(library, "_AXSAutomationEnabled")
        else { throw XCTSkip("the accessibility automation switch is not available") }
        let setAutomation = unsafeBitCast(setSymbol, to: SetAutomation.self)
        let wasOn = unsafeBitCast(getSymbol, to: GetAutomation.self)()
        setAutomation(1)
        defer { setAutomation(wasOn) }

        let host = UIHostingController(rootView: view)
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 1000))
        window.rootViewController = host
        window.makeKeyAndVisible()
        defer { window.isHidden = true }
        host.view.layoutIfNeeded()
        RunLoop.main.run(until: Date().addingTimeInterval(0.3))

        var found: [NSObject] = []
        var seen = Set<ObjectIdentifier>()
        func walk(_ object: NSObject) {
            guard seen.insert(ObjectIdentifier(object)).inserted else { return }
            if object.isAccessibilityElement {
                found.append(object)
                return
            }
            if let children = object.accessibilityElements {
                children.compactMap { $0 as? NSObject }.forEach(walk)
            } else {
                let count = object.accessibilityElementCount()
                if count != NSNotFound, count > 0 {
                    (0..<count).compactMap { object.accessibilityElement(at: $0) as? NSObject }.forEach(walk)
                }
            }
            (object as? UIView)?.subviews.forEach(walk)
        }
        walk(host.view)
        return found
    }

    /// What VoiceOver speaks the element in: its language, or the one on its
    /// label's text. Nil means the app's own.
    private static func speechLanguage(of element: NSObject) -> String? {
        if let language = element.accessibilityLanguage { return language }
        guard let label = element.accessibilityAttributedLabel, label.length > 0 else { return nil }
        return label.attribute(.accessibilitySpeechLanguage, at: 0, effectiveRange: nil) as? String
    }
}
