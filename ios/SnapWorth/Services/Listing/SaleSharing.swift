import Foundation
import StoreKit

// ── Shared sale outcomes (#224) ──────────────────────────────────────────────
//
// A user who turns on "Share sale prices to improve estimates" sends, for each
// sold flip, the estimate they saw at scan time and what it sold for, to
// `POST /outcomes`. Off by default; asked once, the first time a sold price is
// saved; deletable from Settings. Never sent: the photo, the item's name,
// notes, the price paid, or any device identifier — `SaleOutcomePayload` has
// no field for any of them, and the server refuses unknown fields.

/// The currencies a sale can be recorded in: the server's allowlist
/// (`outcomes.CURRENCIES`), the markets the app's marketplaces serve.
enum SaleCurrency {
    static let all = ["USD", "EUR", "GBP", "RON", "PLN", "CHF", "CAD", "AUD",
                      "CNY", "MXN", "SEK", "DKK", "NOK", "CZK", "HUF", "BGN"]

    /// The phone's region currency when it is one of `all`, else US dollars.
    static func regionDefault(locale: Locale = .current) -> String {
        if let code = locale.currency?.identifier, all.contains(code) { return code }
        return "USD"
    }

    /// The currency of an amount saved with none: US dollars.
    ///
    /// Up to build 22 every ledger field sat beside a fixed "$" and every
    /// figure was printed as dollars, so that is what those amounts were typed
    /// as. Reading them as the region default (#224) relabelled a Romanian
    /// user's "$20 paid" as "20 lei" on the first launch of 23, with nothing
    /// changed but the symbol.
    static let unstated = "USD"

    /// The currency a flip's amounts are in: the one stored with them, else
    /// dollars when it has amounts (`unstated`), else the region default —
    /// the currency its empty fields offer, which `pin` stores as soon as an
    /// amount or a sale is entered.
    ///
    /// Read-time rather than a launch migration that writes "USD" into old
    /// rows: nothing in the store changes, nothing has to run before the
    /// first read (the widget seed, a sale share), and a flip saved before
    /// currencies still exports as `currency_assumed`, which the gold-set
    /// intake tags for review, instead of as a currency somebody chose.
    static func of(_ result: ScanResult, locale: Locale = .current) -> String {
        if let code = result.saleCurrency, all.contains(code) { return code }
        let hasAmount = result.paidPrice != nil || result.soldPrice != nil
            || result.feesEstimate != nil
        return hasAmount ? unstated : regionDefault(locale: locale)
    }

    /// Stores the currency a flip shows, if it has none yet. Called before an
    /// amount is written and when a find is marked sold: the moment the
    /// region default beside the field becomes the currency of a figure.
    ///
    /// Left nil, the amounts followed the phone's region on every read, so a
    /// user who moved from Germany to the UK saw every flip relabelled £ with
    /// the same numbers, and a shared sale was re-sent under the new code.
    static func pin(_ result: ScanResult, locale: Locale = .current) {
        guard result.saleCurrency == nil else { return }
        result.saleCurrency = of(result, locale: locale)
    }

    /// "$", "lei", "€" — the symbol the ledger shows beside an amount.
    static func symbol(_ code: String, locale: Locale = .current) -> String {
        formatter(code, locale: locale).currencySymbol ?? code
    }

    // ── Printing a flip's money ──────────────────────────────────────────────
    //
    // Every amount a user typed into the ledger is printed through these, in
    // the currency it was typed in. Totals and lists printed them through
    // `NumberFormatter.snapCurrency`, which is US dollars: the result sheet
    // read "+80 lei" and My Flips "+$80" for the same sale (AUDIT-2026-10-07,
    // M1). `snapCurrency` stays for estimates, which are in dollars.

    /// An amount as `locale` writes `code`: "$80", "80 lei", and to the cent
    /// when it is not a whole number, "$10.40".
    ///
    /// This dropped the fraction always, so a sale 40 cents under its cost
    /// read "−$0" under a loss arrow, and 49 cents over it "+$0" — the defect
    /// `snapCurrencyCents` fixed in Thrift Flip. Rounded to the cent first, so
    /// the Double-to-Decimal residue in 10.4 − 10 is not a fraction.
    static func format(_ amount: Decimal, code: String, locale: Locale = .current) -> String {
        let cents = rounded(amount, scale: 2)
        let whole = rounded(cents, scale: 0) == cents
        return formatter(code, locale: locale, whole: whole)
            .string(from: NSDecimalNumber(decimal: cents)) ?? "\(amount) \(code)"
    }

