import Foundation
import os.log
import StoreKit

/// Native StoreKit 2 purchase service. Fetches products directly from the App
/// Store by product identifier — no third-party dashboard or offerings config.
@MainActor
final class StoreKitPurchaseService: PurchaseService, ObservableObject {
    @Published private(set) var isSubscribed: Bool
    /// End of an active *free* introductory trial, when the user is in one.
    /// Nil during a paid introductory offer — see `refreshSubscriptionStatus`.
    @Published private(set) var trialEndDate: Date?
    /// Localised pricing straight from StoreKit, keyed by product ID.
    @Published private(set) var pricing: [String: PlanPricing] = [:]
    @Published private(set) var isPricingLoaded = false
    @Published private(set) var pricingFailed = false

    private nonisolated static let cacheKey = "snapworth_is_subscribed"

    /// The last known entitlement, readable without the service.
    ///
    /// `WidgetDataStore.writeHaul` stamps this into the widget blob, and it
    /// runs from a repository write that holds no purchase service — the same
    /// way it reads `FreeScanCounter.remaining` and `ScanStreak.current()`.
    /// `nonisolated` because `UserDefaults` is thread-safe and the widget
    /// write is not always on the main actor.
    nonisolated static var cachedIsSubscribed: Bool {
        UserDefaults.standard.bool(forKey: cacheKey)
    }

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

