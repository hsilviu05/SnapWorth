import AppIntents
import CoreVideo
import ImageIO
import UIKit
import UniformTypeIdentifiers
import VideoToolbox
#if canImport(VisualIntelligence)
import VisualIntelligence
#endif

// The simulator SDK has no VisualIntelligence module, so CI's simulator
// build compiles this part out; a device build has it. `VisualLookupInbox`
// below stays in both, for the drain and the tests.
#if canImport(VisualIntelligence)

/// "Value this with SnapWorth" in iOS 26's visual intelligence results, for a
/// screenshot or the camera (#412). Tapping it opens the app and values that
/// exact picture. Pro: a free user gets the paywall, and the picture is
/// valued if they subscribe from it.
///
/// iOS asks every app with a query for results on every visual intelligence
/// search, whether or not the user ever taps SnapWorth's. So the query does
/// nothing that costs or leaves the phone: it keeps the picture in this app's
/// own temporary folder, and offers the one result. Only a tap sends it to be
/// valued, through the same scan the camera uses.
@available(iOS 26.0, *)
struct VisualLookupEntity: AppEntity {
    static let typeDisplayRepresentation: TypeDisplayRepresentation = "SnapWorth valuation"
    static let defaultQuery = VisualLookupEntityQuery()

    /// Names the picture `VisualLookupInbox` kept for this search.
    let id: String

    var displayRepresentation: DisplayRepresentation {
        DisplayRepresentation(title: "Value this with SnapWorth",
                              subtitle: "See what it's worth to resell",
                              image: .init(systemName: "camera.viewfinder"))
    }
}

/// How iOS turns a tapped result's id back into the entity it hands
/// `ValueVisualLookupIntent`. The picture is looked up only when it is used.
@available(iOS 26.0, *)
struct VisualLookupEntityQuery: EntityQuery {
    func entities(for identifiers: [String]) async throws -> [VisualLookupEntity] {
        identifiers.filter { VisualLookupInbox.stamp(fromID: $0) != nil }
            .map(VisualLookupEntity.init(id:))
    }
}

@available(iOS 26.0, *)
struct VisualLookupQuery: IntentValueQuery {
    func values(for input: SemanticContentDescriptor) async throws -> [VisualLookupEntity] {
        // Before onboarding there is no camera tab to value it on.
        guard UserDefaults.standard.bool(forKey: "hasCompletedOnboarding"),
              let pixelBuffer = input.pixelBuffer,
              let jpeg = VisualLookupInbox.jpeg(from: pixelBuffer),
              let id = try? VisualLookupInbox.stage(jpeg)
        else { return [] }
        return [VisualLookupEntity(id: id)]
    }
}

/// The tap on the result.
///
/// Reaches `ScanView` the way `ValueItemIntent` reaches the camera: the
/// picture is marked ready, the scene drains it on a cold launch or on the
/// edge to `.active`, and when SnapWorth is already frontmost neither of those
/// runs, so it is taken back here and posted directly.
@available(iOS 26.0, *)
struct ValueVisualLookupIntent: OpenIntent {
    static let title: LocalizedStringResource = "Value this with SnapWorth"

    @Parameter(title: "Item")
    var target: VisualLookupEntity

    @MainActor
    func perform() async throws -> some IntentResult {
        guard VisualLookupInbox.markReady(id: target.id) else { return .result() }
        let onboarded = UserDefaults.standard.bool(forKey: "hasCompletedOnboarding")
        if onboarded, UIApplication.shared.applicationState == .active,
           let data = VisualLookupInbox.takeReady(),
           let image = UIImage(data: data) {
            SnapWorthApp.scanSharedImage(image, origin: .visualIntelligence)
        }
        return .result()
    }
}

@available(iOS 26.0, *)
extension VisualLookupInbox {
    /// The picture as a JPEG no longer than the upload's edge. The scan
    /// downsizes again to 1568 anyway; this keeps a full-resolution camera
    /// frame from being written to disk on every search.
    static func jpeg(from pixelBuffer: CVReadOnlyPixelBuffer) -> Data? {
        let cgImage: CGImage? = pixelBuffer.withUnsafeBuffer { buffer in
            var image: CGImage?
            VTCreateCGImageFromCVPixelBuffer(buffer, options: nil, imageOut: &image)
            return image
        }
        guard let cgImage else { return nil }
        let data = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(
            data as CFMutableData, UTType.jpeg.identifier as CFString, 1, nil) else { return nil }
        let options: [CFString: Any] = [
            kCGImageDestinationImageMaxPixelSize: maxEdge,
            kCGImageDestinationLossyCompressionQuality: 0.85,
        ]
        CGImageDestinationAddImage(destination, cgImage, options as CFDictionary)
        guard CGImageDestinationFinalize(destination) else { return nil }
        return data as Data
    }
}

#endif

