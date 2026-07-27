import Foundation

enum Config {
    // ── API ──────────────────────────────────────────────────────────────────
    /// Set to your deployed backend URL before submitting to the App Store.
    static let baseURL = URL(string: "https://api.snapworth.eu")!

    /// When true, ScanAPIClient returns canned JSON — no network required.
    /// Flip to false once your backend is deployed and the URL above is set.
    static let mockMode = false

    // ── Transport security ───────────────────────────────────────────────────
    /// Base64 SHA-256 hashes of pinned SubjectPublicKeyInfo blobs for
    /// `api.snapworth.eu`. **Empty means pinning is inert** — see
    /// `CertificatePinning.swift` for the exact `openssl` commands that produce
    /// these, and pin the *intermediate* CA plus a backup, never a bare leaf.
    static let pinnedSPKIHashes: Set<String> = []

    /// When false, a pin mismatch is logged but the request proceeds. Ship one
    /// release in report-only mode before enforcing, so a wrong pin surfaces in
    /// logs instead of bricking the app.
    static let pinningEnforced = false

    // ── Authentication ───────────────────────────────────────────────────────
    /// When true the client attests before calling the API and sends a bearer
    /// token. Must be turned on together with `REQUIRE_APP_ATTEST` on the
    /// server — enabling either side alone breaks the other.
    static let useAttestation = true

    // ── Subscription ─────────────────────────────────────────────────────────
    static let monthlyProductID = "com.snapworth.monthly"
    static let yearlyProductID  = "com.snapworth.yearly"

    // ── App Store ────────────────────────────────────────────────────────────
    /// Update to the App Store product URL once the app is live. Used for the share-card QR code.
    static let appStoreURL = "https://apps.apple.com/app/id6788521307"

    // ── Free tier ────────────────────────────────────────────────────────────
    static let freeScansAllowed = 3

    /// Free tier sees only the most recent N sold flips + current-month totals.
    /// Beyond this, the "My Flips" ledger routes to the paywall.
    static let ledgerFreeSoldCap = 10

    // ── Analytics ──────────────────────────────────────────────────────────────
    /// TelemetryDeck app ID (from the telemetrydeck.com dashboard). Analytics
    /// stays a no-op until this is filled in — nothing is sent while empty.
    static let telemetryDeckAppID = "D4C9C11E-F611-4B92-9646-0DF0B2E0F10C"
}
