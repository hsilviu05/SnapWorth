import Foundation

enum AppError: LocalizedError, Equatable {
    case network
    case timeout
    /// The per-hour request limit, with the real remaining wait when the
    /// server told us. See `rateLimitMessage`.
    case rateLimit(retryAfter: TimeInterval?)
    /// Free daily allowance is spent — the server is the authority on this.
    case quotaExceeded(String)
    /// A Pro-only endpoint refused a free-tier caller.
    case proRequired(String)
    case serverUnavailable
    /// The scan pipeline reported why it failed — a real outage, an unreadable
    /// model response, or an item the AI could not price. The message is
    /// user-safe copy written by the backend for that specific failure;
    /// substituting a fixed "temporarily unavailable" string here told users
    /// the service was down when they had photographed something unpriceable,
    /// inviting them to retry the identical photo and fail identically.
    case aiFailed(String)
    /// The device's credential expired or attestation failed. Recoverable by
    /// retrying — the client re-attests automatically on the next request.
    case sessionExpired
    case imageEncodingFailed
    case unusablePhoto(String)
    case purchaseCancelled
    case purchaseFailed(String)
    case persistence
    case unknown(String)

    /// A 402 from the server: the free allowance is spent, or a Pro-only
    /// endpoint refused a free caller. Not a failure — the paywall, arriving
    /// from the authority that actually counts scans.
    ///
    /// The client's own pre-flight gate misses it whenever the two disagree
    /// about which day it is: `FreeScanCounter` resets at *local* midnight,
    /// `quota.py` counts *UTC* days. In the hours between, a user who has
    /// scanned today reads as fresh to the client and spent to the server.
    var isPaywall: Bool {
        switch self {
        case .quotaExceeded, .proRequired: return true
        default:                           return false
        }
    }

    var errorDescription: String? {
        switch self {
        case .network:
            return "No internet connection. Check your network and try again."
        case .timeout:
            return "The request timed out. Please try again."
        case .rateLimit(let retryAfter):
            return Self.rateLimitMessage(retryAfter: retryAfter)
        case .quotaExceeded(let msg), .proRequired(let msg):
            return msg
        case .serverUnavailable:
            return "Our AI is temporarily unavailable. Please try again in a moment."
        case .aiFailed(let msg):
            // Backend copy, shown verbatim — the same contract as .unusablePhoto.
            return msg
        case .sessionExpired:
            // 401 previously fell through to .unknown -> "Something went wrong",
            // which tells the user nothing and offers no way forward.
            //
            // The earlier wording here — "pull to retry, it should reconnect
            // automatically" — was a promise the code did not keep: retrying
            // re-sent the same cached token and failed identically for up to an
            // hour. Now that URLRequest.sendRetryingAuth re-mints and retries
            // once on its own, reaching this message means a *freshly minted*
            // credential was also refused. So it is not transient, and the copy
            // should offer the remedy that actually clears a bad credential
            // rather than suggest the retry we already performed.
            #if targetEnvironment(simulator)
            // App Attest does not exist in the Simulator, and production
            // refuses unattested scans, so this failure is certain here and
            // reinstalling cannot help. Say the real reason to the one person
            // who will ever see it in a Simulator: the developer.
            return "Scanning needs a real iPhone — device verification (App Attest) isn't available in the Simulator."
            #else
            return "We couldn't verify this device. Try again — if it keeps happening, reinstalling the app will reset it."
            #endif
        case .imageEncodingFailed:
            return "Could not process the photo. Please try a different image."
        case .unusablePhoto(let msg):
            // Backend copy, shown verbatim: it names the fix (a clearer photo,
            // one item in frame) rather than reporting a fault.
            return msg
        case .purchaseCancelled:
            return nil
        case .purchaseFailed(let msg):
            return msg
        case .persistence:
            return "Could not save your scan. Please try again."
        case .unknown:
            return "Something went wrong. Please try again."
        }
    }

    /// Copy for a 429, using the wait the server actually computed.
    ///
    /// The fixed "Try again in an hour" this replaces was wrong by up to an
    /// hour in the user's disfavour: the limit is a *sliding* window, so
    /// someone who tripped it 55 minutes ago is a few minutes from being able
    /// to scan again and was being told to come back in an hour. The backend
    /// has always sent the real remaining seconds
    /// (`retry_after=max(1, int(window - (now - oldest)))`); nothing read them.
    ///
    /// Rounding is always *up*, so the message never invites a retry that will
    /// fail again. `nil` means the header was absent or unparseable, and the
    /// honest answer there is the window's own length.
    ///
    /// Pure and `static` so the boundaries are testable without a server: the
    /// interesting cases are 59s versus 60s, and 59 minutes versus 60.
    static func rateLimitMessage(retryAfter: TimeInterval?) -> String {
        let lead = "You've hit the scan limit."
        guard let seconds = retryAfter else {
            return "\(lead) Try again in an hour."
        }
        if seconds < 10 {
            return "\(lead) Try again in a few seconds."
        }
        if seconds < 60 {
            return "\(lead) Try again in \(Int(seconds)) seconds."
        }
        let minutes = Int((seconds / 60).rounded(.up))
        if minutes >= 60 {
            return "\(lead) Try again in an hour."
        }
        if minutes == 1 {
            return "\(lead) Try again in a minute."
        }
        return "\(lead) Try again in \(minutes) minutes."
    }

