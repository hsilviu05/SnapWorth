import Foundation

/// A photo shared to SnapWorth from another app (#410), waiting in the App
/// Group for the app to value it.
///
/// Compiled into the app and the share extension. A share extension cannot
/// open its app through any supported API, so it saves the photo here and
/// leaves the hand-off to a notification, or to the next time the app comes
/// forward. Whichever brings the user in, the app takes the photo on becoming
/// active.
///
/// When it was shared is in the file name rather than read from the file's
/// timestamp, which is an API Apple requires a declared reason for.
enum SharedImageInbox {
    static let appGroupID = "group.eu.snapworth.app"
    /// Older than this, a share is dropped rather than scanned: opening the
    /// app an hour later to find a scan of something long forgotten — and a
    /// free scan spent on it — is worse than losing the share.
    static let maxAge: TimeInterval = 30 * 60

    private static let prefix = "shared-"
    private static let suffix = ".jpg"

    static var directory: URL? {
        FileManager.default
            .containerURL(forSecurityApplicationGroupIdentifier: appGroupID)?
            .appendingPathComponent("SharedInbox", isDirectory: true)
    }

    /// Saves the photo, replacing anything still waiting: only the latest
    /// share is valued.
    static func put(_ jpeg: Data, now: Date = Date()) throws {
        guard let dir = directory else { throw CocoaError(.fileNoSuchFile) }
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        clear(dir)
        let name = "\(prefix)\(Int(now.timeIntervalSince1970))\(suffix)"
        try jpeg.write(to: dir.appendingPathComponent(name),
                       options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }

    /// The waiting photo, if it was shared within `maxAge`. Empties the inbox
    /// either way, so a share is acted on once.
    static func take(now: Date = Date()) -> Data? {
        guard let dir = directory,
              let names = try? FileManager.default.contentsOfDirectory(atPath: dir.path)
        else { return nil }
        let newest = names
            .compactMap { name in sharedAt(name).map { (sharedAt: $0, name: name) } }
            .max { $0.sharedAt < $1.sharedAt }
        var data: Data?
        if let newest, isFresh(sharedAt: newest.sharedAt, now: now) {
            data = try? Data(contentsOf: dir.appendingPathComponent(newest.name))
        }
        clear(dir)
        return data
    }

    /// When a file in the inbox was shared, from its name. Pure, for the tests.
    static func sharedAt(_ name: String) -> TimeInterval? {
        guard name.hasPrefix(prefix), name.hasSuffix(suffix) else { return nil }
        let stamp = name.dropFirst(prefix.count).dropLast(suffix.count)
        return TimeInterval(stamp)
    }

    /// A minute of clock skew either way is tolerated; a share from further in
    /// the future than that says the clock moved, and is not trusted.
    static func isFresh(sharedAt: TimeInterval, now: Date) -> Bool {
        let age = now.timeIntervalSince1970 - sharedAt
        return age >= -60 && age <= maxAge
    }

    private static func clear(_ dir: URL) {
        let names = (try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []
        for name in names {
            try? FileManager.default.removeItem(at: dir.appendingPathComponent(name))
        }
    }
}
