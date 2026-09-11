import CryptoKit
import DeviceCheck
import Foundation
import os.log

/// App Attest client: proves to our backend that requests come from a genuine,
/// unmodified build of this app on real Apple hardware.
///
/// Flow (mirrors `backend/auth.py`):
///   1. `POST /auth/challenge` → server nonce
///   2. `DCAppAttestService.attestKey` over that nonce → `POST /auth/attest`
///   3. Server returns a bearer token, stored in the Keychain
///   4. On expiry, `generateAssertion` over a fresh nonce → `POST /auth/refresh`
///
/// The attestation key is generated once per install and kept in the Secure
/// Enclave; only its identifier is persisted.
actor AttestationService {
    static let shared = AttestationService()

    private let log = Logger(subsystem: "eu.snapworth.app", category: "attestation")
    private let service = DCAppAttestService.shared
    private let session: URLSession
    private let baseURL: URL

    /// Set of in-flight work, so concurrent callers share one token fetch
    /// instead of each performing an expensive attestation.
    private var inFlight: Task<String, Error>?

    private enum Keys {
        static let keyID = "attestKeyID"
    }

    init(baseURL: URL = Config.baseURL, session: URLSession = .snapWorthAPI) {
        self.baseURL = baseURL
        self.session = session
    }

    /// True when the device supports App Attest. Simulators and older hardware
    /// do not; callers must degrade rather than block the user.
    nonisolated var isSupported: Bool { DCAppAttestService.shared.isSupported }

    // MARK: - Public

    /// A valid bearer token, minting one if necessary.
    func accessToken() async throws -> String {
        if let cached = TokenStore.shared.currentToken(), !cached.isExpiringSoon {
            return cached.value
        }
        // Collapse concurrent requests onto a single refresh.
        if let existing = inFlight {
            return try await existing.value
        }
        let task = Task<String, Error> { try await mintToken() }
        inFlight = task
        defer { inFlight = nil }
        return try await task.value
    }

    /// Exchanges a StoreKit signed transaction for a Pro entitlement server-side.
    /// The server is the authority on subscription state from here on.
    func submitEntitlement(signedTransaction: String) async throws {
        let token = try await accessToken()
        var request = URLRequest(url: baseURL.appendingPathComponent("auth/entitlement"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        // The device id lets the server count *devices* on a subscription
        // rather than installs: the attestation key this token carries is
        // minted per install, so without it every reinstall looked like a new
        // phone sharing the plan.
        request.httpBody = try JSONEncoder().encode(EntitlementBody(
            signedTransaction: signedTransaction,
            deviceID: DeviceIdentity.shared.id))

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw AttestationError.serverRejected(Self.detail(from: data))
        }
        // The server returns a re-issued token carrying the new tier; adopting
        // it immediately avoids a window where the client still looks free.
        if let decoded = try? JSONDecoder().decode(EntitlementResponse.self, from: data),
           let refreshed = decoded.accessToken {
            TokenStore.shared.store(AccessToken(value: refreshed,
                                                expiresAt: Date().addingTimeInterval(3600)))
        }
    }

    /// Discards local credentials. Used on sign-out or when the server reports
    /// the key is unknown and re-attestation is required.
    func reset() {
        TokenStore.shared.clear()
        UserDefaults.standard.removeObject(forKey: Keys.keyID)
        // The id is derived from the attestation subject, so it describes a
        // device identity this call is discarding. Keeping it would have a
        // support email quote an id the indexes no longer point at.
        SupportMail.supportID = nil
    }

    /// Discards the cached token, keeping the attestation key.
    ///
    /// `accessToken()` returns the cached token whenever it is not within a
    /// minute of expiry, so a token the *server* has stopped accepting is
    /// handed back unchanged on every retry. That is not hypothetical: when the
    /// backend's signing key rotates — a deploy with an ephemeral `TOKEN_KEYS`
    /// does exactly this — every request 401s for the full hour of the token's
    /// lifetime, and "pull to retry" retries with the same dead credential.
    ///
    /// Only the token is cleared. The App Attest key in the Secure Enclave is
    /// still valid and a cheap assertion re-mints from it; throwing that away
    /// too (`reset()`) would force a full attestation the server never asked
    /// for. Being an actor method, this cannot interleave with `mintToken()`.
    func invalidateCachedToken() {
        TokenStore.shared.clear()
    }

    // MARK: - Attestation

    private func mintToken() async throws -> String {
        guard isSupported else { throw AttestationError.unsupportedDevice }

        // An existing key means we've already attested; a cheap assertion is
        // enough to prove possession.
        if let keyID = UserDefaults.standard.string(forKey: Keys.keyID) {
            do {
                return try await refresh(keyID: keyID)
            } catch AttestationError.reattestationRequired {
                log.notice("server requires re-attestation; regenerating key")
                UserDefaults.standard.removeObject(forKey: Keys.keyID)
            } catch let error where Self.requiresFreshKey(error) {
                // The key id outlived the key it names.
                //
                // The id lives in UserDefaults, which iCloud backup and Quick
                // Start restore onto a new iPhone. The Secure Enclave key it
                // refers to is hardware-bound and does not migrate, and the
                // bearer token is `…ThisDeviceOnly`, so the new device has to
                // mint — and `generateAssertion` throws `DCError.invalidKey`
                // before any request leaves the phone.
                //
                // Without this branch that error escaped `mintToken`, callers
                // sent unauthenticated, the server answered 401, and the user
                // was told to reinstall. Every retry repeated it identically:
                // nothing cleared the stale id, so the app was dead on that
                // device until a reinstall wiped UserDefaults.
                log.notice("stored App Attest key is unusable on this device; re-attesting")
                UserDefaults.standard.removeObject(forKey: Keys.keyID)
            }
        }
        return try await attestFresh()
    }

    /// Whether a `generateAssertion` failure means the stored key id is
    /// unusable *on this device*, so only a fresh attestation can recover.
    ///
    /// Deliberately narrow. Discarding the key on a transient failure is not
    /// free — it forces a full attestation and a new server record — so a
    /// DeviceCheck outage (`.serverUnavailable`) or an unexplained system
    /// failure must retry with the key we have, not throw it away. Only
    /// errors that say *this id cannot work here* qualify.
    static func requiresFreshKey(_ error: Error) -> Bool {
        guard let code = (error as? DCError)?.code else { return false }
        switch code {
        case .invalidKey, .invalidInput:
            // The Enclave has no such key (device migration), or the stored
            // string is not a key id at all. Re-attesting is the only path.
            return true
        default:
            // .serverUnavailable, .unknownSystemFailure, .featureUnsupported —
            // transient or hopeless; either way a new key does not help.
            return false
        }
    }

    private func attestFresh() async throws -> String {
        let challenge = try await fetchChallenge()
        let keyID = try await service.generateKey()

        let clientDataHash = Data(SHA256.hash(data: Data(challenge.utf8)))
        let attestation = try await service.attestKey(keyID, clientDataHash: clientDataHash)

        // `keyID` is base64 from Apple; the server expects the raw bytes it
        // decodes to, so pass it through unchanged.
        let body = AttestBody(
            keyId: keyID,
            attestation: attestation.base64EncodedString(),
            challenge: challenge,
            deviceToken: await deviceCheckToken()
        )
        let token = try await post(path: "auth/attest", body: body)
        UserDefaults.standard.set(keyID, forKey: Keys.keyID)
        log.info("attestation complete")
        return token
    }

    private func refresh(keyID: String) async throws -> String {
        let challenge = try await fetchChallenge()
        let clientDataHash = Data(SHA256.hash(data: Data(challenge.utf8)))
        let assertion = try await service.generateAssertion(keyID, clientDataHash: clientDataHash)

        let body = AssertBody(
            keyId: keyID,
            assertion: assertion.base64EncodedString(),
            challenge: challenge
        )
        return try await post(path: "auth/refresh", body: body)
    }

    /// DeviceCheck token — persists across reinstall, so the server can tell a
    /// reinstall from a genuinely new device. Optional: failure is not fatal.
    private func deviceCheckToken() async -> String? {
        guard DCDevice.current.isSupported else { return nil }
        return try? await DCDevice.current.generateToken().base64EncodedString()
    }

    // MARK: - Transport

    private func fetchChallenge() async throws -> String {
        var request = URLRequest(url: baseURL.appendingPathComponent("auth/challenge"))
        request.httpMethod = "POST"
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw AttestationError.challengeFailed
        }
        return try JSONDecoder().decode(ChallengeResponse.self, from: data).challenge
    }

    private func post<B: Encodable>(path: String, body: B) async throws -> String {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw AttestationError.serverRejected("No response")
        }
        // 401 on refresh means the server no longer knows this key (cache
        // eviction, key rotation) — the caller regenerates rather than failing.
        if http.statusCode == 401, path.hasSuffix("refresh") {
            throw AttestationError.reattestationRequired
        }
        guard http.statusCode == 200 else {
            throw AttestationError.serverRejected(Self.detail(from: data))
        }

        let decoded = try JSONDecoder().decode(TokenResponse.self, from: data)
        let token = AccessToken(
            value: decoded.accessToken,
            expiresAt: Date().addingTimeInterval(TimeInterval(decoded.expiresIn))
        )
        TokenStore.shared.store(token)

        // The mint response carries the authoritative allowance. Recording it
        // here means the count is right at launch, before any scan — including
        // a reinstall whose allowance the server withheld, which the local
        // counter would otherwise report as untouched.
        // Only a number the server actually knows is worth persisting. A nil
        // (or a fail-closed 0 the server could not substantiate) leaves the
        // local count in charge rather than pinning the user at zero.
        if decoded.tier != "pro", let remaining = decoded.freeScansRemaining {
            FreeScanCounter.serverRemaining = remaining
        }

        // Kept so a support email can quote it. Only overwritten when the
        // server actually sent one: a mint against a backend too old to know
        // the field must not erase an id we already have.
        if let id = decoded.supportID, !id.isEmpty {
            SupportMail.supportID = id
        }
        return token.value
    }

    private static func detail(from data: Data) -> String {
        APIErrorDetail.parse(data)
    }
}

