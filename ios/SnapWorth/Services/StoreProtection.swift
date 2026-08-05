import Foundation

/// Data-protection class for the SwiftData store.
///
/// Why this exists
/// ---------------
/// The store holds every scan the user has ever taken — item names, valuations,
/// timestamps and the photo of each item — and nothing was setting a protection
/// class explicitly. The platform default applied, which meant the guarantee was
/// whatever the OS happened to do rather than something this app had decided and
/// could be held to.
///
/// Why `.completeUntilFirstUserAuthentication` and not `.complete`
/// --------------------------------------------------------------
/// `.complete` makes files unreadable whenever the device is locked. That is
/// stronger, and wrong here: it would break any work that happens while the
/// screen is off. `.completeUntilFirstUserAuthentication` keeps the store
/// encrypted at rest and unreadable until the user unlocks the device once
/// after boot — which is the threat that actually matters for a lost or stolen
/// phone — while leaving normal operation untouched.
///
/// The widget is unaffected either way: it reads a `UserDefaults` app-group
/// suite (`WidgetDataStore`), never the SwiftData store.
///
/// Applied to the files rather than via the
/// `com.apple.developer.default-data-protection` entitlement deliberately. That
/// entitlement is app-wide and more declarative, but it requires the Data
/// Protection capability on the App ID; adding it to a shipping app risks a
/// provisioning failure at exactly the wrong moment. Setting the attribute is
/// self-contained and needs no portal change.
///
/// Verifying this
/// --------------
/// **Not observable on the Simulator.** It runs on the host's APFS volume,
/// which has no iOS data-protection classes: `setAttributes` reports success
/// and the attribute is then dropped, so reading it back returns nil.
/// `StoreProtectionTests` therefore asserts which files are targeted and that
/// setting succeeds — not the resulting class, which would fail on the
/// Simulator while this code is correct.
///
/// To confirm on real hardware, run on a device and read it back:
/// ```
/// let url = ModelConfiguration(schema: schema, isStoredInMemoryOnly: false).url
/// print(StoreProtection.currentLevel(of: url) as Any)
/// // expected: NSFileProtectionCompleteUntilFirstUserAuthentication
/// ```
enum StoreProtection {

    static let level = FileProtectionType.completeUntilFirstUserAuthentication

    /// SQLite keeps its write-ahead log and shared-memory file alongside the
    /// store. Protecting only the `.store` file would leave recent writes
    /// readable in the `-wal`, which is where the newest scans live.
    static func siblings(of storeURL: URL) -> [URL] {
        [storeURL,
         URL(fileURLWithPath: storeURL.path + "-wal"),
         URL(fileURLWithPath: storeURL.path + "-shm")]
    }

    /// Applies the protection class to the store and its sidecar files.
    ///
    /// Best-effort by design: a failure here must not stop the app launching.
    /// Losing the store is a far worse outcome for the user than a file whose
    /// protection class did not get tightened, and the files that exist are
    /// still covered by the platform default. Returns the URLs it successfully
    /// updated so a caller — or a test — can check.
    @discardableResult
    static func apply(to storeURL: URL,
                      fileManager: FileManager = .default) -> [URL] {
        var updated: [URL] = []
        for url in siblings(of: storeURL) where fileManager.fileExists(atPath: url.path) {
            do {
                try fileManager.setAttributes([.protectionKey: level],
                                              ofItemAtPath: url.path)
                updated.append(url)
            } catch {
                // Deliberately swallowed — see above. Not logged with the path,
                // which contains the container UUID.
                continue
            }
        }
        return updated
    }

    /// Reads back the protection class actually recorded for a file.
    /// Used by tests; there is no other way to assert this was applied.
    static func currentLevel(of url: URL,
                             fileManager: FileManager = .default) -> FileProtectionType? {
        let attributes = try? fileManager.attributesOfItem(atPath: url.path)
        return attributes?[.protectionKey] as? FileProtectionType
    }
}
