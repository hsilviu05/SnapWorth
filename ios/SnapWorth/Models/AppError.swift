import Foundation

enum AppError: LocalizedError, Equatable {
    case network
    case timeout
    case rateLimit
    case serverUnavailable
    case imageEncodingFailed
    case purchaseCancelled
    case purchaseFailed(String)
    case persistence
    case unknown(String)

    var errorDescription: String? {
        switch self {
        case .network:
            return "No internet connection. Check your network and try again."
        case .timeout:
            return "The request timed out. Please try again."
        case .rateLimit:
            return "You've hit the scan limit. Try again in an hour."
        case .serverUnavailable:
            return "Our AI is temporarily unavailable. Please try again in a moment."
        case .imageEncodingFailed:
            return "Could not process the photo. Please try a different image."
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

    static func from(_ error: Error) -> AppError {
        if let appErr = error as? AppError { return appErr }

        if let scanErr = error as? ScanAPIError {
            switch scanErr {
            case .imageEncodingFailed:
                return .imageEncodingFailed
            case .serverError(let code, _):
                switch code {
                case 429:        return .rateLimit
                case 502, 503:   return .serverUnavailable
                case 500:        return .unknown("Server error \(code)")
                default:         return .unknown("Server error \(code)")
                }
            }
        }

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
        if msg.contains("429") || msg.contains("rate limit")       { return .rateLimit }
        if msg.contains("network") || msg.contains("offline")      { return .network }
        if msg.contains("timeout") || msg.contains("timed out")    { return .timeout }
        if msg.contains("502") || msg.contains("503")              { return .serverUnavailable }

        return .unknown(error.localizedDescription)
    }

    static func == (lhs: AppError, rhs: AppError) -> Bool {
        switch (lhs, rhs) {
        case (.network, .network),
             (.timeout, .timeout),
             (.rateLimit, .rateLimit),
             (.serverUnavailable, .serverUnavailable),
             (.imageEncodingFailed, .imageEncodingFailed),
             (.purchaseCancelled, .purchaseCancelled),
             (.persistence, .persistence):
            return true
        case (.purchaseFailed(let a), .purchaseFailed(let b)): return a == b
        case (.unknown(let a), .unknown(let b)):               return a == b
        default: return false
        }
    }
}
