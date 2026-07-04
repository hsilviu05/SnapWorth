import Foundation

/// Protocol that all purchase service implementations conform to.
/// The app depends only on this protocol — swap implementations freely.
@MainActor
protocol PurchaseService: AnyObject {
    var isSubscribed: Bool { get }

    /// Initiates a purchase for the given product ID.
    /// Throws if the purchase fails or is cancelled.
    func purchase(productID: String) async throws

    /// Restores previously-completed purchases.
    func restorePurchases() async throws
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