    static func from(_ error: Error) -> AppError {
        if let appErr = error as? AppError { return appErr }

        if let scanErr = error as? ScanAPIError {
            switch scanErr {
            case .imageEncodingFailed:
                return .imageEncodingFailed
            case .rateLimited(_, let retryAfter):
                // The backend's `detail` here is "Rate limit: 20
                // requests/hour." — accurate and useless to someone standing in
                // a shop. This is the one status whose copy is better written
                // on the client, because what the user needs is the *wait*, and
                // that arrives in a header.
                return .rateLimit(retryAfter: retryAfter)
            case .serverError(let code, let detail):
                switch code {
                // Reachable only if something throws a plain `serverError`
                // with a 429; `ScanAPIError.from` produces `.rateLimited`,
                // which carries the header and is handled above.
                case 429:        return .rateLimit(retryAfter: nil)
                // 402 is the server saying "this needs payment" — either the
                // free daily allowance is spent, or a Pro-only endpoint refused
                // a free caller. Both route to the paywall, and `detail` is
                // already user-safe copy written by the backend.
                case 402:        return detail.lowercased().contains("pro feature")
                                     ? .proRequired(detail)
                                     : .quotaExceeded(detail)
                case 401:        return .sessionExpired
                // 422 is the server saying it looked at the photo and could not
                // use it — a safety block, or an image it cannot read. The
                // detail is user-safe copy written by the backend and tells the
                // user what to do differently ("try a clear photo of a single
                // item"). It used to fall through to .unknown, which threw that
                // away and said "Something went wrong", leaving the user to
                // retry the identical photo and fail identically.
                case 422:        return .unusablePhoto(detail)
                // 502 carries four distinct, user-safe explanations from the
                // backend: a genuine outage, an unreadable model response, an
                // item the AI couldn't price, and a listing-generation outage.
                // Only the first is "temporarily unavailable" — collapsing all
                // four into that fixed string told a user with an unpriceable
                // photo that the service was down. Surface the detail the way
                // 422 does; the outage detail still reads as an outage because
                // the backend's own copy says so. Empty detail keeps the fixed
                // string, and 503 really is the service refusing traffic.
                case 502:        return detail.isEmpty ? .serverUnavailable
                                                       : .aiFailed(detail)
                case 503:        return .serverUnavailable
                default:         return .unknown(detail)
                }
            }
        }

        // A rolled-back save. Callers that only want to report the failure
        // should not have to know the error carries a replacement row.
        if let failure = error as? ScanPersistenceError,
           case .saveFailed = failure { return .persistence }

        if let purchaseErr = error as? PurchaseError {
            switch purchaseErr {
            case .cancelled:          return .purchaseCancelled
            case .failed(let msg):    return .purchaseFailed(msg)
            case .notConfigured:      return .purchaseFailed("In-app purchases are not available right now.")
            }
        }

        let url = error as? URLError
        switch url?.code {
        case .notConnectedToInternet, .networkConnectionLost, .cannotConnectToHost:
            return .network
        case .timedOut:
            return .timeout
        default:
            break
        }

        let msg = error.localizedDescription.lowercased()
        if msg.contains("429") || msg.contains("rate limit")       { return .rateLimit(retryAfter: nil) }
        if msg.contains("network") || msg.contains("offline")      { return .network }
        if msg.contains("timeout") || msg.contains("timed out")    { return .timeout }
        if msg.contains("502") || msg.contains("503")              { return .serverUnavailable }

        return .unknown(error.localizedDescription)
    }

    static func == (lhs: AppError, rhs: AppError) -> Bool {
        switch (lhs, rhs) {
        case (.network, .network),
             (.timeout, .timeout),
             (.serverUnavailable, .serverUnavailable),
             // Omitted when .sessionExpired was introduced, so it fell to the
             // `default: false` arm and did not equal itself. Any `== `
             // comparison or SwiftUI alert de-duplication on this case was
             // silently wrong.
             (.sessionExpired, .sessionExpired),
             (.imageEncodingFailed, .imageEncodingFailed),
             (.purchaseCancelled, .purchaseCancelled),
             (.persistence, .persistence):
            return true
        case (.rateLimit(let a), .rateLimit(let b)):           return a == b
        case (.purchaseFailed(let a), .purchaseFailed(let b)): return a == b
        case (.unknown(let a), .unknown(let b)):               return a == b
        case (.quotaExceeded(let a), .quotaExceeded(let b)):   return a == b
        case (.proRequired(let a), .proRequired(let b)):       return a == b
        // Compared by message, like every other case carrying server copy.
        // Matching on the case alone would make two different "why this photo
        // failed" explanations equal, and the alert would not re-present when
        // the reason changed.
        case (.unusablePhoto(let a), .unusablePhoto(let b)):   return a == b
        case (.aiFailed(let a), .aiFailed(let b)):             return a == b
        default: return false
        }
    }
}
