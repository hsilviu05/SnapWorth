import Foundation

// ── Marketplace ───────────────────────────────────────────────────────────────

/// Marketplaces Snap → Sell can tailor a listing for.
/// Declaration order is chip order in the pickers: US platforms lead, because
/// that is where most users are (#54). OLX and Vinted stay for the European
/// third of the user base.
enum Marketplace: String, CaseIterable, Identifiable {
    case ebay
    case poshmark
    case mercari
    case depop
    case facebook
    case vinted
    case olx

    var id: String { rawValue }

    /// Value the backend expects.
    var apiValue: String { rawValue }

    var displayName: String {
        switch self {
        case .ebay:     return "eBay"
        case .poshmark: return "Poshmark"
        case .mercari:  return "Mercari"
        case .depop:    return "Depop"
        case .vinted:   return "Vinted"
        case .facebook: return "Facebook"
        case .olx:      return "OLX"
        }
    }

    /// SF Symbol stand-in — the marketplaces ship no bundled brand assets.
    var iconName: String {
        switch self {
        case .ebay:     return "tag.fill"
        case .poshmark: return "hanger"
        case .mercari:  return "shippingbox.fill"
        case .depop:    return "sparkles"
        case .vinted:   return "tshirt.fill"
        case .facebook: return "person.2.fill"
        case .olx:      return "cart.fill"
        }
    }

    /// A documented public URL scheme that brings the marketplace's app to the
    /// foreground when installed. Defined only where a real scheme exists; the
    /// others fall back to `webSellURL` (which still opens the app via universal
    /// links when installed). We never fabricate a scheme.
    var appURLScheme: URL? {
        switch self {
        case .ebay:         return URL(string: "ebay://")
        case .facebook:     return URL(string: "fb://")
        // Poshmark, Mercari and Depop publish no URL scheme; universal links
        // via `webSellURL` open their apps when installed.
        case .poshmark, .mercari, .depop, .vinted, .olx: return nil
        }
    }

    /// Public "create a listing" page. Opens the native app via universal links
    /// when installed, otherwise the website.
    ///
    /// IMPORTANT: no marketplace exposes a public deep link that pre-fills the
    /// compose form, so this only lands the user on the new-listing screen where
    /// they paste the copied text. Snap → Sell cannot and does not auto-post.
    var webSellURL: URL {
        switch self {
        case .ebay:     return URL(string: "https://www.ebay.com/sl/sell")!
        case .poshmark: return URL(string: "https://poshmark.com/create-listing")!
        case .mercari:  return URL(string: "https://www.mercari.com/sell/")!
        case .depop:    return URL(string: "https://www.depop.com/products/create/")!
        case .vinted:   return URL(string: "https://www.vinted.com/items/new")!
        case .facebook: return URL(string: "https://www.facebook.com/marketplace/create/item")!
        case .olx:      return URL(string: "https://www.olx.com/")!
        }
    }
}

// ── Generated listing (client model) ──────────────────────────────────────────

/// A marketplace-ready listing. Every field is guaranteed non-empty and valid
/// (the backend validates + repairs, and the mock/fallback builds from the
/// valuation) so the UI never renders a blank listing.
struct GeneratedListing: Equatable {
    let title: String
    let description: String
    let listingPrice: Double
    let negotiationFloor: Double
    let category: String
    let marketplace: Marketplace

    /// Ready-to-paste plain text for the clipboard and share sheet.
    var shareText: String {
        let price = NumberFormatter.snapCurrency.string(from: NSNumber(value: listingPrice))
            ?? "$\(Int(listingPrice))"
        return """
        \(title)

        \(description)

        Price: \(price)
        """
    }
}

// ── API wire models ───────────────────────────────────────────────────────────

private struct ListingAPIRequest: Encodable {
    let item_name: String
    let brand: String
    let category: String
    let condition: String
    let price_low_usd: Double
    let price_likely_usd: Double
    let price_high_usd: Double
    let marketplace: String
    let currency: String
}

private struct ListingAPIResponse: Decodable {
    let title: String
    let description: String
    let listingPrice: Double
    let negotiationFloor: Double
    let category: String

    enum CodingKeys: String, CodingKey {
        case title, description, category
        case listingPrice     = "listing_price"
        case negotiationFloor = "negotiation_floor"
    }
}

// ── Client ────────────────────────────────────────────────────────────────────