    /// `format` in whole units, for the share cards: they print the paid price
    /// beside a whole-dollar estimate and divide one by the other, as printed
    /// (`ShareCardView.findBadge`).
    static func formatWhole(_ amount: Decimal, code: String, locale: Locale = .current) -> String {
        formatter(code, locale: locale, whole: true)
            .string(from: NSDecimalNumber(decimal: amount)) ?? "\(amount) \(code)"
    }

    /// Signed, with U+2212 for a loss as `FlipsViewModel` always printed it:
    /// "+80 lei", "−$12". The sign is the printed figure's, so nothing that
    /// rounds to zero reads as a loss.
    static func signed(_ amount: Decimal, code: String, locale: Locale = .current) -> String {
        let money = format(abs(amount), code: code, locale: locale)
        return rounded(amount, scale: 2) < 0 ? "−\(money)" : "+\(money)"
    }

    /// A total in one currency or several, never added across them: "80 lei",
    /// "80 lei · $12". Empty is zero in the phone's currency.
    static func format(_ amounts: LedgerMath.Amounts, locale: Locale = .current) -> String {
        joined(amounts, locale: locale) { format($0, code: $1, locale: locale) }
    }

    /// `format(_:locale:)`, signed: "+80 lei · −$12".
    static func signed(_ amounts: LedgerMath.Amounts, locale: Locale = .current) -> String {
        joined(amounts, locale: locale) { signed($0, code: $1, locale: locale) }
    }

    /// The currencies in `amounts`, the phone's own first and the rest by
    /// code, so a mixed total always reads in the same order.
    static func ordered(_ amounts: LedgerMath.Amounts, locale: Locale = .current) -> [String] {
        let home = regionDefault(locale: locale)
        return amounts.byCurrency.keys.sorted { a, b in
            if (a == home) != (b == home) { return a == home }
            return a < b
        }
    }

    private static func joined(_ amounts: LedgerMath.Amounts, locale: Locale,
                               _ each: (Decimal, String) -> String) -> String {
        guard !amounts.isEmpty else { return each(0, regionDefault(locale: locale)) }
        return ordered(amounts, locale: locale)
            .map { each(amounts.amount(in: $0), $0) }
            .joined(separator: " · ")
    }

    private static func formatter(_ code: String, locale: Locale,
                                  whole: Bool = true) -> NumberFormatter {
        let f = NumberFormatter()
        f.numberStyle = .currency
        f.locale = code == "USD" ? dollarLocale(locale) : locale
        f.currencyCode = code
        if whole {
            f.maximumFractionDigits = 0
        } else {
            f.minimumFractionDigits = 2
            f.maximumFractionDigits = 2
        }
        return f
    }

    /// The locale US dollars are written in: en_US, "$80", as the estimate
    /// beside them (`snapCurrency`) and every ledger figure up to build 22
    /// were. The phone's own locale wrote "80 USD" in Romania, "80 $" in
    /// Germany and "US$80" in the UK. Except where the phone's currency is a
    /// "$" too — Canada, Australia, Mexico — and "US$80" is what tells the
    /// two apart.
    private static func dollarLocale(_ locale: Locale) -> Locale {
        let home = regionDefault(locale: locale)
        let us = Locale(identifier: "en_US")
        guard home != "USD" else { return us }
        let f = NumberFormatter()
        f.numberStyle = .currency
        f.locale = locale
        f.currencyCode = home
        return f.currencySymbol == "$" ? locale : us
    }

    private static func rounded(_ value: Decimal, scale: Int) -> Decimal {
        var input = value
        var result = Decimal()
        NSDecimalRound(&result, &input, scale, .plain)
        return result
    }
}

/// One shared sale, exactly the server's `Outcome`. Optional fields that are
/// nil are left out of the JSON, which the server reads as absent.
struct SaleOutcomePayload: Encodable, Equatable {
    let contributionID: String
    let contributorToken: String
    let build: String
    let storefront: String?
    let scanDay: String
    let promptVersion: String
    let valuationSource: String
    let category: String
    let brandIdentified: Bool
    let conditionGrade: String?
    let conditionChosen: String?
    let confidenceScore: Int?
    let confidenceBand: String
    let estimateLow: Double
    let estimateHigh: Double
    let likely: Double?
    let expected: Double?
    let soldPrice: Double
    let currency: String
    let soldDay: String
    let daysListedToSold: Int?

