import Foundation
import os.log
import StoreKit

/// Native StoreKit 2 purchase service. Fetches products directly from the App
/// Store by product identifier — no third-party dashboard or offerings config.
@MainActor
final class StoreKitPurchaseService: PurchaseService, ObservableObject {
    @Published private(set) var isSubscribed: Bool
    /// End of an active introductory free trial, when the user is in one.
    @Published private(set) var trialEndDate: Date?
    /// Localised pricing straight from StoreKit, keyed by product ID.
    @Published private(set) var pricing: [String: PlanPricing] = [:]
    @Published private(set) var isPricingLoaded = false

    private static let cacheKey = "snapworth_is_subscribed"
    private let productIDs = [Config.monthlyProductID, Config.yearlyProductID]

    private var products: [Product] = []
    private var updatesTask: Task<Void, Never>?

    init() {
        // Restore last known status instantly so subscribed users never see
        // the free-tier UI while the async entitlement check is in flight.
        self.isSubscribed = UserDefaults.standard.bool(forKey: Self.cacheKey)

        // Listen for transactions that happen outside an explicit purchase call
        // (renewals, Ask-to-Buy approvals, purchases made on another device).
        updatesTask = listenForTransactions()

        Task {
            await loadProducts()
            await refreshSubscriptionStatus()
        }
    }

    deinit {
        updatesTask?.cancel()
    }

    // MARK: - PurchaseService

    func purchase(productID: String) async throws {
        let product = try await product(for: productID)

        let result: Product.PurchaseResult
        do {
            result = try await product.purchase()
        } catch {
            Analytics.shared.track(.purchaseFailed(productID: productID, reason: "storekit_error"))
            throw PurchaseError.failed(error.localizedDescription)
        }

        switch result {
        case .success(let verification):
            let transaction: Transaction
            do {
                transaction = try checkVerified(verification)
            } catch {
                Analytics.shared.track(.purchaseFailed(productID: productID, reason: "unverified"))
                throw error
            }
            await transaction.finish()
            await refreshSubscriptionStatus()
            // Fires on the confirmed StoreKit transaction — never on the tap.
            Analytics.shared.track(.purchaseCompleted(productID: transaction.productID))
        case .userCancelled:
            Analytics.shared.track(.purchaseFailed(productID: productID, reason: "cancelled"))
            throw PurchaseError.cancelled
        case .pending:
            // Deferred (e.g. Ask to Buy / SCA). Not a failure — leave state as-is.
            // The transaction listener finalizes it once approved.
            break
        @unknown default:
            Analytics.shared.track(.purchaseFailed(productID: productID, reason: "unknown"))
            throw PurchaseError.failed("This purchase could not be completed.")
        }
    }

    func restorePurchases() async throws {
        do {
            try await AppStore.sync()
        } catch {
            throw PurchaseError.failed(error.localizedDescription)
        }
        await refreshSubscriptionStatus()
        Analytics.shared.track(.restoreCompleted)
    }

    // MARK: - Private

    /// Returns the cached `Product`, fetching on demand if the initial load
    /// hasn't finished (or previously failed) so a tap is never a dead end.
    private func product(for productID: String) async throws -> Product {
        if let cached = products.first(where: { $0.id == productID }) {
            return cached
        }
        await loadProducts()
        guard let product = products.first(where: { $0.id == productID }) else {
            throw PurchaseError.failed("This subscription is currently unavailable. Please try again.")
        }
        return product
    }

    func reloadProducts() async {
        await loadProducts()
    }

    private func loadProducts() async {
        defer { isPricingLoaded = true }
        if let fetched = try? await Product.products(for: productIDs), !fetched.isEmpty {
            products = fetched
            pricing = Self.buildPricing(from: fetched)
        }
    }

    // MARK: - Pricing

    /// Derives display strings from StoreKit rather than hardcoding them.
    ///
    /// `displayPrice` is already localised to the user's storefront currency and
    /// number format. Everything derived (per-week, savings) is computed from
    /// `product.price`, a `Decimal`, so there is no float drift in money math.
    static func buildPricing(from products: [Product]) -> [String: PlanPricing] {
        let monthly = products.first { $0.id == Config.monthlyProductID }
        var result: [String: PlanPricing] = [:]

        for product in products {
            let isYearly = product.id == Config.yearlyProductID
            result[product.id] = PlanPricing(
                productID: product.id,
                displayPrice: product.displayPrice,
                displayPricePerWeek: isYearly ? weeklyPrice(for: product) : nil,
                introductoryOffer: introductoryDescription(for: product),
                savingsPercent: isYearly ? savings(yearly: product, monthly: monthly) : nil
            )
        }
        return result
    }

