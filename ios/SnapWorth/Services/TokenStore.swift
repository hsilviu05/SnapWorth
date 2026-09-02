import Foundation
import os.log
import Security

/// A bearer token and its expiry.
struct AccessToken: Codable, Sendable {
    let value: String
    let expiresAt: Date

    /// Treated as expiring a minute early so a token never dies mid-request.
    var isExpiringSoon: Bool { expiresAt.timeIntervalSinceNow < 60 }
}

/// Keychain-backed storage for the access token.
///
/// The Keychain — not `UserDefaults` — because the token authorises paid API
/// calls. `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly` keeps it readable
/// by background refresh while preventing it from migrating to a new device via
/// encrypted backup.
final class TokenStore: @unchecked Sendable {
    static let shared = TokenStore()

    private let service = "eu.snapworth.app.auth"
    private let account = "access-token"
    private let lock = NSLock()
    private var cached: AccessToken?

    private init() {}

    func currentToken() -> AccessToken? {
        lock.lock()
        defer { lock.unlock() }
        if let cached, !cached.isExpiringSoon { return cached }

        var query = baseQuery()
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne

        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data,
              let token = try? JSONDecoder().decode(AccessToken.self, from: data)
        else { return nil }

        cached = token
        return token.isExpiringSoon ? nil : token
    }

    func store(_ token: AccessToken) {
        guard let data = try? JSONEncoder().encode(token) else { return }
        lock.lock()
        defer { lock.unlock() }
        cached = token

        // Delete-then-add is the reliable upsert; SecItemUpdate fails when the
        // item doesn't exist yet.
        SecItemDelete(baseQuery() as CFDictionary)
        var attributes = baseQuery()
        attributes[kSecValueData as String] = data
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(attributes as CFDictionary, nil)
    }

    func clear() {
        lock.lock()
        defer { lock.unlock() }
        cached = nil
        SecItemDelete(baseQuery() as CFDictionary)
    }

    private func baseQuery() -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }
}

// MARK: - Device identity

/// Where `DeviceIdentity` keeps its value. The Keychain in the app; memory in
/// tests, so the migration and caching logic is testable without touching the
/// simulator's real Keychain.
protocol DeviceIdentityStore {
    func read() -> String?
    /// Returns false when the value could not be persisted.
    func write(_ value: String) -> Bool
}

/// A stable, anonymous identifier for this device.
///
/// It used to be a UUID in `UserDefaults`, which is deleted with the app. So
/// every reinstall arrived at the server as a brand-new device — and, because
/// App Attest keys are also per install, the server had no way to tell one
/// phone reinstalled six times from six phones sharing one subscription. That
/// is how the subscription device cap locked a paying subscriber out of their
/// own plan (backend PR #66 made the cap evict instead of refuse, and asked for
/// exactly this: an identity that survives reinstall).
///
/// The Keychain outlives app deletion. `ThisDeviceOnly` means the value is
/// never carried to another device by a backup, so two phones restored from one
/// backup are correctly two devices.
///
/// An existing install keeps the id it already has: the `UserDefaults` value is
/// adopted into the Keychain on first read, so upgrading does not reset the
/// server's rate-limit view of this device.
final class DeviceIdentity: @unchecked Sendable {
    static let shared = DeviceIdentity()

    static let legacyDefaultsKey = "snapworth_device_id"

    private let store: DeviceIdentityStore
    private let defaults: UserDefaults
    private let lock = NSLock()
    private var cached: String?

    init(store: DeviceIdentityStore = KeychainDeviceIdentityStore(),
         defaults: UserDefaults = .standard) {
        self.store = store
        self.defaults = defaults
    }

    var id: String {
        lock.lock()
        defer { lock.unlock() }
        if let cached { return cached }

        if let stored = store.read() {
            cached = stored
            return stored
        }

        let value = defaults.string(forKey: Self.legacyDefaultsKey) ?? UUID().uuidString
        // Kept in UserDefaults as well: if the Keychain write fails (it can,
        // briefly, before first unlock), the next launch still finds this value
        // and adopts it rather than minting another.
        defaults.set(value, forKey: Self.legacyDefaultsKey)
        _ = store.write(value)
        cached = value
        return value
    }
}

struct KeychainDeviceIdentityStore: DeviceIdentityStore {
    var service = "eu.snapworth.app.device"
    private let account = "device-id"

    func read() -> String? {
        var query = baseQuery()
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data,
              let value = String(data: data, encoding: .utf8), !value.isEmpty
        else { return nil }
        return value
    }

    func write(_ value: String) -> Bool {
        SecItemDelete(baseQuery() as CFDictionary)
        var attributes = baseQuery()
        attributes[kSecValueData as String] = Data(value.utf8)
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        return SecItemAdd(attributes as CFDictionary, nil) == errSecSuccess
    }

    private func baseQuery() -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }
}

extension URLRequest {
    /// Attaches a bearer token, minting one via App Attest if needed.
    ///
    /// Deliberately non-throwing: during rollout the server still accepts
    /// unauthenticated requests, so an attestation failure (unsupported device,
    /// offline, simulator) must degrade to the legacy path rather than block a
    /// scan. Once the server enforces, it will answer 401 and the client
    /// surfaces that as a normal server error.
    mutating func attachBearerToken() async {
        guard Config.useAttestation else { return }
        do {
            let token = try await AttestationService.shared.accessToken()
            setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        } catch {
            // Logged rather than thrown — see note above.
            os_log(.info, "attestation unavailable, continuing unauthenticated: %{public}@",
                   error.localizedDescription)
        }
    }

    /// Sends the request, and on a 401 re-mints the token and sends it once more.
    ///
    /// A 401 means the credential we attached is not acceptable to the server.
    /// Left alone, `accessToken()` keeps returning that same token from cache
    /// until it expires — up to an hour — so every retry the user makes fails
    /// identically, and the "pull to retry, it should reconnect automatically"
    /// copy promises a recovery that never happens.
    ///
    /// Exactly one retry. If a freshly minted token is also rejected, the
    /// problem is not staleness and looping would only multiply the failure.
    func sendRetryingAuth(on session: URLSession) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await session.data(for: self)
        guard let http = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        guard http.statusCode == 401, Config.useAttestation else {
            return (data, http)
        }

        await AttestationService.shared.invalidateCachedToken()
        var retry = self
        await retry.attachBearerToken()
        // No new token means attestation itself is failing; returning the
        // original 401 reports that honestly rather than re-sending the same
        // request to get the same answer.
        guard retry.value(forHTTPHeaderField: "Authorization")
                != value(forHTTPHeaderField: "Authorization") else {
            return (data, http)
        }

        let (retryData, retryResponse) = try await session.data(for: retry)
        guard let retryHTTP = retryResponse as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        return (retryData, retryHTTP)
    }
}