    enum CodingKeys: String, CodingKey, CaseIterable {
        case contributionID = "contribution_id"
        case contributorToken = "contributor_token"
        case build, storefront
        case scanDay = "scan_day"
        case promptVersion = "prompt_version"
        case valuationSource = "valuation_source"
        case category
        case brandIdentified = "brand_identified"
        case conditionGrade = "condition_grade"
        case conditionChosen = "condition_chosen"
        case confidenceScore = "confidence_score"
        case confidenceBand = "confidence_band"
        case estimateLow = "estimate_low"
        case estimateHigh = "estimate_high"
        case likely, expected
        case soldPrice = "sold_price"
        case currency
        case soldDay = "sold_day"
        case daysListedToSold = "days_listed_to_sold"
    }

    /// The payload for a sold flip, or nil when it is not sold with a price.
    static func make(for result: ScanResult, contributionID: String, token: String,
                     build: String, storefront: String?,
                     calendar: Calendar = .current) -> SaleOutcomePayload? {
        guard result.status == .sold, let sold = result.soldPrice, sold > 0 else { return nil }
        let detail = result.valuationDetail
        let utc = DateFormatter.dayStamp(TimeZone(identifier: "UTC")!)
        let local = DateFormatter.dayStamp(calendar.timeZone)
        let soldDate = result.soldDate ?? Date()
        var listedDays: Int?
        if let listed = result.listedDate {
            listedDays = max(0, calendar.dateComponents(
                [.day], from: calendar.startOfDay(for: listed),
                to: calendar.startOfDay(for: soldDate)).day ?? 0)
        }
        return SaleOutcomePayload(
            contributionID: contributionID, contributorToken: token, build: build,
            storefront: storefront,
            scanDay: utc.string(from: result.timestamp),
            promptVersion: detail?.promptVersion ?? "unknown",
            valuationSource: result.valuationSource.rawValue,
            category: String(result.category.prefix(40)),
            brandIdentified: identifiesBrand(result),
            conditionGrade: detail?.conditionGrade,
            conditionChosen: result.conditionRaw,
            confidenceScore: detail?.confidenceScore,
            confidenceBand: String(result.confidence.prefix(10)),
            estimateLow: result.valueLow, estimateHigh: result.valueHigh,
            likely: detail?.likely, expected: detail?.expected,
            soldPrice: sold, currency: SaleCurrency.of(result),
            soldDay: local.string(from: soldDate),
            daysListedToSold: listedDays)
    }

    /// Whether a brand was identified — a yes or no, never the brand's text.
    /// The server's own reason code when the scan carried one, else whether
    /// the brand field names one.
    static func identifiesBrand(_ result: ScanResult) -> Bool {
        if let codes = result.valuationDetail?.confidenceReasonCodes {
            if codes.contains(ConfidenceReason.brandIdentified.rawValue) { return true }
            if codes.contains(ConfidenceReason.brandUnidentified.rawValue) { return false }
        }
        let brand = result.brand.trimmingCharacters(in: .whitespaces).lowercased()
        return !["", "unknown", "generic", "unbranded", "none", "n/a"].contains(brand)
    }
}

private extension DateFormatter {
    static func dayStamp(_ zone: TimeZone) -> DateFormatter {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.calendar = Calendar(identifier: .gregorian)
        f.timeZone = zone
        f.dateFormat = "yyyy-MM-dd"
        return f
    }
}

/// What the sharing service asks the server to do.
enum SaleOutcomeRequest: Equatable {
    case share(SaleOutcomePayload)
    /// One record (un-marking a sale), or every record of this install.
    case delete(contributionID: String?, token: String)
}

/// The consent, the per-install token, and the one call made when a result
/// sheet closes.
@MainActor
final class SaleSharing {
    static let shared = SaleSharing()

    static let enabledKey = "shareSalePrices.enabled"
    static let askedKey = "shareSalePrices.asked"
    static let tokenKey = "shareSalePrices.contributorToken"
    static let sentKey = "shareSalePrices.sentFingerprints"
    static let withdrawnKey = "shareSalePrices.withdrawnIDs"
    static let pendingKey = "shareSalePrices.pendingDeletions"
    static let pendingAllKey = "shareSalePrices.pendingDeleteAll"