// MARK: - Wire types

private struct ChallengeResponse: Decodable {
    let challenge: String
}

private struct AttestBody: Encodable {
    let keyId: String
    let attestation: String
    let challenge: String
    let deviceToken: String?

    enum CodingKeys: String, CodingKey {
        case keyId = "key_id"
        case attestation
        case challenge
        case deviceToken = "device_token"
    }
}

private struct AssertBody: Encodable {
    let keyId: String
    let assertion: String
    let challenge: String

    enum CodingKeys: String, CodingKey {
        case keyId = "key_id"
        case assertion
        case challenge
    }
}

private struct TokenResponse: Decodable {
    let accessToken: String
    let expiresIn: Int
    let tier: String
    /// Optional on purpose, matching `ScanResponse`'s field of the same name.
    ///
    /// When the quota store is unreachable the server fails closed and sends 0
    /// (`auth.py`), which is right for that request and wrong to persist: the
    /// client wrote it into the day-stamped counter and preferred it over the
    /// local count, so one blip at mint locked a free user out for the rest of
    /// their local day with no way to clear it. `/scan` already sends `null`
    /// for the same condition; this makes the two agree.
    let freeScansRemaining: Int?

    /// The device's pseudonym in the operator's own indexes, for quoting in a
    /// support email. Optional because a client can outrun the deploy that
    /// started sending it, and an absent id must degrade to "no id" rather
    /// than failing the mint that carries the access token.
    let supportID: String?

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case expiresIn = "expires_in"
        case tier
        case freeScansRemaining = "free_scans_remaining"
        case supportID = "support_id"
    }
}

private struct EntitlementBody: Encodable {
    let signedTransaction: String
    let deviceID: String

    enum CodingKeys: String, CodingKey {
        case signedTransaction = "signed_transaction"
        case deviceID = "device_id"
    }
}

private struct EntitlementResponse: Decodable {
    let tier: String
    let accessToken: String?

    enum CodingKeys: String, CodingKey {
        case tier
        case accessToken = "access_token"
    }
}

enum AttestationError: LocalizedError {
    case unsupportedDevice
    case challengeFailed
    case reattestationRequired
    case serverRejected(String)

    var errorDescription: String? {
        switch self {
        case .unsupportedDevice:
            return "This device doesn't support secure attestation."
        case .challengeFailed:
            return "Couldn't reach SnapWorth. Check your connection and try again."
        case .reattestationRequired:
            return "Re-verification needed."
        case let .serverRejected(detail):
            return detail
        }
    }
}
