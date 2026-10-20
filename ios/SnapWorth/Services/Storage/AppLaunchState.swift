import Foundation

/// Whether this launch is running on a healthy persistent store.
///
/// Exists because of a specific, invisible failure. If SwiftData cannot open
/// the on-disk store, `SnapWorthApp` falls back to an **in-memory** container so
/// the app still runs. That fallback is the right call — it beats crash-looping
/// — but from the user's side it looks like every scan they ever saved has
/// vanished, and anything they do in that session disappears on quit.
///
/// Before this, that happened with no log, no event and no UI signal: a
/// data-loss launch was indistinguishable from a normal one, and the only way
/// we would ever have learned about it is a one-star review.
///
/// Why a type-level flag rather than firing the event at the point of failure:
/// `sharedModelContainer` is a stored property whose initialiser runs *before*
/// `SnapWorthApp.init()`, and `Analytics.track` is a no-op until a backend is
/// configured in that init. Emitting from the catch branch would therefore be
/// silently dropped — the worst possible outcome, since the code would look
/// instrumented while reporting nothing. So the failure is recorded here and
/// emitted once analytics is live.
enum AppLaunchState {

    /// Coarse reason for the fallback, or nil when the store opened normally.
    ///
    /// Read by `SnapWorthApp.init()` after analytics is configured, and
    /// available to any surface that wants to warn the user.
    private(set) static var persistentStoreFallbackReason: String?

    /// True when this launch is running without a persistent store.
    static var isRunningOnFallbackStore: Bool {
        persistentStoreFallbackReason != nil
    }

    /// Records that the on-disk store failed to open.
    ///
    /// Stores a **classification**, never the raw error. A SwiftData error
    /// description routinely embeds the store's filesystem path, which on iOS
    /// contains the container UUID and can contain the device owner's name —
    /// neither belongs in an analytics payload.
    static func recordPersistentStoreFallback(_ error: Error) {
        persistentStoreFallbackReason = classify(error)
    }

    /// Test hook. Not called in shipping code.
    static func reset() {
        persistentStoreFallbackReason = nil
    }

    /// Buckets an arbitrary error into a small, PII-free vocabulary.
    ///
    /// Deliberately coarse: the value of this event is "how many users lost
    /// their history, and roughly why", not a stack trace. A high-cardinality
    /// reason would also make the metric useless to group by.
    static func classify(_ error: Error) -> String {
        let nsError = error as NSError
        let text = "\(error)".lowercased()

        if text.contains("migrat") {
            return "migration_failed"
        }
        if text.contains("incompatible") || text.contains("model") && text.contains("version") {
            return "schema_incompatible"
        }
        if nsError.domain == NSCocoaErrorDomain {
            switch nsError.code {
            case NSFileReadNoSuchFileError, NSFileNoSuchFileError:
                return "store_missing"
            case NSFileReadNoPermissionError, NSFileWriteNoPermissionError:
                return "permission_denied"
            case NSFileWriteOutOfSpaceError:
                return "disk_full"
            case NSFileReadCorruptFileError:
                return "store_corrupt"
            default:
                return "cocoa_\(nsError.code)"
            }
        }
        return "unknown"
    }
}