/// Generates marketplace listings via the backend (LLM proxied server-side so
/// the API key never ships in the client). Mirrors `ScanAPIClient`'s shape.
actor ListingAPIClient {
    static let shared = ListingAPIClient()
    private init() {}

    /// App is USD-only today (the shared currency formatter is USD-locked).
    static let currency = "USD"

    /// Shared pinned session — see `ScanAPIClient` for why this must not be a
    /// locally-constructed URLSession.
    private let session: URLSession = .snapWorthAPI

    // Same device id as ScanAPIClient so the rate-limit backstop is coherent.
    private var deviceID: String { DeviceIdentity.shared.id }

    /// Generates a listing for `result` graded at `condition`, tailored to
    /// `marketplace`. Throws on network/server failure so the caller can offer a
    /// retry; the backend guarantees a validated, non-blank body on success.
    func generate(for result: ScanResult,
                  condition: Condition,
                  marketplace: Marketplace) async throws -> GeneratedListing {
        let range = result.priceRange(for: condition)
        if Config.mockScans {
            return mockListing(result: result, condition: condition, marketplace: marketplace, range: range)
        }
        return try await liveGenerate(result: result, condition: condition,
                                      marketplace: marketplace, range: range)
    }

    // ── Live ────────────────────────────────────────────────────────────────
    private func liveGenerate(result: ScanResult,
                              condition: Condition,
                              marketplace: Marketplace,
                              range: (low: Decimal, likely: Decimal, high: Decimal)) async throws -> GeneratedListing {
        let body = ListingAPIRequest(
            item_name: result.itemName,
            brand: result.brand,
            category: result.category,
            condition: condition.rawValue,
            price_low_usd: Self.double(range.low),
            price_likely_usd: Self.double(range.likely),
            price_high_usd: Self.double(range.high),
            marketplace: marketplace.apiValue,
            currency: Self.currency
        )

        let endpoint = Config.baseURL.appendingPathComponent("listing")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(deviceID, forHTTPHeaderField: "x-device-id")
        await request.attachBearerToken()
        request.httpBody = try JSONEncoder().encode(body)

        let (data, http) = try await request.sendRetryingAuth(on: session)
        guard (200..<300).contains(http.statusCode) else {
            throw ScanAPIError.serverError(http.statusCode, APIErrorDetail.parse(data))
        }

        let decoded = try JSONDecoder().decode(ListingAPIResponse.self, from: data)
        return GeneratedListing(
            title: decoded.title,
            description: decoded.description,
            listingPrice: decoded.listingPrice,
            negotiationFloor: min(decoded.negotiationFloor, decoded.listingPrice),
            category: decoded.category,
            marketplace: marketplace
        )
    }

    // ── Mock ────────────────────────────────────────────────────────────────
    private func mockListing(result: ScanResult,
                             condition: Condition,
                             marketplace: Marketplace,
                             range: (low: Decimal, likely: Decimal, high: Decimal)) -> GeneratedListing {
        let ask = (Self.double(range.likely)).rounded()
        let floor = min((Self.double(range.low)).rounded(), ask)
        let phrase = condition.listingPhrase

        let title: String
        let description: String
        switch marketplace {
        case .ebay:
            title = String(result.itemName.prefix(80))
            description = "\(result.itemName) in \(phrase). Ships fast from a smoke-free home. "
                + "Please see photos for exact condition — buy with confidence."
        case .vinted:
            title = String(result.itemName.prefix(80))
            description = "Lovely \(result.itemName.lowercased()), \(phrase). Happy to share "
                + "measurements — just ask! Bundle to save. 💛"
        case .facebook:
            title = String(result.itemName.prefix(80))
            description = "\(result.itemName), \(phrase). Local pickup preferred. Price is OBO — "
                + "message me if interested!"
        case .olx:
            title = String(result.itemName.prefix(80))
            description = "\(result.itemName) — \(phrase). Cash on local pickup. Serious buyers only, thanks."
        case .poshmark:
            title = String(result.itemName.prefix(80))
            description = "\(result.itemName) in \(phrase). Measurements on request — bundle for a "
                + "discount! Ships next day from a smoke-free closet."
        case .mercari:
            title = String(result.itemName.prefix(80))
            description = "\(result.itemName), \(phrase). What you see is what you get. "
                + "Ships within 1 business day."
        case .depop:
            title = String(result.itemName.prefix(80))
            description = "\(result.itemName.lowercased()) — \(phrase). dm for measurements, "
                + "open to offers. #vintage #thrift #secondhand"
        }

        return GeneratedListing(
            title: title, description: description,
            listingPrice: ask, negotiationFloor: floor,
            category: result.category, marketplace: marketplace
        )
    }

    private static func double(_ d: Decimal) -> Double {
        NSDecimalNumber(decimal: d).doubleValue
    }
}


