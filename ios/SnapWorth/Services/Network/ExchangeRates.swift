import Foundation

/// Today's exchange rates, for the "≈ 185–370 lei" line under an estimate.
///
/// Estimates are US resale prices and stay in dollars (`ios/Localization/
/// README.md`, Money). The line only restates them in the phone's currency,
/// and says it is a conversion: it is not what the item sells for locally.
struct ExchangeRateTable: Codable, Equatable {
    /// The ECB reference day the rates are from.
    let date: String
    /// Units of each currency per US dollar.
    let rates: [String: Double]
}

enum ConvertedPrice {
    /// "≈ 185–370 lei", or nil when the phone's currency is dollars, has no
    /// rate, or the range is not a range of money.
    static func range(low: Double, high: Double, table: ExchangeRateTable?,
                      locale: Locale = .current) -> String? {
        let code = SaleCurrency.regionDefault(locale: locale)
        guard code != "USD", let rate = table?.rates[code], rate.isFinite, rate > 0,
              low.isFinite, high.isFinite, low >= 0, high >= low else { return nil }
        func money(_ usd: Double) -> String {
            SaleCurrency.formatWhole(Decimal((usd * rate).rounded()), code: code, locale: locale)
        }
        let a = money(low), b = money(high)
        return a == b ? "≈ \(a)" : "≈ \(a)–\(b)"
    }
}

/// Fetches `/rates` at most every `ttl`, and keeps the last table on the
/// phone. A table older than `maxAge` is not shown: a stale rate printed as
/// today's would be wrong in the way this line exists to avoid.
actor ExchangeRatesClient {
    static let shared = ExchangeRatesClient()
    private init() {}

    private let session: URLSession = .snapWorthAPI
    private static let key = "snapworth_exchange_rates"
    static let ttl: TimeInterval = 12 * 3600
    static let maxAge: TimeInterval = 4 * 24 * 3600

    private struct Stored: Codable {
        let table: ExchangeRateTable
        let fetchedAt: Date
    }

    private var inFlight = false

    /// The table to show now, refreshing it first when it is due. Nil when
    /// there is none recent enough; never throws, since the line is optional.
    func table(now: Date = Date()) async -> ExchangeRateTable? {
        let stored = Self.load()
        if Config.mockScans { return ExchangeRateTable(date: "mock", rates: ["EUR": 0.93, "RON": 4.6]) }
        if stored.map({ now.timeIntervalSince($0.fetchedAt) >= Self.ttl }) ?? true, !inFlight {
            inFlight = true
            defer { inFlight = false }
            if let fresh = try? await fetch() {
                Self.save(Stored(table: fresh, fetchedAt: now))
                return fresh
            }
        }
        guard let stored, now.timeIntervalSince(stored.fetchedAt) < Self.maxAge else { return nil }
        return stored.table
    }

    private func fetch() async throws -> ExchangeRateTable {
        var request = URLRequest(url: Config.baseURL.appendingPathComponent("rates"))
        request.httpMethod = "GET"
        try await request.requireBearerToken()
        let (data, http) = try await request.sendRetryingAuth(on: session)
        guard (200..<300).contains(http.statusCode) else {
            throw ScanAPIError.from(http, data: data)
        }
        return try JSONDecoder().decode(ExchangeRateTable.self, from: data)
    }

    private static func load() -> Stored? {
        UserDefaults.standard.data(forKey: key).flatMap { try? JSONDecoder().decode(Stored.self, from: $0) }
    }

    private static func save(_ stored: Stored) {
        if let data = try? JSONEncoder().encode(stored) {
            UserDefaults.standard.set(data, forKey: key)
        }
    }
}
