import CryptoKit
import Foundation
import os

/// TLS certificate pinning for the SnapWorth API.
///
/// ## Status: **active in report-only mode**
///
/// `Config.pinnedSPKIHashes` is populated (the four ISRG root keys), so every
/// request to `api.snapworth.eu` is evaluated against the pin set and a
/// mismatch is logged. `Config.pinningEnforced` is still `false`, so a mismatch
/// does not yet fail the request.
///
/// That two-stage rollout is the whole point: a wrong or stale pin bricks the
/// app for every user until they ship an update, which is a worse outage than
/// the attack it prevents. Report-only proves the pins are right against real
/// field traffic first.
///
/// ### Promoting to enforcement
///
/// 1. Ship one release with the pins live and `pinningEnforced = false`.
/// 2. Watch the `certificate_pin_mismatch` event in TelemetryDeck (and, on a
///    device you hold, the `tls` log category). Zero occurrences across a full
///    release cycle means the pin set is correct. The os_log line alone was
///    never enough: nobody can read a user's device log, so until the event
///    existed this step could not be completed and the flag stayed off.
///    Note what silence does *not* prove: a pin for a certificate the host
///    never serves is never exercised, so zero mismatches means "every chain
///    actually served matched", not "every pin is right". The ISRG Root X2 pin
///    was inert for exactly that reason and this gate passed anyway — while
///    the ECDSA chain's own root, Root YE, was missing from the set entirely.
///    `/checkup` in the ops bot hashes the chain the host serves against the
///    same set, so a server-side change that no pin covers shows up there
///    without waiting for a phone to report it.
/// 3. Set `Config.pinningEnforced = true`. A refusal then ends the request as
///    `URLError.cancelled`, which `TLSRefusal` lets `AppError` tell apart from
///    a cancelled task.
///
/// ### Rotation
///
/// The pins are root keys, so they change only when Let's Encrypt introduces a
/// new root generation, which it announces well ahead. Add the new
/// root's pin *alongside* the old ones for at least one release, never as a
/// replacement: overlapping pins are what make rotation non-disruptive. The
/// earliest of the four certificates to expire is ISRG Root X1, 2035-06-04.
///
/// Pinning roots rather than the leaf is deliberate: the leaf rotates every 90
/// days on Let's Encrypt and a renewal would lock everyone out, while a root
/// pin still prevents an arbitrary CA from impersonating the host. See
/// `Config.pinnedSPKIHashes` for why no intermediate is pinned.
final class CertificatePinningDelegate: NSObject, URLSessionDelegate {
    private let log = Logger(subsystem: "eu.snapworth.app", category: "tls")
    private let pinnedHashes: Set<String>
    private let enforced: Bool
    private let host: String

    init(pinnedHashes: Set<String> = Config.pinnedSPKIHashes,
         enforced: Bool = Config.pinningEnforced,
         host: String = Config.baseURL.host ?? "") {
        self.pinnedHashes = pinnedHashes
        self.enforced = enforced
        self.host = host
    }

    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust
        else {
            completionHandler(.performDefaultHandling, nil)
            return
        }

        // Only pin our own API. Other hosts (App Store, analytics) keep default
        // system validation.
        guard challenge.protectionSpace.host == host, !pinnedHashes.isEmpty else {
            completionHandler(.performDefaultHandling, nil)
            return
        }

        // System validation still runs first — pinning is *additional* to chain
        // validation, never a replacement for it.
        var error: CFError?
        guard SecTrustEvaluateWithError(trust, &error) else {
            log.error("TLS chain validation failed for \(self.host, privacy: .public)")
            TLSRefusal.record()
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }

        if matchesPin(trust: trust) {
            completionHandler(.useCredential, URLCredential(trust: trust))
            return
        }

        // Off-device as well as in the log: this is the only signal that can
        // ever justify flipping `pinningEnforced`. It carries no host and no
        // certificate detail — just that it happened, and whether it blocked.
        Analytics.shared.track(.certificatePinMismatch(enforced: enforced))

