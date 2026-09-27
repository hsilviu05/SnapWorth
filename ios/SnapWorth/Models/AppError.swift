import DeviceCheck
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
    /// StoreKit shows a subscription and the server still refused, even after
    /// it was re-sent. Deliberately not `isPaywall`: offering a subscriber the
    /// plan they already pay for is the one wrong answer. See
    /// `PurchaseService.confirmingSubscription`.
    case subscriptionUnconfirmed
    case serverUnavailable
    /// The scan pipeline reported why it failed — a real outage, an unreadable
    /// model response, or an item the AI could not price. The message is
    /// specific to that failure: the server's words in English, this build's
    /// own for the code in any other language (`ServerCopy.text`). One fixed
    /// "temporarily unavailable" string here told users the service was down
    /// when they had photographed something unpriceable, inviting them to
    /// retry the identical photo and fail identically.
    case aiFailed(String)
    /// The server refused this device: its credential, even freshly minted,
    /// or its attestation. See the copy for why that is not transient.
    case sessionExpired
    /// The device could not be verified *right now*: our token service or
    /// Apple's App Attest answered with an outage. Not `sessionExpired`,
    /// whose copy suggests a reinstall — which cannot fix an outage.
    case verificationUnavailable
    /// App Attest does not run here: hardware without it, or the Simulator.
    /// Not `sessionExpired` either — a reinstall cannot add the feature.
    case deviceUnsupported
    case imageEncodingFailed
    case unusablePhoto(String)
    /// The model read the photo and priced it at zero on purpose: not
    /// something that resells (`not_resalable`). Not `unusablePhoto`, because
    /// it is a verdict on this one photo and says nothing about the next —
    /// see `HaulSession.signature(for:)`.
    case notResalable(String)
    case purchaseCancelled
    case purchaseFailed(String)
    case persistence
    /// The store could not be opened at launch, so nothing written this
    /// session survives it. Distinct from `persistence`, which is a write that
    /// failed and can be retried — this one cannot.
    case storageUnavailable
    /// The server no longer serves this build: a 426, or any body whose code
    /// is `update_required`. Not a failure to retry — the scan view offers
    /// the App Store (`Config.appStoreURL`) beside it.
    case updateRequired
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
            return String(localized: "No internet connection. Check your network and try again.")
        case .timeout:
            return String(localized: "The request timed out. Please try again.")
        case .rateLimit(let retryAfter):
            return Self.rateLimitMessage(retryAfter: retryAfter)
        case .quotaExceeded(let msg), .proRequired(let msg):
            return msg
        case .subscriptionUnconfirmed:
            return String(localized: "Apple shows an active subscription on this Apple ID, but SnapWorth couldn't confirm it just now. Restore your purchase to try again, or contact support if it keeps happening.")
        case .serverUnavailable:
            return String(localized: "Our AI is temporarily unavailable. Please try again in a moment.")
        case .aiFailed(let msg):
            // The server's words or this build's, chosen by `ServerCopy.text`
            // in `from` — as for .unusablePhoto.
            return msg
        case .sessionExpired:
            // 401 previously fell through to .unknown -> "Something went wrong",
            // which tells the user nothing and offers no way forward.
            //
            // The earlier wording here — "pull to retry, it should reconnect
            // automatically" — was a promise the code did not keep: retrying
            // re-sent the same cached token and failed identically for up to an
            // hour. Now that URLRequest.sendRetryingAuth re-mints and retries
            // once on its own, reaching this message means the server refused
            // this device: a 401 on a request whose token was just re-minted,
            // the token service rejecting the attestation or assertion itself,
            // or a key the Secure Enclave cannot use. So it is not transient,
            // and the copy should offer the remedy that actually clears a bad
            // credential — a reinstall is a new key — rather than suggest the
            // retry we already performed. A mint that failed for any other
            // reason has a case of its own: `verificationUnavailable` for an
            // outage, `deviceUnsupported` for a device without App Attest.
            #if targetEnvironment(simulator)
            // App Attest does not exist in the Simulator, and production
            // refuses unattested scans, so this failure is certain here and
            // reinstalling cannot help. Say the real reason to the one person
            // who will ever see it in a Simulator: the developer.
            return String(localized: "Scanning needs a real iPhone — device verification (App Attest) isn't available in the Simulator.")
            #else
            return String(localized: "We couldn't verify this device. Try again — if it keeps happening, reinstalling the app will reset it.")
            #endif
        case .verificationUnavailable:
            return String(localized: "We couldn't verify this device just now. Please try again in a moment.")
        case .deviceUnsupported:
            #if targetEnvironment(simulator)
            // The mint fails before any network here, so this — not
            // `sessionExpired` — is what the developer meets in the Simulator.
            return String(localized: "Scanning needs a real iPhone — device verification (App Attest) isn't available in the Simulator.")
            #else
            return String(localized: "This device doesn't support secure attestation.")
            #endif
        case .imageEncodingFailed:
            return String(localized: "Could not process the photo. Please try a different image.")
        case .unusablePhoto(let msg), .notResalable(let msg):
            // It names the fix (a clearer photo, one item in frame) rather
            // than reporting a fault. The server's words in English, this
            // build's for a code it knows in any other language — see
            // `ServerCopy.text`.
            return msg
        case .purchaseCancelled:
            return nil
        case .purchaseFailed(let msg):
            return msg
        case .persistence:
            return String(localized: "Could not save your scan. Please try again.")
        case .storageUnavailable:
            // Deliberately not "try again": retrying cannot help. The store
            // failed to open at launch and the app is running on a throwaway
            // in-memory one, so a second attempt succeeds exactly as silently
            // as the first and is lost the same way.
            return String(localized: "SnapWorth couldn't open your library on this launch, so this find can't be saved to it. Reopening the app may fix it.")
        case .updateRequired:
            // This build's own words, not the server's `detail`: the server
            // says the same in English, and a build that reads the code can
            // say it in the user's language.
            return ServerErrorCode.updateRequired.message
        case .unknown:
            return String(localized: "Something went wrong. Please try again.")
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
        let lead = String(localized: "You've hit the scan limit.")
        guard let seconds = retryAfter else {
            return String(localized: "\(lead) Try again in an hour.")
        }
        if seconds < 10 {
            return String(localized: "\(lead) Try again in a few seconds.")
        }
        if seconds < 60 {
            let wait = String(localized: "\(Int(seconds)) seconds")
            return String(localized: "\(lead) Try again in \(wait).")
        }
        let minutes = Int((seconds / 60).rounded(.up))
        if minutes >= 60 {
            return String(localized: "\(lead) Try again in an hour.")
        }
        if minutes == 1 {
            return String(localized: "\(lead) Try again in a minute.")
        }
        let wait = String(localized: "\(minutes) minutes")
        return String(localized: "\(lead) Try again in \(wait).")
    }

    /// - Parameter inEnglish: whether the app is running in English, which
    ///   decides between the server's words and this build's for a code it
    ///   knows — see `ServerCopy.text`. A parameter so tests can take the
    ///   other branch in an English simulator.
    static func from(_ error: Error, inEnglish: Bool = ServerCopy.appIsInEnglish) -> AppError {
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
            case .serverError(let status, let detail, let code):
                let known = code.flatMap(ServerErrorCode.init(rawValue:))
                // Before the status. A 426 is only ever this, and the same
                // refusal reaches a build the server read from its
                // User-Agent as a 422 — the status it can show — with the
                // same code.
                if status == 426 || known == .updateRequired { return .updateRequired }
                // For the cases below that show server copy: the server's
                // `detail`, or this build's words for its code.
                let text = ServerCopy.text(server: detail, translated: known?.message,
                                           inEnglish: inEnglish)
                switch status {
                // Reachable only if something throws a plain `serverError`
                // with a 429; `ScanAPIError.from` produces `.rateLimited`,
                // which carries the header and is handled above.
                case 429:        return .rateLimit(retryAfter: nil)
                // 402 is the server saying "this needs payment" — either the
                // free daily allowance is spent, or a Pro-only endpoint refused
                // a free caller. Both route to the paywall, carrying `text`.
                //
                // Told apart by the code. It used to be by the English — did
                // `detail` contain "pro feature"? — so rewording either
                // message on the server would have sent users to the wrong
                // screen. The words are still the test for a body without a
                // code: a backend rolled back to before the codes.
                case 402:
                    switch known {
                    case .proRequired?:    return .proRequired(text)
                    case .quotaExhausted?: return .quotaExceeded(text)
                    default:
                        return detail.lowercased().contains("pro feature")
                            ? .proRequired(text)
                            : .quotaExceeded(text)
                    }
                case 401:        return .sessionExpired
                // 422 is the server saying it looked at the photo and could not
                // use it — a safety block, or an image it cannot read. The
                // text tells the user what to do differently ("try a clear
                // photo of a single item"). It used to fall through to
                // .unknown, which threw that away and said "Something went
                // wrong", leaving the user to retry the identical photo and
                // fail identically.
                //
                // A not-resalable verdict is a 422 too, and gets its own case:
                // it is about this photo alone, where a safety block or a
                // paused device repeats for every photo after it.
                case 422:        return known == .notResalable
                                     ? .notResalable(text)
                                     : .unusablePhoto(text)
                // 502 carries four distinct, user-safe explanations from the
                // backend: a genuine outage, an unreadable model response, an
                // item the AI couldn't price, and a listing-generation outage.
                // Only the first is "temporarily unavailable" — collapsing all
                // four into that fixed string told a user with an unpriceable
                // photo that the service was down. Surface the text the way
                // 422 does; an outage still reads as an outage because its
                // words — the server's, or this build's for `ai_unavailable` —
                // say so. Empty detail keeps the fixed string, and 503 really
                // is the service refusing traffic.
                //
                // `detail.isEmpty` never held: `APIErrorDetail.parse` returns
                // its own fixed fallback — the very words `.unknown` prints —
                // when there is no usable `detail` in the body, so a real
                // outage with an empty body arrived here carrying "Something
                // went wrong. Please try again." and became `.aiFailed` with
                // that text. The one branch written for an outage could not be
                // reached, and the user was told to try again rather than that
                // the service was down. Test for what the parser actually
                // produces.
                case 502:        return Self.isPlaceholderDetail(detail)
                                     ? .serverUnavailable
                                     : .aiFailed(text)
                case 503:        return .serverUnavailable
                default:         return .unknown(detail)
                }
            }
        }

        // A rolled-back save. Callers that only want to report the failure
        // should not have to know the error carries a replacement row.
        if let failure = error as? ScanPersistenceError {
            switch failure {
            case .saveFailed:      return .persistence
            case .storeUnavailable: return .storageUnavailable
            }
        }

        // Minting the token failed, so nothing was uploaded. Only a real
        // verdict on the device reads as `sessionExpired`, whose copy says to
        // reinstall; an outage — ours or App Attest's — reads as one, and a
        // device App Attest does not run on is told that. Network failures
        // and a 429 arrive as `URLError` and `ScanAPIError` and are mapped
        // with the rest.
        if let attestation = error as? AttestationError {
            switch attestation {
            case .challengeFailed, .unavailable:
                return .verificationUnavailable
            case .unsupportedDevice:
                return .deviceUnsupported
            case .reattestationRequired, .serverRejected:
                return .sessionExpired
            }
        }
        if let deviceCheck = error as? DCError {
            switch deviceCheck.code {
            // The stored key names nothing this Enclave can use. A new key is
            // the only cure, and a reinstall is what makes one.
            case .invalidKey, .invalidInput: return .sessionExpired
            case .featureUnsupported:        return .deviceUnsupported
            // `.serverUnavailable`, `.unknownSystemFailure`, anything newer:
            // `requiresFreshKey` keeps the key for these, and a reinstall,
            // which only replaces it, would not help either.
            default:                         return .verificationUnavailable
            }
        }

        if let purchaseErr = error as? PurchaseError {
            switch purchaseErr {
            case .cancelled:          return .purchaseCancelled
            case .failed(let msg):    return .purchaseFailed(msg)
            case .notConfigured:
                return .purchaseFailed(String(localized: "In-app purchases are not available right now."))
            }
        }

        let url = error as? URLError
        switch url?.code {
        // Every one of these is "the phone could not reach us", and all of
        // them used to fall through. The substring test below cannot rescue
        // them: iOS's own strings for these codes say "A server with the
        // specified hostname could not be found." and "An SSL error has
        // occurred…" — no "network", no "offline", no code number — so they
        // reached `.unknown` and printed "Something went wrong. Please try
        // again." to someone standing in a shop with no signal, while the
        // copy that would have told them lived one case away.
        case .notConnectedToInternet, .networkConnectionLost, .cannotConnectToHost,
             .cannotFindHost, .dnsLookupFailed,
             .dataNotAllowed, .internationalRoamingOff, .callIsActive:
            return .network
        case .timedOut:
            return .timeout
        // Deliberately NOT mapped here: `.secureConnectionFailed`. This app
        // pins its certificate, so that code can mean an interception rather
        // than an outage, and "check your network and try again" is the wrong
        // advice for it — the retry would be the thing that succeeds. It keeps
        // falling through to `.unknown` until it has copy of its own.
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

    /// True when `detail` is the parser's own stand-in rather than anything
    /// the backend said.
    ///
    /// `APIErrorDetail.parse` never returns an empty string: with no usable
    /// `detail` in the body it returns a fixed sentence — the same words
    /// `.unknown` prints. So `detail.isEmpty` was never true at the 502 branch
    /// above, and a genuine outage with an empty body was reported as an AI
    /// failure carrying "Something went wrong. Please try again."
    ///
    /// Compared case- and whitespace-insensitively against the literal rather
    /// than reaching into `APIErrorDetail`: this is a *presentation* decision
    /// about copy the user would see, and coupling the two types would make
    /// the client's error mapping depend on the API client's internals.
    static func isPlaceholderDetail(_ detail: String) -> Bool {
        let trimmed = detail.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty
            || trimmed.caseInsensitiveCompare("Something went wrong. Please try again.")
                == .orderedSame
    }

    // The hand-written `==` that used to live here is gone.
    //
    // Every one of its arms was exactly what the compiler synthesises — the
    // payload-free cases matched by case, the rest by their associated value —
    // so the only thing it contributed was a `default: return false` that had
    // to be kept in step by hand. Twice it was not. `.sessionExpired` was
    // omitted when it was introduced and did not equal itself, which the
    // comment on that arm recorded; `.storageUnavailable` was omitted the same
    // way and failed the same way, so an alert could not de-duplicate and
    // `AppError.from(.storeUnavailable(…)) == .storageUnavailable` was false.
    //
    // Synthesis cannot drift: a new case is covered the moment it is declared.
}

