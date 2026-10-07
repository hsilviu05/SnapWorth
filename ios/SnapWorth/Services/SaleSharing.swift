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

    /// The currency a flip's amounts are in: chosen, or the region default.
    static func of(_ result: ScanResult, locale: Locale = .current) -> String {
        result.saleCurrency.flatMap { all.contains($0) ? $0 : nil }
            ?? regionDefault(locale: locale)
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

    /// An amount in whole units, as `locale` writes `code`: "$80", "80 lei".
    static func format(_ amount: Decimal, code: String, locale: Locale = .current) -> String {
        formatter(code, locale: locale).string(from: NSDecimalNumber(decimal: amount))
            ?? "\(amount) \(code)"
    }

    /// Signed, with U+2212 for a loss as `FlipsViewModel` always printed it:
    /// "+80 lei", "−$12".
    static func signed(_ amount: Decimal, code: String, locale: Locale = .current) -> String {
        let money = format(abs(amount), code: code, locale: locale)
        return amount < 0 ? "−\(money)" : "+\(money)"
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

    private static func formatter(_ code: String, locale: Locale) -> NumberFormatter {
        let f = NumberFormatter()
        f.numberStyle = .currency
        f.locale = locale
        f.currencyCode = code
        f.maximumFractionDigits = 0
        return f
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

    /// When a result sheet closes. Off: nothing at all. On: a sold flip with a
    /// price is sent (an edit replaces the same record, an unchanged one is
    /// not re-sent); a flip that was shared and is no longer sold is deleted.
    /// A failure is left for the next close; nothing here interrupts the user.
    func sync(_ result: ScanResult) async {
        guard isEnabled else { return }
        if let id = result.outcomeID ?? (result.status == .sold ? UUID().uuidString : nil),
           let payload = SaleOutcomePayload.make(
               for: result, contributionID: id, token: contributorToken,
               build: build, storefront: await storefront()) {
            let fingerprint = Self.fingerprint(payload)
            guard sent[id] != fingerprint else { return }
            do {
                try await send(.share(payload))
                result.outcomeID = id
                sent[id] = fingerprint
            } catch {}
        } else if let id = result.outcomeID {
            do {
                try await send(.delete(contributionID: id, token: contributorToken))
                result.outcomeID = nil
                sent[id] = nil
            } catch {}
        }
    }

    /// "Delete my shared sales": every record this install made.
    func deleteAll() async throws {
        guard hasShared else { return }
        try await send(.delete(contributionID: nil, token: contributorToken))
        defaults.removeObject(forKey: Self.sentKey)
    }

    private var sent: [String: String] {
        get { defaults.dictionary(forKey: Self.sentKey) as? [String: String] ?? [:] }
        set { defaults.set(newValue, forKey: Self.sentKey) }
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