    var defaults: UserDefaults = .standard
    /// The network call; `SaleOutcomesClient` in the app, a fake in tests.
    var send: (SaleOutcomeRequest) async throws -> Void = { try await SaleOutcomesClient.shared.send($0) }
    var storefront: () async -> String? = { await Storefront.current?.countryCode }
    var build: String = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "0"

    init() {}

    var isEnabled: Bool {
        get { defaults.bool(forKey: Self.enabledKey) }
        set { defaults.set(newValue, forKey: Self.enabledKey) }
    }

    var hasAsked: Bool {
        get { defaults.bool(forKey: Self.askedKey) }
        set { defaults.set(newValue, forKey: Self.askedKey) }
    }

    /// Whether this install has a token, so may have records to delete.
    /// Settings reads the same key through `@AppStorage(tokenKey)`, so its
    /// row follows a first share and a delete.
    var hasShared: Bool { defaults.string(forKey: Self.tokenKey) != nil }

    /// A random per-install token, used only to delete this install's records.
    var contributorToken: String {
        if let token = defaults.string(forKey: Self.tokenKey) { return token }
        var bytes = [UInt8](repeating: 0, count: 32)
        _ = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        let token = Data(bytes).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
        defaults.set(token, forKey: Self.tokenKey)
        return token
    }

    /// Show the one-time card: a sold price, never asked, not already on.
    func needsConsent(for result: ScanResult) -> Bool {
        !isEnabled && !hasAsked && result.status == .sold && (result.soldPrice ?? 0) > 0
    }

    /// Flips with a sync running, and those closed again while it ran.
    private var syncing: Set<ObjectIdentifier> = []
    private var resync: Set<ObjectIdentifier> = []

    /// When a result sheet closes. On: a sold flip with a price is sent (an
    /// edit replaces the same record, an unchanged one is not re-sent). On or
    /// off: a flip that was shared and is no longer sold has its record
    /// deleted, because deleting is not sharing. A failure is left for the
    /// next close; nothing here interrupts the user.
    ///
    /// One flip runs one sync at a time. Two closes in quick succession used
    /// to overlap: each minted its own id, so one sale became two records,
    /// and an edit could land before the older send it followed and then be
    /// overwritten by it. A close during a sync now runs once more after it,
    /// with the flip as it is then.
    func sync(_ result: ScanResult) async {
        let key = ObjectIdentifier(result)
        guard !syncing.contains(key) else {
            resync.insert(key)
            return
        }
        syncing.insert(key)
        defer { syncing.remove(key) }
        repeat {
            resync.remove(key)
            await syncOnce(result)
        } while resync.contains(key)
    }

    private func syncOnce(_ result: ScanResult) async {
        await flushDeletions()
        // Deleted with "Delete my shared sales": never sent again.
        if let id = result.outcomeID, withdrawn.contains(id) { return }
        // The same test as `SaleOutcomePayload.make`'s nil.
        guard result.status == .sold, (result.soldPrice ?? 0) > 0 else {
            // Un-marking deletes the record whether or not sharing is still
            // on, as both policies say: turning sharing off stops sending, not
            // deleting.
            guard let id = result.outcomeID else { return }
            guard hasShared else {
                result.outcomeID = nil      // no token, so nothing it could delete
                return
            }
            do {
                try await send(.delete(contributionID: id, token: contributorToken))
                result.outcomeID = nil
                sent[id] = nil
            } catch {}
            return
        }
        guard isEnabled else { return }
        // Stored before the first send, so a send that fails, or lands with
        // its response lost, is retried under the same id and the server
        // replaces it rather than keeping a second record of the sale. The
        // empty fingerprint marks the id as possibly on the server, which is
        // what `deleteAll` withdraws.
        let id: String
        if let existing = result.outcomeID {
            id = existing
        } else {
            id = UUID().uuidString
            result.outcomeID = id
            sent[id] = ""
        }
        guard let payload = SaleOutcomePayload.make(
            for: result, contributionID: id, token: contributorToken,
            build: build, storefront: await storefront()) else { return }
        let fingerprint = Self.fingerprint(payload)
        guard sent[id] != fingerprint else { return }
        do {
            try await send(.share(payload))
            sent[id] = fingerprint
        } catch {}
    }