// MARK: - Server copy

/// The `code` the server sends beside `detail` on an error
/// (`backend/apierrors.py`), for the failures this build words itself.
///
/// `detail` is English, and every earlier build shows it as written — in an
/// app translated into four other languages. A code is what can be
/// translated, and routed on. One this build does not know is not an error:
/// the server's own words are shown, as they always were.
enum ServerErrorCode: String, CaseIterable {
    case updateRequired = "update_required"
    case quotaExhausted = "quota_exhausted"
    case proRequired    = "pro_required"
    case photoUnusable  = "photo_unusable"
    case notResalable   = "not_resalable"
    case devicePaused   = "device_paused"
    case aiUnavailable  = "ai_unavailable"
    case aiUnreadable   = "ai_unreadable"
    case aiNoPrice      = "ai_no_price"

    /// This build's words for it.
    ///
    /// The server's own English where that is fixed. Where the server's is
    /// more specific than any fixed sentence can be, this is the general one:
    /// `notResalable`'s `detail` opens with the model's reason for declining
    /// the photo, `quotaExhausted`'s counts the scans, and `proRequired`'s
    /// names the one Pro endpoint that sends it today. Outside English this
    /// sentence replaces that detail. Losing the model's reason there is an
    /// open owner decision, listed in `ios/Localization/README.md`.
    var message: String {
        switch self {
        case .updateRequired:
            return String(localized: "This version of SnapWorth is no longer supported. Update SnapWorth from the App Store to keep using it.")
        case .quotaExhausted:
            return String(localized: "You've used today's free scans.")
        case .proRequired:
            return String(localized: "This is a SnapWorth Pro feature.")
        case .photoUnusable:
            return String(localized: "This photo couldn't be analysed. Try a clear photo of a single item.")
        case .notResalable:
            return String(localized: "This doesn't look like something with a resale value. Try a photo of a single item you'd actually sell.")
        case .devicePaused:
            return String(localized: "Scanning from this device is paused for 24 hours after repeated photos that could not be analysed.")
        case .aiUnavailable:
            return String(localized: "The AI service is temporarily unavailable. Please try again.")
        case .aiUnreadable:
            return String(localized: "The AI response couldn't be read. Please try again.")
        case .aiNoPrice:
            return String(localized: "The AI couldn't price this item. Please try again.")
        }
    }
}

/// Which words to show for something the server wrote in English and sent
/// with a code: an error's `detail`, a confidence reason.
enum ServerCopy {
    /// Whether the app is running in English — its own language, not the
    /// phone's. A phone set to French runs the English app, and is English
    /// here.
    static var appIsInEnglish: Bool {
        Bundle.main.preferredLocalizations.first?.hasPrefix("en") ?? true
    }

    /// The server's words in English; in any other language, this build's
    /// translation when it has one, and the server's English when it does not.
    ///
    /// English keeps the server's words because they can say more than a
    /// fixed sentence: the model's reason for declining a photo, how many free
    /// scans were spent, which category a confidence reason is about. They are
    /// also exactly what every earlier build showed, so English loses nothing.
    /// Every other language gives up that detail for words it can read — the
    /// detail was in English there anyway. Server text that is only the
    /// parser's stand-in (`AppError.isPlaceholderDetail`) is never preferred
    /// to a translation.
    static func text(server: String, translated: String?,
                     inEnglish: Bool = appIsInEnglish) -> String {
        guard let translated else { return server }
        if inEnglish && !AppError.isPlaceholderDetail(server) { return server }
        return translated
    }
}