// ═══════════════════════════════════════════════════════════════════
// MARK: - Trends (#96)
// ═══════════════════════════════════════════════════════════════════

/// One row of the weekly trend: a category or a brand, how many scans it drew,
/// and — when both weeks had enough of them — which way it moved.
struct TrendRow: Decodable, Identifiable, Equatable {
    let name: String
    let count: Int
    let changePct: Int?
    /// Pro only, and only once enough finds support it.
    let averageEstimate: Double?

    var id: String { name }

    enum CodingKeys: String, CodingKey {
        case name, count
        case changePct = "change_pct"
        case averageEstimate = "average_estimate"
    }
}

struct NotableFind: Decodable, Identifiable, Equatable {
    let name: String
    let category: String
    let low: Double
    let high: Double

    var id: String { "\(name)-\(low)-\(high)" }
}

/// What the app shows on My Finds. Aggregates about everyone, never about a
/// person: the server applies a floor before any of this is sent.
struct Trends: Decodable, Equatable {
    let days: Int
    let scans: Int
    let categories: [TrendRow]
    let brands: [TrendRow]
    let notableFinds: [NotableFind]

    enum CodingKeys: String, CodingKey {
        case days, scans, categories, brands
        case notableFinds = "notable_finds"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        days = try c.decodeIfPresent(Int.self, forKey: .days) ?? 7
        scans = try c.decodeIfPresent(Int.self, forKey: .scans) ?? 0
        categories = try c.decodeIfPresent([TrendRow].self, forKey: .categories) ?? []
        brands = try c.decodeIfPresent([TrendRow].self, forKey: .brands) ?? []
        notableFinds = try c.decodeIfPresent([NotableFind].self, forKey: .notableFinds) ?? []
    }

    init(days: Int = 7, scans: Int = 0, categories: [TrendRow] = [],
         brands: [TrendRow] = [], notableFinds: [NotableFind] = []) {
        self.days = days
        self.scans = scans
        self.categories = categories
        self.brands = brands
        self.notableFinds = notableFinds
    }

    /// Nothing cleared the server's floor this week — show nothing rather than
    /// an empty heading.
    var isEmpty: Bool { categories.isEmpty && brands.isEmpty }
}

/// Reads `GET /trends`. Same session, same device header, same auth retry as
/// every other call; the free/Pro shape is decided by the server.
actor TrendsAPIClient {
    static let shared = TrendsAPIClient()
    private init() {}

    private let session: URLSession = .snapWorthAPI
    private var deviceID: String { DeviceIdentity.shared.id }

    func fetch() async throws -> Trends {
        if Config.mockScans { return Self.mock }

        var request = URLRequest(url: Config.baseURL.appendingPathComponent("trends"))
        request.httpMethod = "GET"
        request.setValue(deviceID, forHTTPHeaderField: "x-device-id")
        await request.attachBearerToken()

        let (data, http) = try await request.sendRetryingAuth(on: session)
        guard (200..<300).contains(http.statusCode) else {
            throw ScanAPIError.serverError(http.statusCode, APIErrorDetail.parse(data))
        }
        return try JSONDecoder().decode(Trends.self, from: data)
    }

    /// For the Simulator's mock-scans scheme.
    static let mock = Trends(
        days: 7, scans: 128,
        categories: [TrendRow(name: "clothing", count: 54, changePct: 18, averageEstimate: 46),
                     TrendRow(name: "shoes", count: 31, changePct: -7, averageEstimate: 72),
                     TrendRow(name: "home", count: 22, changePct: nil, averageEstimate: 58)],
        brands: [TrendRow(name: "Carhartt", count: 14, changePct: 40, averageEstimate: nil),
                 TrendRow(name: "Nike", count: 11, changePct: 5, averageEstimate: nil),
                 TrendRow(name: "Le Creuset", count: 7, changePct: nil, averageEstimate: nil)],
        notableFinds: [NotableFind(name: "Le Creuset Dutch Oven 5.5qt", category: "home", low: 120, high: 220),
                       NotableFind(name: "The North Face Nuptse 700", category: "clothing", low: 110, high: 200)])
}

extension TrendRow {
    init(name: String, count: Int, changePct: Int?, averageEstimate: Double?) {
        self.name = name
        self.count = count
        self.changePct = changePct
        self.averageEstimate = averageEstimate
    }
}

extension NotableFind {
    init(name: String, category: String, low: Double, high: Double) {
        self.name = name
        self.category = category
        self.low = low
        self.high = high
    }
}