        if enforced {
            log.fault("certificate pin mismatch for \(self.host, privacy: .public) — refusing")
            TLSRefusal.record()
            completionHandler(.cancelAuthenticationChallenge, nil)
        } else {
            // Report-only mode: surfaces a misconfigured pin before it can lock
            // anyone out. Run here for at least one release.
            log.error("certificate pin mismatch (report-only) for \(self.host, privacy: .public)")
            completionHandler(.useCredential, URLCredential(trust: trust))
        }
    }

    /// True when any certificate in the chain has a pinned SPKI hash.
    private func matchesPin(trust: SecTrust) -> Bool {
        guard let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate] else {
            return false
        }
        for certificate in chain {
            guard let hash = Self.spkiHash(of: certificate) else {
                // Not "no match" — "could not ask". Hashing the raw key here
                // instead, which is what this used to do, produced a digest
                // unrelated to any pin and reported it as a mismatch: a key
                // shape the app cannot header was indistinguishable from an
                // attacker. Say which shape, so a rotation onto a new curve is
                // a one-line diagnosis rather than a field mystery.
                log.error("unpinnable key shape in chain for \(self.host, privacy: .public)")
                continue
            }
            if pinnedHashes.contains(hash) { return true }
        }
        return false
    }

    /// One certificate's key in the form `Config.pinnedSPKIHashes` holds —
    /// the base64 SHA-256 that `openssl x509 -pubkey | openssl pkey -pubin
    /// -outform der | openssl dgst -sha256 -binary | base64` prints — or nil
    /// when the key cannot be read or its shape has no header here.
    ///
    /// Static so a test can put a real published certificate through the
    /// exact path a handshake takes, rather than through a copy of it.
    static func spkiHash(of certificate: SecCertificate) -> String? {
        guard let publicKey = SecCertificateCopyKey(certificate),
              let keyData = SecKeyCopyExternalRepresentation(publicKey, nil) as Data?,
              let spki = subjectPublicKeyInfo(for: keyData, key: publicKey)
        else { return nil }
        return Data(SHA256.hash(data: spki)).base64EncodedString()
    }

    /// Prefixes the raw key with its ASN.1 SPKI header so the digest matches
    /// what `openssl pkey -pubin -outform der` produces. Nil when the key's
    /// shape has no header here — see `spkiHeader`.
    private static func subjectPublicKeyInfo(for keyData: Data, key: SecKey) -> Data? {
        guard let attributes = SecKeyCopyAttributes(key) as? [CFString: Any],
              let type = attributes[kSecAttrKeyType] as? String,
              let size = attributes[kSecAttrKeySizeInBits] as? Int,
              let header = Self.spkiHeader(keyType: type, sizeInBits: size)
        else { return nil }
        return Data(header) + keyData
    }

    /// The ASN.1 SPKI header for a key shape, or nil when the shape is unknown.
    ///
    /// `SecKeyCopyExternalRepresentation` hands back the bare key — an RSA
    /// `SEQUENCE { modulus, exponent }`, or an EC point `04 || X || Y` — while
    /// the pins in `Config` were generated from a full DER
    /// `SubjectPublicKeyInfo`. These are the missing wrappers.
    ///
    /// **Nil rather than the raw key.** Falling back to the bare bytes looks
    /// harmless and is not: their SHA-256 bears no relation to any pin, so the
    /// certificate reports as a mismatch, and a mismatch is the signal that
    /// decides whether `pinningEnforced` may be turned on. `ISRG Root X2` — one
    /// of the four pins in `Config` — is ECDSA **P-384**, so before the branch
    /// below existed that pin could never match anything. It was inert, and
    /// invisibly so: the chain served today ends at ISRG Root X1, which is
    /// RSA-4096 and handled, so the report-only telemetry showed zero
    /// mismatches on a pin set that was one certificate short of correct. Let's
    /// Encrypt serves the P-384 chain whenever the leaf key is ECDSA, and with
    /// enforcement on that is every request failing, for every user, until an
    /// App Store review clears.
    ///
    /// Pure and `static` so the bytes can be checked against
    /// `openssl pkey -pubout -outform der` without a keychain.
    static func spkiHeader(keyType: String, sizeInBits: Int) -> [UInt8]? {
        // `if`/`else if` rather than a `switch` over the pair: in a *pattern*
        // position `kSecAttrKeyTypeRSA as String` is read as a cast pattern,
        // not an expression, so the case has type `CFString` and cannot match
        // a `String` — "expression pattern of type 'CFString' cannot match
        // values of type 'String'", which is what CI said.
        let rsa = kSecAttrKeyTypeRSA as String
        let ec = kSecAttrKeyTypeECSECPrimeRandom as String

        if keyType == rsa, sizeInBits == 2048 {
            return [0x30, 0x82, 0x01, 0x22, 0x30, 0x0d, 0x06, 0x09, 0x2a, 0x86, 0x48,
                    0x86, 0xf7, 0x0d, 0x01, 0x01, 0x01, 0x05, 0x00, 0x03, 0x82, 0x01,
                    0x0f, 0x00]
        }
        if keyType == rsa, sizeInBits == 4096 {
            return [0x30, 0x82, 0x02, 0x22, 0x30, 0x0d, 0x06, 0x09, 0x2a, 0x86, 0x48,
                    0x86, 0xf7, 0x0d, 0x01, 0x01, 0x01, 0x05, 0x00, 0x03, 0x82, 0x02,
                    0x0f, 0x00]
        }
        // prime256v1: SEQUENCE(SEQUENCE(id-ecPublicKey, secp256r1), BIT STRING)
        if keyType == ec, sizeInBits == 256 {
            return [0x30, 0x59, 0x30, 0x13, 0x06, 0x07, 0x2a, 0x86, 0x48, 0xce, 0x3d,
                    0x02, 0x01, 0x06, 0x08, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x03, 0x01,
                    0x07, 0x03, 0x42, 0x00]
        }
        // secp384r1, the curve of ISRG Root X2 and the Let's Encrypt E-series
        // intermediates. Same shape, shorter curve OID (06 05 2b 81 04 00 22)
        // and a 97-byte point instead of 65.
        if keyType == ec, sizeInBits == 384 {
            return [0x30, 0x76, 0x30, 0x10, 0x06, 0x07, 0x2a, 0x86, 0x48, 0xce, 0x3d,
                    0x02, 0x01, 0x06, 0x05, 0x2b, 0x81, 0x04, 0x00, 0x22, 0x03, 0x62,
                    0x00]
        }
        return nil
    }
}

