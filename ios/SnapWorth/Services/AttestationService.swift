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

    init(baseURL: URL = Config.baseURL, session: URLSession = .shared) {
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
        request.httpBody = try JSONEncoder().encode(["signed_transaction": signedTransaction])

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
            }
        }
        return try await attestFresh()
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
        return token.value
    }

    private static func detail(from data: Data) -> String {
        (try? JSONDecoder().decode([String: String].self, from: data))?["detail"]
            ?? "Request failed"
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
    let freeScansRemaining: Int

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case expiresIn = "expires_in"
        case tier
        case freeScansRemaining = "free_scans_remaining"
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
