import Foundation

enum Config {
    // ── API ──────────────────────────────────────────────────────────────────
    /// Set to your deployed backend URL before submitting to the App Store.
    static let baseURL = URL(string: "https://api.snapworth.eu")!

    /// When true, ScanAPIClient returns canned JSON — no network required.
    /// Flip to false once your backend is deployed and the URL above is set.
    static let mockMode = false

    /// The Simulator cannot attest (App Attest does not exist there), so
    /// against production every scan fails and nothing downstream of a scan —
    /// the result sheet, Guess the price, Why this price, the streak — can be
    /// exercised. This launch argument turns the canned responses on for one
    /// run without touching `mockMode`, which stays false and is guarded by a
    /// test. Xcode → Edit Scheme… → Run → Arguments Passed On Launch.
    /// An App Store build can never receive a launch argument, so it cannot
    /// ship on.
    static let mockScansLaunchArgument = "-mock-scans"

    /// Whether scans (and Snap → Sell listings) come from canned data.
    static var mockScans: Bool {
        mockMode || CommandLine.arguments.contains(mockScansLaunchArgument)
    }

    // ── Transport security ───────────────────────────────────────────────────
    /// Base64 SHA-256 hashes of pinned SubjectPublicKeyInfo blobs for
    /// `api.snapworth.eu`, extracted 2026-07-28 from the live chain.
    ///
    /// **What is pinned, and why these four.** The served chain is
    /// `leaf → YR2 → Root YR → ISRG Root X1`. The leaf is deliberately *not*
    /// here: Let's Encrypt rotates it every 90 days and pinning it would brick
    /// the app on every renewal. Pinning the issuing intermediate plus the ISRG
    /// roots survives all leaf and intermediate rotation while still preventing
    /// an unrelated CA from impersonating the host.
    ///
    /// `matchesPin` accepts a match on *any* certificate in the evaluated chain,
    /// so these are alternatives, not a required set — which is what makes the
    /// redundancy load-bearing.
    ///
    /// Regenerate with:
    /// ```
    /// openssl s_client -connect api.snapworth.eu:443 -showcerts </dev/null 2>/dev/null \
    ///   | openssl x509 -noout -pubkey | openssl pkey -pubin -outform der \
    ///   | openssl dgst -sha256 -binary | base64
    /// ```
    static let pinnedSPKIHashes: Set<String> = [
        "nWN7PSep5XDQdge5zK24CnCRXHr3KvzhKEGxsdqCX9E=",  // LE YR2 intermediate  (exp 2028-09-02)
        "fk6IOKit1ild5647BH06ujSIq5XbCgqlbYl6ANhhi88=",  // ISRG Root YR         (exp 2032-09-02)
        "C5+lpZ7tcVwmwQIMcRtPbsQtWLABXhQzejna0wHFr8M=",  // ISRG Root X1         (exp 2035-06-04)
        "diGVwiVYbubAI3RW4hB9xU8e/CH2GnkuvVFZE8zmgzI=",  // ISRG Root X2, backup (exp 2040-09-17)
    ]

    /// When false, a pin mismatch is logged but the request proceeds.
    ///
    /// **Deliberately still false.** The pins above are live and evaluated on
    /// every request, so a wrong or stale pin now shows up in logs — but cannot
    /// yet lock anyone out. Flip to `true` only after one full release has
    /// reported zero mismatches in the field; enabling it blind is the classic
    /// way to brick an app until the next App Review cycle.
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
    /// Must match the backend's `FREE_SCANS_PER_DAY`. The client renders the
    /// remaining count from this constant rather than from the server's
    /// `free_scans_remaining`, so the two drifting apart makes the UI lie.
    static let freeScansAllowed = 1

    /// Free tier sees only the most recent N sold flips + current-month totals.
    /// Beyond this, the "My Flips" ledger routes to the paywall.
    static let ledgerFreeSoldCap = 10

    // ── Analytics ──────────────────────────────────────────────────────────────
    /// TelemetryDeck app ID (from the telemetrydeck.com dashboard). Analytics
    /// stays a no-op until this is filled in — nothing is sent while empty.
    static let telemetryDeckAppID = "D4C9C11E-F611-4B92-9646-0DF0B2E0F10C"
}