/// When this process last refused the API's TLS challenge.
///
/// `cancelAuthenticationChallenge` ends the request with `URLError.cancelled`
/// — the code a cancelled task produces too — so the error alone cannot say
/// "the certificate was refused". `AppError.from` had a carve-out waiting for
/// `.secureConnectionFailed`, which is not what a refusal here produces, so
/// with enforcement on a pin failure would have read "Something went wrong.
/// Please try again." This is the other half of the sentence: a `.cancelled`
/// that arrives within `window` of a recorded refusal is reported as one.
///
/// A timestamp, not a flag, because the refusal and the error reach the app
/// on different paths — the delegate queue and the awaiting caller — with
/// nothing to join them on. Both sites in the delegate that refuse record it.
enum TLSRefusal {
    /// The refusal and the failed task are the same handshake, milliseconds
    /// apart; ten seconds is generous rather than tight on purpose.
    static let window: TimeInterval = 10

    private static let lastRefusal = OSAllocatedUnfairLock<Date?>(initialState: nil)

    static func record(at date: Date = .now) {
        lastRefusal.withLock { $0 = date }
    }

    static func isRecent(now: Date = .now) -> Bool {
        guard let date = lastRefusal.withLock({ $0 }) else { return false }
        let age = now.timeIntervalSince(date)
        return age >= 0 && age < window
    }
}

extension URLSession {
    /// Shared session for API traffic. Uses pinning when configured.
    static let snapWorthAPI: URLSession = {
        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = 30
        // Hard ceiling on the whole request. Without it the default is seven
        // days, and because `waitsForConnectivity` is bounded by *this* value
        // (not the request timeout), an offline scan would hang on "Analyzing…"
        // indefinitely rather than failing so the user can retry.
        configuration.timeoutIntervalForResource = 35
        configuration.waitsForConnectivity = true
        return URLSession(
            configuration: configuration,
            delegate: CertificatePinningDelegate(),
            delegateQueue: nil
        )
    }()
}