    /// "Delete my shared sales": every record this install made.
    ///
    /// It also turns sharing off. Left on, the next close of any of those
    /// flips found no fingerprint for its id and sent it again: the user was
    /// told their sales were deleted, and each one they looked at was back on
    /// the server. The ids are kept as withdrawn, so turning sharing back on
    /// shares new sales and never these, and the token goes with the records,
    /// since nothing is left for it to delete.
    func deleteAll() async throws {
        guard hasShared else { return }
        try await send(.delete(contributionID: nil, token: contributorToken))
        isEnabled = false
        hasAsked = true
        withdrawn.formUnion(sent.keys)
        for key in [Self.sentKey, Self.pendingKey, Self.pendingAllKey, Self.tokenKey] {
            defaults.removeObject(forKey: key)
        }
    }

    /// Flips were deleted from the library: their shared records go too, now,
    /// or at the next sync if this send fails. `all` is a cleared history:
    /// one request for every record of this install rather than one per
    /// flip. On or off, as un-marking is. Returns the send, for a test.
    @discardableResult
    func forget(_ outcomeIDs: [String], all: Bool = false) -> Task<Void, Never>? {
        let withdrawn = self.withdrawn
        let ids = outcomeIDs.filter { !withdrawn.contains($0) }
        guard hasShared, !ids.isEmpty else { return nil }
        if all {
            defaults.set(true, forKey: Self.pendingAllKey)
        } else {
            pending.formUnion(ids)
        }
        for id in ids { sent[id] = nil }
        return Task { await self.flushDeletions() }
    }

    /// Sends the deletions `forget` queued, stopping at the first failure;
    /// the rest wait for the next sync.
    func flushDeletions() async {
        guard hasShared else { return }
        if defaults.bool(forKey: Self.pendingAllKey) {
            do {
                try await send(.delete(contributionID: nil, token: contributorToken))
            } catch { return }
            defaults.removeObject(forKey: Self.pendingAllKey)
            defaults.removeObject(forKey: Self.pendingKey)
            return
        }
        for id in pending.sorted() {
            do {
                try await send(.delete(contributionID: id, token: contributorToken))
            } catch { return }
            pending.remove(id)
        }
    }

    private var sent: [String: String] {
        get { defaults.dictionary(forKey: Self.sentKey) as? [String: String] ?? [:] }
        set { defaults.set(newValue, forKey: Self.sentKey) }
    }

    /// Ids deleted by "Delete my shared sales", never to be sent again.
    private var withdrawn: Set<String> {
        get { Set(defaults.stringArray(forKey: Self.withdrawnKey) ?? []) }
        set { defaults.set(newValue.sorted(), forKey: Self.withdrawnKey) }
    }

    /// Records of deleted flips whose own delete has not gone through yet.
    private var pending: Set<String> {
        get { Set(defaults.stringArray(forKey: Self.pendingKey) ?? []) }
        set { defaults.set(newValue.sorted(), forKey: Self.pendingKey) }
    }

    /// What was last sent for a record, to skip re-sending an unchanged one.
    /// Sorted keys: `JSONEncoder`'s key order is not stable between calls,
    /// so an unsorted fingerprint changed with nothing changed.
    private static func fingerprint(_ payload: SaleOutcomePayload) -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = .sortedKeys
        return ((try? encoder.encode(payload)) ?? Data()).base64EncodedString()
    }
}

/// `POST` and `DELETE /outcomes`, attested like every other call.
actor SaleOutcomesClient {
    static let shared = SaleOutcomesClient()
    private let session: URLSession = .snapWorthAPI

    struct Failure: Error { let status: Int }

    func send(_ request: SaleOutcomeRequest) async throws {
        if Config.mockScans { return }
        var req = URLRequest(url: Config.baseURL.appendingPathComponent("outcomes"))
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        switch request {
        case .share(let payload):
            req.httpMethod = "POST"
            req.httpBody = try JSONEncoder().encode(payload)
        case .delete(let id, let token):
            req.httpMethod = "DELETE"
            var body = ["contributor_token": token]
            if let id { body["contribution_id"] = id }
            req.httpBody = try JSONEncoder().encode(body)
        }
        try await req.requireBearerToken()
        let (_, http) = try await req.sendRetryingAuth(on: session)
        guard (200..<300).contains(http.statusCode) else { throw Failure(status: http.statusCode) }
    }
}