    /// Formats `price ÷ 52` in the product's own currency.
    private static func weeklyPrice(for product: Product) -> String? {
        let weeks = Decimal(52)
        guard weeks > 0 else { return nil }
        let perWeek = product.price / weeks
        return product.priceFormatStyle.format(perWeek)
    }

    /// "3-day free trial" / "1 month free", from the product's real offer.
    /// Returns nil when no introductory offer is configured, so the paywall
    /// cannot advertise a trial that App Store Connect does not grant.
    private static func introductoryDescription(for product: Product) -> String? {
        guard let offer = product.subscription?.introductoryOffer else { return nil }
        let period = offer.period
        let unit: String
        switch period.unit {
        case .day:   unit = "day"
        case .week:  unit = "week"
        case .month: unit = "month"
        case .year:  unit = "year"
        @unknown default: return nil
        }
        let count = period.value
        let plural = count == 1 ? "" : "s"

        switch offer.paymentMode {
        case .freeTrial:
            // Attributive compound — "3-day free trial", never "3-days". The
            // paywall headline re-reads this to say "free for 3 days", so
            // pluralising here too produced "free for 3 dayss" in production.
            return "\(count)-\(unit) free trial"
        case .payAsYouGo, .payUpFront:
            return "\(offer.displayPrice) for \(count) \(unit)\(plural)"
        default:
            return nil
        }
    }

    /// Percentage the yearly plan saves against 12× monthly.
    private static func savings(yearly: Product, monthly: Product?) -> Int? {
        guard let monthly else { return nil }
        let twelveMonths = monthly.price * 12
        guard twelveMonths > 0, yearly.price < twelveMonths else { return nil }
        let ratio = (twelveMonths - yearly.price) / twelveMonths * 100
        let percent = NSDecimalNumber(decimal: ratio).intValue
        return percent > 0 ? percent : nil
    }

    private func refreshSubscriptionStatus() async {
        var active = false
        var trialEnd: Date?
        var activeJWS: String?
        for await result in Transaction.currentEntitlements {
            guard let transaction = try? checkVerified(result) else { continue }
            if productIDs.contains(transaction.productID), transaction.revocationDate == nil {
                active = true
                // Apple's own signature over this transaction. The server
                // re-verifies it against Apple's root CA, so entitlement is
                // never taken on the client's word.
                activeJWS = result.jwsRepresentation
                // The only introductory offer on these products is the free
                // trial, so an introductory entitlement means we're in it.
                if transaction.offerType == .introductory, let exp = transaction.expirationDate {
                    trialEnd = exp
                }
            }
        }
        setSubscribed(active)
        trialEndDate = trialEnd

        // Push proof of purchase to the backend so it can lift the free-scan
        // quota. Runs on every status refresh — purchase, restore, and the
        // transaction listener all funnel through here — which also makes it
        // self-healing if an earlier attempt failed offline.
        if let activeJWS {
            // Detached, not awaited: this sits inside the `purchase()` await
            // chain on the main actor, and attestation plus the POST can take
            // seconds (or block on a bad network). The local entitlement is
            // already active, and every later status refresh retries, so the
            // user must never wait on it to see their purchase complete.
            Task.detached { [weak self] in
                await self?.syncEntitlementToServer(activeJWS)
            }
        }
        // Keep the courtesy "trial ends tomorrow" reminder in sync — schedules
        // when in a trial, cancels the moment the state changes.
        await NotificationManager.shared.syncTrialReminder(endDate: trialEnd)
    }

    private func listenForTransactions() -> Task<Void, Never> {
        Task { [weak self] in
            for await result in Transaction.updates {
                guard let self else { continue }
                guard let transaction = try? self.checkVerified(result) else { continue }
                await transaction.finish()
                await self.refreshSubscriptionStatus()
            }
        }
    }

    /// Best-effort upload of the signed transaction.
    ///
    /// Deliberately non-throwing: a network failure here must not make a
    /// successful purchase look failed to the user. The local entitlement is
    /// already active, and the next status refresh retries.
    private func syncEntitlementToServer(_ jws: String) async {
        guard Config.useAttestation else { return }
        do {
            try await AttestationService.shared.submitEntitlement(signedTransaction: jws)
        } catch {
            Logger(subsystem: "eu.snapworth.app", category: "purchases")
                .error("entitlement sync failed: \(error.localizedDescription, privacy: .public)")
        }
    }

    private nonisolated func checkVerified<T>(_ result: VerificationResult<T>) throws -> T {
        switch result {
        case .unverified:
            throw PurchaseError.failed("Your purchase could not be verified.")
        case .verified(let safe):
            return safe
        }
    }

    private func setSubscribed(_ value: Bool) {
        isSubscribed = value
        UserDefaults.standard.set(value, forKey: Self.cacheKey)
    }
}