/// Where a visual intelligence picture waits between the search and the tap.
///
/// In this app's own temporary folder, not the App Group: the query and the
/// intent both run in the app, and nothing else needs to read it. A search
/// keeps its picture as `staged-<ms>.jpg`; the tap renames that one to
/// `ready-<ms>.jpg`, which is what the app values. Only the newest few staged
/// pictures are kept, and none past `maxAge`, so the folder never holds more
/// than the last searches.
///
/// When it was taken is in the name rather than read from the file's
/// timestamp, which is an API Apple requires a declared reason for.
enum VisualLookupInbox {
    static let maxAge: TimeInterval = 30 * 60
    static let maxEdge = 1568
    /// iOS can ask again for the same search; an older result still on screen
    /// must still open when tapped.
    static let keepStaged = 3

    static let staged = "staged-"
    static let ready = "ready-"
    static let suffix = ".jpg"

    static var directory: URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("VisualLookup", isDirectory: true)
    }

    /// Keeps the picture and returns its id.
    static func stage(_ jpeg: Data, now: Date = Date()) throws -> String {
        let dir = directory
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let id = String(Int64(now.timeIntervalSince1970 * 1000))
        try jpeg.write(to: dir.appendingPathComponent(staged + id + suffix),
                       options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        prune(dir, now: now)
        return id
    }

    /// The picture a tapped result names, now the one to value. False when it
    /// is gone: aged out, or pushed out by later searches.
    static func markReady(id: String, now: Date = Date()) -> Bool {
        guard let stamp = stamp(fromID: id), isFresh(stamp, now: now) else { return false }
        let dir = directory
        let from = dir.appendingPathComponent(staged + id + suffix)
        let to = dir.appendingPathComponent(ready + id + suffix)
        removeAll(dir, prefix: ready)
        return (try? FileManager.default.moveItem(at: from, to: to)) != nil
    }

    /// The picture to value, if one was tapped within `maxAge`. Empties the
    /// folder either way, so a tap is acted on once and nothing lingers.
    static func takeReady(now: Date = Date()) -> Data? {
        let dir = directory
        let newest = names(dir, prefix: ready).max { $0.stamp < $1.stamp }
        var data: Data?
        if let newest, isFresh(newest.stamp, now: now) {
            data = try? Data(contentsOf: dir.appendingPathComponent(newest.name))
        }
        if newest != nil {
            removeAll(dir, prefix: ready)
            removeAll(dir, prefix: staged)
        }
        return data
    }

    /// The milliseconds an id stands for. Ids come back from the system, so
    /// anything but digits — a path, say — is refused here.
    static func stamp(fromID id: String) -> TimeInterval? {
        guard !id.isEmpty, id.count <= 16, id.allSatisfy(\.isASCII), id.allSatisfy(\.isNumber),
              let ms = Int64(id) else { return nil }
        return TimeInterval(ms) / 1000
    }

    /// When a file was kept, from its name. Pure, for the tests.
    static func stamp(fromName name: String, prefix: String) -> TimeInterval? {
        guard name.hasPrefix(prefix), name.hasSuffix(suffix) else { return nil }
        return stamp(fromID: String(name.dropFirst(prefix.count).dropLast(suffix.count)))
    }

    /// A minute of clock skew either way is tolerated, as `SharedImageInbox`
    /// does.
    static func isFresh(_ stamp: TimeInterval, now: Date) -> Bool {
        let age = now.timeIntervalSince1970 - stamp
        return age >= -60 && age <= maxAge
    }

    /// Which staged pictures to delete: anything stale, and all but the
    /// newest `keepStaged`. Pure, for the tests.
    static func stagedToPrune(_ stamps: [TimeInterval], now: Date) -> Set<TimeInterval> {
        let fresh = stamps.filter { isFresh($0, now: now) }.sorted(by: >)
        return Set(stamps).subtracting(fresh.prefix(keepStaged))
    }

    private static func prune(_ dir: URL, now: Date) {
        let files = names(dir, prefix: staged)
        let drop = stagedToPrune(files.map(\.stamp), now: now)
        for file in files where drop.contains(file.stamp) {
            try? FileManager.default.removeItem(at: dir.appendingPathComponent(file.name))
        }
        for file in names(dir, prefix: ready) where !isFresh(file.stamp, now: now) {
            try? FileManager.default.removeItem(at: dir.appendingPathComponent(file.name))
        }
    }

    private static func names(_ dir: URL, prefix: String) -> [(stamp: TimeInterval, name: String)] {
        let all = (try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []
        return all.compactMap { name in stamp(fromName: name, prefix: prefix).map { ($0, name) } }
    }

    private static func removeAll(_ dir: URL, prefix: String) {
        for file in names(dir, prefix: prefix) {
            try? FileManager.default.removeItem(at: dir.appendingPathComponent(file.name))
        }
    }
}
