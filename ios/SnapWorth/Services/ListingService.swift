import Foundation

// ── Marketplace ───────────────────────────────────────────────────────────────

/// Marketplaces Snap → Sell can tailor a listing for.
enum Marketplace: String, CaseIterable, Identifiable {
    case ebay
    case vinted
    case facebook
    case olx

    var id: String { rawValue }

    /// Value the backend expects.
    var apiValue: String { rawValue }

    var displayName: String {
        switch self {
        case .ebay:     return "eBay"
        case .vinted:   return "Vinted"
        case .facebook: return "Facebook"
        case .olx:      return "OLX"
        }
    }

    /// SF Symbol stand-in — the marketplaces ship no bundled brand assets.
    var iconName: String {
        switch self {
        case .ebay:     return "tag.fill"
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
        case .vinted, .olx: return nil
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

    private let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        config.timeoutIntervalForResource = 35
        return URLSession(configuration: config)
    }()

    // Same device id as ScanAPIClient so the rate-limit backstop is coherent.
    private let deviceID: String = {
        if let stored = UserDefaults.standard.string(forKey: "snapworth_device_id") {
            return stored
        }
        let newID = UUID().uuidString
        UserDefaults.standard.set(newID, forKey: "snapworth_device_id")
        return newID
    }()

    /// Generates a listing for `result` graded at `condition`, tailored to
    /// `marketplace`. Throws on network/server failure so the caller can offer a
    /// retry; the backend guarantees a validated, non-blank body on success.
    func generate(for result: ScanResult,
                  condition: Condition,
                  marketplace: Marketplace) async throws -> GeneratedListing {
        let range = result.priceRange(for: condition)
        if Config.mockMode {
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

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        guard (200..<300).contains(http.statusCode) else {
            let detail = (try? JSONDecoder().decode([String: String].self, from: data))?["detail"]
            throw ScanAPIError.serverError(http.statusCode, detail ?? "Unknown error")
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
