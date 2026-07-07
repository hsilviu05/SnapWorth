import Foundation
import UIKit

// ── API response model ────────────────────────────────────────────────────────
struct ScanAPIResponse: Decodable {
    let itemName: String
    let brand: String
    let category: String
    let conditionNotes: String
    let estValueLowUsd: Double
    let estValueHighUsd: Double
    let confidence: String
    let soldListingsCount: Int
    let listingTitle: String
    let listingDescription: String

    enum CodingKeys: String, CodingKey {
        case itemName            = "item_name"
        case brand
        case category
        case conditionNotes      = "condition_notes"
        case estValueLowUsd      = "est_value_low_usd"
        case estValueHighUsd     = "est_value_high_usd"
        case confidence
        case soldListingsCount   = "sold_listings_count"
        case listingTitle        = "listing_title"
        case listingDescription  = "listing_description"
    }
}

// ── Client ────────────────────────────────────────────────────────────────────
actor ScanAPIClient {
    static let shared = ScanAPIClient()
    private init() {}

    private let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        config.timeoutIntervalForResource = 35
        return URLSession(configuration: config)
    }()

    private let deviceID: String = {
        if let stored = UserDefaults.standard.string(forKey: "snapworth_device_id") {
            return stored
        }
        let newID = UUID().uuidString
        UserDefaults.standard.set(newID, forKey: "snapworth_device_id")
        return newID
    }()

    /// Uploads `image` to the backend and returns the AI analysis.
    /// When `Config.mockMode` is true, returns realistic canned data instantly.
    func scan(image: UIImage) async throws -> ScanAPIResponse {
        if Config.mockMode {
            return try await mockScan()
        }
        return try await liveScan(image: image)
    }

    // ── Mock ──────────────────────────────────────────────────────────────────
    private func mockScan() async throws -> ScanAPIResponse {
        // Simulate ~2 second network + AI latency
        try await Task.sleep(for: .seconds(2.2))

        let mocks: [ScanAPIResponse] = [
            ScanAPIResponse(
                itemName: "Patagonia Better Sweater 1/4-Zip, Size M",
                brand: "Patagonia",
                category: "clothing",
                conditionNotes: "Good — light pilling on cuffs, no stains or damage",
                estValueLowUsd: 45,
                estValueHighUsd: 90,
                confidence: "High",
                soldListingsCount: 38,
                listingTitle: "Patagonia Better Sweater Fleece 1/4-Zip Medium",
                listingDescription: "Classic Patagonia Better Sweater in great used condition. Light pilling typical of normal wear — no stains, holes, or fading. Retails for $149 new. Ships same day in smoke-free home."
            ),
            ScanAPIResponse(
                itemName: "Levi's 501 Original Straight Jeans, 32x32",
                brand: "Levi's",
                category: "clothing",
                conditionNotes: "Very Good — minimal wear, no fading",
                estValueLowUsd: 28,
                estValueHighUsd: 55,
                confidence: "High",
                soldListingsCount: 62,
                listingTitle: "Levi's 501 Original Straight Jeans 32x32 Vintage",
                listingDescription: "Authentic Levi's 501 in excellent secondhand condition. Minimal wear with original dark wash intact. Classic fit that never goes out of style."
            ),
            ScanAPIResponse(
                itemName: "Nike Air Max 90 Sneakers, Size 10",
                brand: "Nike",
                category: "shoes",
                conditionNotes: "Good — creasing on toe box, clean soles",
                estValueLowUsd: 55,
                estValueHighUsd: 110,
                confidence: "Medium",
                soldListingsCount: 24,
                listingTitle: "Nike Air Max 90 White Size 10 — Clean & Ready",
                listingDescription: "Nike Air Max 90 in good used condition. Some normal creasing on the toe box but soles are clean and cushioning is excellent. Includes original laces."
            ),
        ]

        return mocks[Int.random(in: 0..<mocks.count)]
    }

    // ── Live ──────────────────────────────────────────────────────────────────
    private func liveScan(image: UIImage) async throws -> ScanAPIResponse {
        guard let jpegData = image.jpegData(compressionQuality: 0.82) else {
            throw URLError(.badURL)
        }

        let endpoint = Config.baseURL.appendingPathComponent("scan")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue(deviceID, forHTTPHeaderField: "x-device-id")

        let boundary = "Boundary-\(UUID().uuidString)"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.httpBody = buildMultipart(data: jpegData, boundary: boundary)

        let (data, response) = try await session.data(for: request)

        guard let http = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        guard (200..<300).contains(http.statusCode) else {
            let detail = (try? JSONDecoder().decode([String: String].self, from: data))?["detail"]
            throw ScanAPIError.serverError(http.statusCode, detail ?? "Unknown error")
        }

        let decoder = JSONDecoder()
        return try decoder.decode(ScanAPIResponse.self, from: data)
    }

    private func buildMultipart(data: Data, boundary: String) -> Data {
        var body = Data()
        let crlf = "\r\n"
        body.append(Data("--\(boundary)\(crlf)".utf8))
        body.append(Data("Content-Disposition: form-data; name=\"file\"; filename=\"scan.jpg\"\(crlf)".utf8))
        body.append(Data("Content-Type: image/jpeg\(crlf)\(crlf)".utf8))
        body.append(data)
        body.append(Data("\(crlf)--\(boundary)--\(crlf)".utf8))
        return body
    }
}

enum ScanAPIError: LocalizedError {
    case serverError(Int, String)

    var errorDescription: String? {
        switch self {
        case .serverError(let code, let detail):
            return "Server error \(code): \(detail)"
        }
    }
}
