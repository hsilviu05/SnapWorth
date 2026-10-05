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
    /// `api.snapworth.eu`: the four root keys every Let's Encrypt chain ends
    /// in. Hashed 2026-09-27 from the PEMs Let's Encrypt publishes at
    /// letsencrypt.org/certificates, and checked against the live chain.
    ///
    /// **What is served.** The RSA chain is `leaf → YR1 → Root YR`, with Root
    /// YR cross-signed by ISRG Root X1; a phone completes it to X1, or stops
    /// at Root YR when its trust store already carries that root. When the
    /// leaf key is ECDSA it is `leaf → YE1-3 → Root YE`, cross-signed by
    /// ISRG Root X2 — so a phone that trusts Root YE directly evaluates a
    /// chain with no X2 in it, which is why Root YE is here and why X2 alone
    /// was never enough. Let's Encrypt's older R10-R14 and E5-E9
    /// intermediates chain to X1 and X2, also pinned.
    ///
    /// **Roots, not intermediates.** `matchesPin` accepts a match on *any*
    /// certificate in the evaluated chain, and that chain always includes its
    /// anchor, so a root pin already covers every intermediate beneath it —
    /// an intermediate pin can only ever be a redundant alternative. This set
    /// used to lead with YR2, described as the issuing intermediate, while
    /// the host was being served YR1: nothing noticed, because a root
    /// matched. Let's Encrypt picks among YR1-YR3 (and YE1-YE3) on its own,
    /// so pinning the one seen on a given day records that day, not the
    /// chain. The leaf is never pinned: it rotates every 90 days.
    ///
    /// A pin is a key, not a certificate: the self-signed and cross-signed
    /// Root YR share one key and so one hash. The dates below are the
    /// self-signed roots' expiry, the later of the two.
    ///
    /// Regenerate from a published certificate with:
    /// ```
    /// curl -s https://letsencrypt.org/certs/gen-y/root-ye.pem \
    ///   | openssl x509 -noout -pubkey | openssl pkey -pubin -outform der \
    ///   | openssl dgst -sha256 -binary | base64
    /// ```
    /// and compare what the host serves with `/checkup` in the ops bot, which
    /// hashes the live chain against this same set (`backend/notify.py`,
    /// `PINNED_SPKI_HASHES` — a backend test fails when the two differ).
    static let pinnedSPKIHashes: Set<String> = [
        "fk6IOKit1ild5647BH06ujSIq5XbCgqlbYl6ANhhi88=",  // ISRG Root YR, RSA-4096   (exp 2045-09-02)
        "sCkq5UWXjg+7mKu9lMhhYF5bGLsy7VI/UNW3tccdR7w=",  // ISRG Root YE, P-384      (exp 2045-09-02)
        "C5+lpZ7tcVwmwQIMcRtPbsQtWLABXhQzejna0wHFr8M=",  // ISRG Root X1, RSA-4096   (exp 2035-06-04)
        "diGVwiVYbubAI3RW4hB9xU8e/CH2GnkuvVFZE8zmgzI=",  // ISRG Root X2, P-384      (exp 2040-09-17)
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
    /// The app's App Store page: the review link (`SettingsView` appends
    /// `?action=write-review`, so this stays a bare product URL) and the
    /// button beside "no longer supported" (`AppError.updateRequired`).
    static let appStoreURL = "https://apps.apple.com/app/id6788521307"

    /// What a share card's QR code opens: a short path on the website, one per
    /// card, which `website/vercel.json` redirects to that card's App Store
    /// campaign link (`ct=share_<kind>`, #221). A bare product URL credited
    /// every install from a shared image to one undivided "web" number.
    ///
    /// The redirect, not the campaign link itself, because a shared image can
    /// never be changed: the site can retarget `/get/haul` without a release,
    /// and the shorter URL keeps the code sparse enough to scan off a story.
    /// `ShareCardURLTests` holds each kind to a redirect in vercel.json.
    static func shareCardURL(for kind: ShareCardKind) -> String {
        "https://www.snapworth.eu/get/\(kind.rawValue)"
    }

    // ── Which build is calling ───────────────────────────────────────────────
    /// Sent on every API request (`URLSession.snapWorthAPI`), so the server
    /// knows the build without reading it out of URLSession's default
    /// User-Agent — which this app does not write, and which is all every
    /// earlier build sends. Its presence also tells the server's
    /// minimum-build gate that this build can show a 426
    /// (`observability.client_build`).
    static let buildHeaderField = "X-SnapWorth-Build"

    /// `CFBundleVersion`, which is `$(CURRENT_PROJECT_VERSION)`: the build
    /// number, digits only, which is all the server reads.
    static var buildNumber: String? {
        Bundle.main.infoDictionary?["CFBundleVersion"] as? String
    }

    // ── Support ──────────────────────────────────────────────────────────────
    /// The one place the support address lives.
    ///
    /// It used to be typed out in four Swift files. That is how 1.3.4 shipped
    /// with the app pointing at one inbox and the website at another (see
    /// `marketing/RELEASE-NOTES-1.3.4.md`) — a drift no test could catch
    /// because there was nothing to compare against.
    static let supportEmail = "her.silviu.i@gmail.com"

    // ── Free tier ────────────────────────────────────────────────────────────
    /// Must match the backend's `FREE_SCANS_PER_DAY`. The client renders the
    /// remaining count from this constant rather than from the server's
    /// `free_scans_remaining`, so the two drifting apart makes the UI lie.
    static let freeScansAllowed = 1

    /// Free tier sees only the most recent N sold flips + current-month totals.
    /// Beyond this, the "My Flips" ledger routes to the paywall.
    static let ledgerFreeSoldCap = 10

    // ── Easter eggs ──────────────────────────────────────────────────────────
    /// The "rare find" appraisal for one particular shirt — see `RareFind`.
    ///
    /// Compile-time, like everything in this file: the app has no remote
    /// config, and the backend's runtime switches are never read by the
    /// client. So this cannot be turned off from a server — switching it off
    /// takes a build. With it false no detection runs at all and a scan is
    /// exactly the scan it was before the easter egg existed.
    ///
    /// It ships dark until the owner picks the release it goes out in: on in
    /// Debug, so a run from Xcode and the test suite exercise it, and off in
    /// Release, which is every archive — TestFlight and the App Store alike.
    /// Holding the merge back would not do the same job: a bump commit only
    /// bounds which build a merge lands in (see CLAUDE.md). Turning it on for
    /// users is replacing this `#if` with `true`, in a PR of its own.
    #if DEBUG
    static let rareFindEasterEggEnabled = true
    #else
    static let rareFindEasterEggEnabled = false
    #endif

    // ── Analytics ──────────────────────────────────────────────────────────────
    /// TelemetryDeck app ID (from the telemetrydeck.com dashboard). Analytics
    /// stays a no-op until this is filled in — nothing is sent while empty.
    static let telemetryDeckAppID = "D4C9C11E-F611-4B92-9646-0DF0B2E0F10C"

    /// Salt for the anonymous per-device identifier. **Never change this.**
    ///
    /// The SDK computes every signal's user identifier as
    /// `sha256(identifierForVendor, salt:)`, and the `salt:` argument was never
    /// passed — it defaults to the empty string, so what shipped was a plain
    /// `sha256(IDFV)`. That is a globally fixed function with no app-specific
    /// input: anyone holding a device's IDFV could compute its exact identifier
    /// and single out that device's entire analytics history. IDFV is shared by
    /// every app under the same team identifier and is trivially printable from
    /// any build, which is precisely the linkage a salt exists to break — and
    /// the in-app privacy policy has been promising "a one-way salted hash" the
    /// whole time.
    ///
    /// This is not a credential. It ships inside the binary by design, so it
    /// defeats an outside party who has an IDFV, not someone who
    /// reverse-engineers the app — which is the threat that matters here.
    ///
    /// Rotating it makes every existing user look like a new one, so it is
    /// fixed for the life of the app. Added before 1.4.0 deliberately: the
    /// install base is the smallest it will ever be, and the one-time
    /// discontinuity in the retention figures is the price of the policy text
    /// being true.
    static let telemetryDeckSalt = "jq9!cuSbW8j=F%l4T-sfKbVB7NdHwBy5dioOYXg!T41!OKTEQ8x#FXBaCi@DJxyc"
}
