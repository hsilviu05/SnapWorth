import CryptoKit
import Foundation
import os.log

/// TLS certificate pinning for the SnapWorth API.
///
/// ## Status: infrastructure complete, **not yet enabled**
///
/// Pinning is inert until `Config.pinnedSPKIHashes` contains at least one value.
/// That is deliberate — shipping a wrong or stale pin bricks the app for every
/// user until they update, which is a worse outage than the attack it prevents.
///
/// ### What is still required to enable it
///
/// 1. Extract the SPKI hash of the **intermediate CA** for `api.snapworth.eu`:
///
///    ```
///    openssl s_client -connect api.snapworth.eu:443 -showcerts </dev/null 2>/dev/null \
///      | openssl x509 -noout -pubkey \
///      | openssl pkey -pubin -outform der \
///      | openssl dgst -sha256 -binary \
///      | base64
///    ```
///
/// 2. Repeat for the **backup pin** — a second CA you could migrate to. Pinning
///    a single leaf is the classic way to brick an app: leaves rotate every 90
///    days on Let's Encrypt, and a renewal would lock everyone out.
///
/// 3. Put both in `Config.pinnedSPKIHashes`, then set `Config.pinningEnforced`.
///
/// Pin the intermediate rather than the leaf: it survives certificate renewal
/// while still preventing an arbitrary CA from impersonating the host.
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
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }

        if matchesPin(trust: trust) {
            completionHandler(.useCredential, URLCredential(trust: trust))
            return
        }

        if enforced {
            log.fault("certificate pin mismatch for \(self.host, privacy: .public) — refusing")
            completionHandler(.cancelAuthenticationChallenge, nil)
        } else {
            // Report-only mode: surfaces a misconfigured pin in logs before it
            // can lock anyone out. Run here for at least one release.
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
            guard let publicKey = SecCertificateCopyKey(certificate),
                  let data = SecKeyCopyExternalRepresentation(publicKey, nil) as Data?
            else { continue }
            let digest = Data(SHA256.hash(data: subjectPublicKeyInfo(for: data, key: publicKey)))
            if pinnedHashes.contains(digest.base64EncodedString()) { return true }
        }
        return false
    }

    /// Prefixes the raw key with its ASN.1 SPKI header so the digest matches
    /// what `openssl pkey -pubin -outform der` produces.
    private func subjectPublicKeyInfo(for keyData: Data, key: SecKey) -> Data {
        guard let attributes = SecKeyCopyAttributes(key) as? [CFString: Any],
              let type = attributes[kSecAttrKeyType] as? String,
              let size = attributes[kSecAttrKeySizeInBits] as? Int
        else { return keyData }

        let header: [UInt8]
        if type == (kSecAttrKeyTypeRSA as String), size == 2048 {
            header = [0x30, 0x82, 0x01, 0x22, 0x30, 0x0d, 0x06, 0x09, 0x2a, 0x86, 0x48,
                      0x86, 0xf7, 0x0d, 0x01, 0x01, 0x01, 0x05, 0x00, 0x03, 0x82, 0x01,
                      0x0f, 0x00]
        } else if type == (kSecAttrKeyTypeRSA as String), size == 4096 {
            header = [0x30, 0x82, 0x02, 0x22, 0x30, 0x0d, 0x06, 0x09, 0x2a, 0x86, 0x48,
                      0x86, 0xf7, 0x0d, 0x01, 0x01, 0x01, 0x05, 0x00, 0x03, 0x82, 0x02,
                      0x0f, 0x00]
        } else if type == (kSecAttrKeyTypeECSECPrimeRandom as String), size == 256 {
            header = [0x30, 0x59, 0x30, 0x13, 0x06, 0x07, 0x2a, 0x86, 0x48, 0xce, 0x3d,
                      0x02, 0x01, 0x06, 0x08, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x03, 0x01,
                      0x07, 0x03, 0x42, 0x00]
        } else {
            return keyData
        }
        return Data(header) + keyData
    }
}

extension URLSession {
    /// Shared session for API traffic. Uses pinning when configured.
    static let snapWorthAPI: URLSession = {
        let configuration = URLSessionConfiguration.default
        configuration.waitsForConnectivity = true
        configuration.timeoutIntervalForRequest = 30
        return URLSession(
            configuration: configuration,
            delegate: CertificatePinningDelegate(),
            delegateQueue: nil
        )
    }()
}
