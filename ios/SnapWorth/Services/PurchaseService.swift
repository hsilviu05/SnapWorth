import Foundation

/// What a purchase attempt actually did.
///
/// `purchase` used to return Void, so "StoreKit is waiting for a parent to
/// approve this" and "the subscription is live" were the same answer. The
/// paywall dismissed on both, leaving an Ask to Buy or SCA user with no
/// subscription, no explanation, and the same paywall on their next scan.
enum PurchaseOutcome: Equatable {
    /// Verified, finished, entitlement refreshed.
    case completed
    /// Deferred — Ask to Buy, or a bank's SCA challenge. Nothing is wrong and
    /// nothing is owed yet; `Transaction.updates` finalises it if and when it
    /// is approved, which can be days later.
    case pending
}

/// Display-ready pricing for one subscription option, sourced from StoreKit.
///
/// Every string here comes from `Product`, so it is already in the user's
/// storefront currency with the correct locale formatting. Hardcoding these
/// showed a German user "$39.99" while Apple charged €44,99 — a refund
/// generator and an App Review risk under Guideline 2.3.
struct PlanPricing: Equatable, Sendable {
    let productID: String
    /// Localised total price, e.g. "$39.99" / "44,99 €".
    let displayPrice: String
    /// Localised price per week, for the yearly plan's value framing. Nil when
    /// StoreKit can't express the period.
    let displayPricePerWeek: String?
    /// Introductory offer duration, e.g. "3-day free trial". Nil when none.
    let introductoryOffer: String?
    /// Percentage saved against the monthly plan, when comparable.
    let savingsPercent: Int?

    /// Placeholder used only while StoreKit is still loading. Never shows a
    /// currency figure, so it cannot display a wrong price.
    static func loading(_ productID: String) -> PlanPricing {
        PlanPricing(productID: productID, displayPrice: "—",
                    displayPricePerWeek: nil, introductoryOffer: nil,
                    savingsPercent: nil)
    }
}

/// Protocol that all purchase service implementations conform to.
/// The app depends only on this protocol — swap implementations freely.
@MainActor
protocol PurchaseService: AnyObject {
    var isSubscribed: Bool { get }

    /// End date of an active free trial, when known — powers the optional
    /// "trial ends tomorrow" reminder. Nil when not in a trial.
    var trialEndDate: Date? { get }

    /// Localised pricing keyed by product ID. Empty until StoreKit responds.
    var pricing: [String: PlanPricing] { get }

    /// True once we actually have prices.
    ///
    /// It used to mean "a fetch finished, successfully or not", which made a
    /// failed load indistinguishable from a completed one: the redaction
    /// lifted onto price cards reading "—" and stayed there. Failure is now
    /// `pricingFailed`, and the two together give the paywall three states
    /// rather than two.
    var isPricingLoaded: Bool { get }

    /// A product fetch completed and returned nothing usable. The paywall
    /// shows this rather than an inert CTA over em-dashes.
    var pricingFailed: Bool { get }

    /// Retry a failed product fetch — surfaced behind the paywall's error state.
    func reloadProducts() async

    /// Initiates a purchase for the given product ID.
    /// Throws if the purchase fails or is cancelled.
    func purchase(productID: String) async throws -> PurchaseOutcome

    /// Restores previously-completed purchases.
    func restorePurchases() async throws

    /// Re-reads `Transaction.currentEntitlements`.
    ///
    /// `Transaction.updates` delivers renewals and revocations while the app is
    /// resident, but a subscription that simply *expires* produces no update —
    /// so a session that stayed open across the expiry date kept showing Pro
    /// chrome. The server still enforces (402), so this was stale UI rather
    /// than a leak, but it is the paywall the user never saw.
    func refreshEntitlements() async
}

extension PurchaseService {
    /// Default so implementations without trial tracking (e.g. mocks) are
    /// unaffected.
    var trialEndDate: Date? { nil }

    /// Defaults keep existing conformances source-compatible.
    var pricing: [String: PlanPricing] { [:] }
    var isPricingLoaded: Bool { true }
    var pricingFailed: Bool { false }
    func reloadProducts() async {}
    func refreshEntitlements() async {}
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