    func purchase(productID: String) async throws -> PurchaseOutcome {
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
            Analytics.shared.track(.purchaseCompleted(productID: transaction.productID,
                                                      isFirst: ScanTally.isFirstRun()))
            return .completed
        case .userCancelled:
            Analytics.shared.track(.purchaseFailed(productID: productID, reason: "cancelled"))
            throw PurchaseError.cancelled
        case .pending:
            // Deferred (e.g. Ask to Buy / SCA). Not a failure — leave state as-is.
            // The transaction listener finalizes it once approved. Reported as
            // its own outcome so the paywall does not dismiss on it.
            return .pending
        @unknown default:
            Analytics.shared.track(.purchaseFailed(productID: productID, reason: "unknown"))
            throw PurchaseError.failed(String(localized: "This purchase could not be completed."))
        }
    }

    /// The restore in flight, so a second tap joins it instead of starting a
    /// second `AppStore.sync()`.
    ///
    /// `sync()` can take seconds and can put a system sign-in sheet on screen.
    /// Nothing in the app disabled the Settings row while it ran, so the
    /// natural response to a slow network — tap it again — ran two overlapping
    /// syncs and potentially two sign-in prompts, with whichever finished last
    /// overwriting the message. Now the second caller awaits the first and both
    /// report the same outcome.
    private var restoreTask: Task<Void, Error>?

    func restorePurchases() async throws {
        if let restoreTask { return try await restoreTask.value }
        let task = Task { @MainActor in try await self.performRestore() }
        restoreTask = task
        defer { restoreTask = nil }
        try await task.value
    }

    private func performRestore() async throws {
        do {
            try await AppStore.sync()
        } catch StoreKitError.userCancelled {
            // Dismissing the App Store sign-in sheet is a decision, not a
            // failure, and it was being reported as one: the raw StoreKit
            // string went straight through `PurchaseError.failed` to
            // `AppError.purchaseFailed`, whose `errorDescription` returns the
            // message verbatim, and the paywall rendered it in red. The
            // purchase path has always distinguished the two (`.userCancelled`
            // → `PurchaseError.cancelled`, whose `AppError` maps to a nil
            // description so nothing is shown); restore simply never did.
            throw PurchaseError.cancelled
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
            throw PurchaseError.failed(String(localized: "This subscription is currently unavailable. Please try again."))
        }
        return product
    }

    func reloadProducts() async {
        await loadProducts()
    }

    /// The product fetch in flight, so overlapping callers share one result.
    ///
    /// `loadProducts` writes `pricingFailed` unconditionally on resume, and two
    /// callers overlap by construction: `init` starts one, and the paywall's
    /// `.task` starts another via `reloadProducts()` whenever pricing has not
    /// arrived — which is exactly the window in which the init fetch is still
    /// running. Last writer won, whichever result was better: a cold launch on
    /// flaky cellular could show correct, purchasable price cards with
    /// "Couldn't load every plan. Try again" above them, and the mirror case —
    /// a late success clearing a real failure while the cards still read "—" —
    /// is worse. Joining the fetch in flight removes the race rather than
    /// ordering it.
    private var loadTask: Task<Void, Never>?

    private func loadProducts() async {
        if let loadTask { return await loadTask.value }
        let task = Task { @MainActor in await self.performLoad() }
        loadTask = task
        defer { loadTask = nil }
        await task.value
    }

    private func performLoad() async {
        // `isPricingLoaded` used to be set unconditionally, so a failed fetch
        // looked exactly like a finished one: the redaction lifted, the price
        // cards read "—" forever, and the CTA sat there inert with nothing
        // said. The flag now means "we have prices", and `pricingFailed`
        // carries the other case so the paywall can offer a retry.
        defer { isPricingLoaded = !products.isEmpty }
        guard let fetched = try? await Product.products(for: productIDs),
              !fetched.isEmpty else {
            pricingFailed = true
            return
        }
        products = fetched
        // Eligibility, asked of StoreKit rather than assumed.
        //
        // `introductoryOffer` describes the offer *configured on the product*
        // in App Store Connect. It says nothing about the customer in front of
        // you, and Apple grants one introductory offer per subscription
        // *group*, once. So anyone who has already taken the 3-day trial —
        // then cancelled, or simply lapsed — was still being shown "Try
        // SnapWorth free for 3 days" and a button reading "Start Free Trial",
        // and Apple's sheet then charged them $39.99 today with no trial.
        //
        // Unknown counts as ineligible. Dropping an offer costs a line of
        // value framing; promising free and billing immediately is a pricing
        // claim made at the exact moment someone decides whether to pay.
        var eligibility: [String: Bool] = [:]
        for product in fetched {
            guard let subscription = product.subscription else {
                eligibility[product.id] = false
                continue
            }
            // Per group, so both plans get the same answer — asked per product
            // because that is where StoreKit hangs it, not because they differ.
            eligibility[product.id] = await subscription.isEligibleForIntroOffer
        }
        pricing = Self.buildPricing(from: fetched, eligibility: eligibility)
        // A *partial* fetch used to land here as an unqualified success. If the
        // missing product was the yearly plan — which the paywall selects by
        // default — the user got "Loading plans…", a disabled CTA, and no
        // retry, because the retry is gated on this flag. Anything short of
        // every plan is a failure now; whatever did arrive is still kept, so
        // the paywall can offer the plan it does have.
        pricingFailed = fetched.count < productIDs.count
    }

    // MARK: - Pricing

    /// Derives display strings from StoreKit rather than hardcoding them.
    ///
    /// `displayPrice` is already localised to the user's storefront currency and
    /// number format. Everything derived (per-week, savings) is computed from
    /// `product.price`, a `Decimal`, so there is no float drift in money math.
    /// `eligibility` maps product ID to whether *this customer* may take the
    /// product's introductory offer. A product missing from the map is treated
    /// as ineligible: the offer is only advertised when StoreKit has said yes.
    static func buildPricing(from products: [Product],
                             eligibility: [String: Bool]) -> [String: PlanPricing] {
        let monthly = products.first { $0.id == Config.monthlyProductID }
        var result: [String: PlanPricing] = [:]

        for product in products {
            let isYearly = product.id == Config.yearlyProductID
            result[product.id] = PlanPricing(
                productID: product.id,
                displayPrice: product.displayPrice,
                displayPricePerWeek: isYearly ? weeklyPrice(for: product) : nil,
                introductoryOffer: introOffer(
                    for: product,
                    isEligible: isOfferEligible(product.id, in: eligibility)),
                savingsPercent: isYearly ? savings(yearly: product, monthly: monthly) : nil
            )
        }
        return result
    }

    /// Whether to advertise a product's introductory offer.
    ///
    /// Fails closed: a product StoreKit gave no answer for is ineligible.
    /// A lookup defaulting the other way would advertise a free trial on every
    /// path where the eligibility check did not run — which is exactly the
    /// state the app was in before it ran at all.
    /// `nonisolated` because it is a dictionary lookup — and because the whole
    /// point of pulling it out was so a test could assert the fail-closed
    /// direction without standing up the service.
    nonisolated static func isOfferEligible(_ productID: String,
                                            in eligibility: [String: Bool]) -> Bool {
        eligibility[productID] ?? false
    }

    /// Formats `price ÷ 52` in the product's own currency.
    private static func weeklyPrice(for product: Product) -> String? {
        let weeks = Decimal(52)
        guard weeks > 0 else { return nil }
        let perWeek = product.price / weeks
        return product.priceFormatStyle.format(perWeek)
    }

    /// The introductory offer this customer would actually get, as data.
    ///
    /// Returns nil when none is configured — so the paywall cannot advertise an
    /// offer App Store Connect does not grant — when the payment mode is one we
    /// have no copy for, and when the customer is not eligible. Dropping an
    /// offer costs a line of value framing; describing it wrongly is a pricing
    /// claim made at the moment a user decides whether to pay.
    ///
    /// The eligibility check is the third axis of the same mistake. First the
    /// paywall read "an offer exists" as "a free trial exists", which is wrong
    /// for the two paid payment modes. Now: "an offer exists" is not "this
    /// person gets it" either.
    static func introOffer(for product: Product, isEligible: Bool) -> IntroOffer? {
        guard isEligible else { return nil }
        guard let offer = product.subscription?.introductoryOffer else { return nil }

        let unit: String
        switch offer.period.unit {
        case .day:   unit = "day"
        case .week:  unit = "week"
        case .month: unit = "month"
        case .year:  unit = "year"
        @unknown default: return nil
        }

        let kind: IntroOffer.Kind
        switch offer.paymentMode {
        case .freeTrial:   kind = .freeTrial
        case .payUpFront:  kind = .payUpFront
        case .payAsYouGo:  kind = .payAsYouGo
        default: return nil
        }

        return IntroOffer(
            kind: kind,
            // A free trial has no price to show, and `displayPrice` on one is
            // the storefront's zero ("$0.00") — a figure this must never put
            // next to the word "free".
            displayPrice: kind == .freeTrial ? "" : offer.displayPrice,
            unitCount: offer.period.value,
            unit: unit,
            periodCount: offer.periodCount
        )
    }

    /// Percentage the yearly plan saves against 12× monthly.
    private static func savings(yearly: Product, monthly: Product?) -> Int? {
        guard let monthly else { return nil }
        return savingsPercent(yearly: yearly.price, monthly: monthly.price)
    }

    /// The whole percentage `yearly` saves against twelve `monthly`s, or nil
    /// when it saves nothing.
    ///
    /// Rounded before it becomes an `Int`. The quotient carries every digit
    /// `Decimal` can hold — 39.99 against 12 × 4.99 is 33.2164328657… — and
    /// `NSDecimalNumber.intValue` returns 0 for a value that long rather than
    /// truncating it. So this was nil in every storefront, the yearly card
    /// fell back to "BEST VALUE", and the saving the website quotes never
    /// appeared on the plan the paywall preselects. Rounded *down*, so the
    /// badge never claims a point the plan does not save.
    ///
    /// `nonisolated` and on plain `Decimal`s so a test can check the maths
    /// without a StoreKit `Product`.
    nonisolated static func savingsPercent(yearly: Decimal, monthly: Decimal) -> Int? {
        let twelveMonths = monthly * 12
        guard twelveMonths > 0, yearly < twelveMonths else { return nil }
        var ratio = (twelveMonths - yearly) / twelveMonths * 100
        var whole = Decimal()
        NSDecimalRound(&whole, &ratio, 0, .down)
        let percent = NSDecimalNumber(decimal: whole).intValue
        return percent > 0 ? percent : nil
    }

    /// `PurchaseService` conformance — see the protocol for why an expiry needs
    /// a poll rather than an update stream.
    func refreshEntitlements() async {
        await refreshSubscriptionStatus()
    }

    /// `PurchaseService` conformance. Awaited end to end, unlike the routine
    /// sync — see the protocol, and `confirmingSubscription` for the caller.
    func resyncEntitlement() async -> EntitlementResync {
        // StoreKit first: the local state may itself be stale, and a lapse it
        // now reports means the 402 was right.
        guard let jws = await refreshSubscriptionStatus(serverSync: false) else {
            return .notSubscribed
        }
        guard Config.useAttestation else { return .failed(reason: "attestation_off") }
        do {
            let tier = try await AttestationService.shared.submitEntitlement(signedTransaction: jws)
            // A 200 that says "free" is the server verifying the transaction
            // and finding it expired — StoreKit and the server disagreeing
            // about the date, as they do during an App Store billing grace
            // period the server does not honour.
            return tier == "pro" ? .confirmed : .failed(reason: "server_says_free")
        } catch {
            return .failed(reason: EntitlementSyncFailure.reason(for: error))
        }
    }

    /// Re-reads `Transaction.currentEntitlements` and publishes the result.
    ///
    /// - Parameter serverSync: whether to also push the active transaction to
    ///   the server in the background. `resyncEntitlement` passes false
    ///   because it sends it itself and waits for the answer.
    /// - Returns: the active subscription's signed transaction, or nil.
    @discardableResult
    private func refreshSubscriptionStatus(serverSync: Bool = true) async -> String? {
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
                // `offerType == .introductory` says an introductory offer is
                // running, not that it is free — a paid intro offer looks the
                // same here, and would have scheduled "Your SnapWorth trial
                // ends tomorrow" for someone who paid. The `Transaction` does
                // not carry the payment mode, so ask the product. If the
                // product fetch failed we have no answer and stay silent: the
                // next status refresh retries, and a missed courtesy reminder
                // is cheaper than telling a paying subscriber they are on a
                // trial.
                let isFreeTrial = products
                    .first { $0.id == transaction.productID }
                    .flatMap { $0.subscription?.introductoryOffer }
                    .map { $0.paymentMode == .freeTrial } ?? false
                if transaction.offerType == .introductory, isFreeTrial,
                   let exp = transaction.expirationDate {
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
        if let activeJWS, serverSync {
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
        return activeJWS
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
    ///
    /// Counted as well as logged. The system log was the only record, so a
    /// subscriber the server never heard about was invisible to the operator
    /// until they wrote in — if they did.
    private func syncEntitlementToServer(_ jws: String) async {
        guard Config.useAttestation else { return }
        do {
            let tier = try await AttestationService.shared.submitEntitlement(signedTransaction: jws)
            if tier != "pro" {
                Analytics.shared.track(.entitlementSyncFailed(reason: "server_says_free"))
            }
        } catch {
            Logger(subsystem: "eu.snapworth.app", category: "purchases")
                .error("entitlement sync failed: \(error.localizedDescription, privacy: .public)")
            Analytics.shared.track(.entitlementSyncFailed(
                reason: EntitlementSyncFailure.reason(for: error)))
        }
    }

    private nonisolated func checkVerified<T>(_ result: VerificationResult<T>) throws -> T {
        switch result {
        case .unverified:
            throw PurchaseError.failed(String(localized: "Your purchase could not be verified."))
        case .verified(let safe):
            return safe
        }
    }

    private func setSubscribed(_ value: Bool) {
        let changed = value != isSubscribed
        // Persist before publishing: anything that reacts to `isSubscribed`
        // reads the cache through `cachedIsSubscribed`, so the store has to be
        // the newer of the two, never the older.
        UserDefaults.standard.set(value, forKey: Self.cacheKey)
        isSubscribed = value
        guard changed else { return }
        // `/trends` returns a different payload *shape* per tier, and the
        // client holds it for thirty minutes. The tier key on that cache
        // covers a user who reaches the card again, and this covers every
        // other transition — a restore, an expiry, a server-side revoke —
        // without waiting the TTL out.
        Task { await TrendsAPIClient.shared.invalidate() }
    }
}

/// The fixed buckets `entitlement_sync_failed` reports, so the event carries
/// no error text — only which of a handful of things went wrong.
enum EntitlementSyncFailure {
    static func reason(for error: Error) -> String {
        // Before `AppError.from`, which reads a rejection as `sessionExpired`:
        // on this route it is the server refusing the *transaction*.
        if let attestation = error as? AttestationError, case .serverRejected = attestation {
            return "rejected"
        }
        switch AppError.from(error) {
        case .network:                                  return "network"
        case .timeout:                                  return "timeout"
        case .rateLimit:                                return "rate_limited"
        case .sessionExpired:                           return "attestation"
        case .verificationUnavailable, .serverUnavailable: return "unavailable"
        default:                                        return "unknown"
        }
    }
}
