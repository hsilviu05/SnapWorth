import Foundation

/// What `/assist` needs from a find, read on the MainActor. For the reason a
/// `ScanResult` must not reach the actor, see `ListingInput`.
struct AskTagInput: Sendable, Equatable {
    let itemName: String
    let brand: String
    let category: String
    let condition: String
    let low: Double
    let likely: Double
    let high: Double
    let confidence: String
    let soldListings: Int
    let paid: Double?
    let status: String

    @MainActor
    init(result: ScanResult) {
        let range = result.currentPriceRange
        itemName = result.itemName
        brand = result.brand
        category = result.category
        condition = result.condition.rawValue
        low = NSDecimalNumber(decimal: range.low).doubleValue
        likely = NSDecimalNumber(decimal: range.likely).doubleValue
        high = NSDecimalNumber(decimal: range.high).doubleValue
        confidence = result.confidence
        soldListings = result.soldListingsCount
        paid = result.paidPrice.flatMap { $0.isFinite && $0 >= 0 ? $0 : nil }
        status = result.status.rawValue
    }

    init(itemName: String, brand: String, category: String, condition: String,
         low: Double, likely: Double, high: Double, confidence: String,
         soldListings: Int, paid: Double?, status: String) {
        self.itemName = itemName
        self.brand = brand
        self.category = category
        self.condition = condition
        self.low = low
        self.likely = likely
        self.high = high
        self.confidence = confidence
        self.soldListings = soldListings
        self.paid = paid
        self.status = status
    }
}

/// The request body, in the server's field names.
struct AskTagAPIRequest: Encodable, Equatable {
    struct Turn: Encodable, Equatable {
        let role: String
        let text: String
    }

    let item_name: String
    let brand: String
    let category: String
    let condition: String
    let price_low_usd: Double
    let price_likely_usd: Double
    let price_high_usd: Double
    let confidence: String
    let sold_listings: Int
    let paid_usd: Double?
    let status: String
    let question: String
    let history: [Turn]
    let language: String

    init(input: AskTagInput, question: String, history: [AskTagTurn], language: String) {
        // The server refuses an empty name and negative prices with a 422.
        item_name = input.itemName.isEmpty ? "Item" : String(input.itemName.prefix(200))
        brand = String(input.brand.prefix(100))
        category = String(input.category.prefix(50))
        condition = String(input.condition.prefix(50))
        price_low_usd = max(0, input.low.isFinite ? input.low : 0)
        price_likely_usd = max(0, input.likely.isFinite ? input.likely : 0)
        price_high_usd = max(0, input.high.isFinite ? input.high : 0)
        confidence = String(input.confidence.prefix(20))
        sold_listings = min(max(0, input.soldListings), 100_000)
        paid_usd = input.paid
        status = input.status
        self.question = AskTagThread.cleanedQuestion(question)
        self.history = AskTagThread.history(history).map {
            Turn(role: $0.role.rawValue, text: $0.text)
        }
        self.language = language
    }
}

private struct AskTagAPIResponse: Decodable {
    let answer: String?
}

/// Asks `/assist`. Returns nil when the server had no answer worth keeping,
/// which the caller words itself; throws `ScanAPIError` on anything else.
actor AskTagAPIClient {
    static let shared = AskTagAPIClient()
    private init() {}

    /// See `ScanAPIClient` for why this must be the shared pinned session.
    private let session: URLSession = .snapWorthAPI

    func ask(_ input: AskTagInput, question: String, history: [AskTagTurn],
             language: String = AskTagThread.languageCode()) async throws -> String? {
        if Config.mockScans {
            try? await Task.sleep(for: .seconds(1))
            return "Start near the top of your estimate and take good daylight photos of the label."
        }
        let body = AskTagAPIRequest(input: input, question: question,
                                    history: history, language: language)
        var request = URLRequest(url: Config.baseURL.appendingPathComponent("assist"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        try await request.requireBearerToken()
        request.httpBody = try JSONEncoder().encode(body)

        let (data, http) = try await request.sendRetryingAuth(on: session)
        guard (200..<300).contains(http.statusCode) else {
            throw ScanAPIError.from(http, data: data)
        }
        let answer = try JSONDecoder().decode(AskTagAPIResponse.self, from: data).answer?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return (answer?.isEmpty ?? true) ? nil : answer
    }
}
