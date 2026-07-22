import Foundation

/// Protocol that all purchase service implementations conform to.
/// The app depends only on this protocol — swap implementations freely.
@MainActor
protocol PurchaseService: AnyObject {
    var isSubscribed: Bool { get }

    /// End date of an active free trial, when known — powers the optional
    /// "trial ends tomorrow" reminder. Nil when not in a trial.
    var trialEndDate: Date? { get }

    /// Initiates a purchase for the given product ID.
    /// Throws if the purchase fails or is cancelled.
    func purchase(productID: String) async throws

    /// Restores previously-completed purchases.
    func restorePurchases() async throws
}

extension PurchaseService {
    /// Default so implementations without trial tracking (e.g. mocks) are
    /// unaffected.
    var trialEndDate: Date? { nil }
}

enum PurchaseError: LocalizedError {
    case cancelled
    case failed(String)
    case notConfigured

    var errorDescription: String? {
        switch self {
        case .cancelled:        return "Purchase was cancelled."
        case .failed(let msg):  return "Purchase failed: \(msg)"
        case .notConfigured:    return "In-app purchases are not configured yet."
        }
    }
}
