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
    /// Legacy compatibility field. The backend has always returned 0 since 1.2
    /// (`main.py` — the model never produced it and no comps source exists), and
    /// no UI surface renders it.
    ///
    /// Decoded with a default so the backend can drop the field entirely once
    /// clients below 1.2 age out, without this client failing to decode.
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

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        itemName            = try c.decode(String.self, forKey: .itemName)
        brand               = try c.decode(String.self, forKey: .brand)
        category            = try c.decode(String.self, forKey: .category)
        conditionNotes      = try c.decode(String.self, forKey: .conditionNotes)
        estValueLowUsd      = try c.decode(Double.self, forKey: .estValueLowUsd)
        estValueHighUsd     = try c.decode(Double.self, forKey: .estValueHighUsd)
        confidence          = try c.decode(String.self, forKey: .confidence)
        soldListingsCount   = try c.decodeIfPresent(Int.self, forKey: .soldListingsCount) ?? 0
        listingTitle        = try c.decode(String.self, forKey: .listingTitle)
        listingDescription  = try c.decode(String.self, forKey: .listingDescription)
    }

    /// Memberwise init retained for mocks and previews.
    init(itemName: String, brand: String, category: String, conditionNotes: String,
         estValueLowUsd: Double, estValueHighUsd: Double, confidence: String,
         soldListingsCount: Int = 0, listingTitle: String, listingDescription: String) {
        self.itemName = itemName
        self.brand = brand
        self.category = category
        self.conditionNotes = conditionNotes
        self.estValueLowUsd = estValueLowUsd
        self.estValueHighUsd = estValueHighUsd
        self.confidence = confidence
        self.soldListingsCount = soldListingsCount
        self.listingTitle = listingTitle
        self.listingDescription = listingDescription
    }
}

// ── Client ────────────────────────────────────────────────────────────────────
actor ScanAPIClient {
    static let shared = ScanAPIClient()
    private init() {}

    /// Shared pinned session — see `CertificatePinning.swift`. Using the shared
    /// instance (rather than a private one) is what puts API traffic behind the
    /// pinning delegate; a locally-built session would silently bypass it.
    private let session: URLSession = .snapWorthAPI

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
                listingTitle: "Nike Air Max 90 White Size 10 — Clean & Ready",
                listingDescription: "Nike Air Max 90 in good used condition. Some normal creasing on the toe box but soles are clean and cushioning is excellent. Includes original laces."
            ),
        ]

        return mocks[Int.random(in: 0..<mocks.count)]
    }

    // ── Live ──────────────────────────────────────────────────────────────────
    private func liveScan(image: UIImage) async throws -> ScanAPIResponse {
        guard let jpegData = await Self.encodeForUpload(image) else {
            throw ScanAPIError.imageEncodingFailed
        }

        let endpoint = Config.baseURL.appendingPathComponent("scan")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        // Retained during rollout: the server falls back to this when
        // attestation isn't enforced yet.
        request.setValue(deviceID, forHTTPHeaderField: "x-device-id")
        await request.attachBearerToken()

        let boundary = "Boundary-\(UUID().uuidString)"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.httpBody = buildMultipart(data: jpegData, boundary: boundary)

        let (data, response) = try await session.data(for: request)

        guard let http = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        guard (200..<300).contains(http.statusCode) else {
            throw ScanAPIError.serverError(http.statusCode, APIErrorDetail.parse(data))
        }

        let decoder = JSONDecoder()
        return try decoder.decode(ScanAPIResponse.self, from: data)
    }

    /// Longest edge, in pixels, sent to the backend.
    ///
    /// Vision models downsample their input aggressively, so a full 12 MP
    /// capture (≈3.5 MB at quality 0.82) spends roughly 12× the bytes to deliver
    /// the same information. That upload is the dominant term in scan latency —
    /// users are on in-store cellular, where 3.5 MB is ~19 s before the model
    /// has seen a byte, against a 35 s resource timeout.
    ///
    /// 1568 px preserves label and tag legibility, which is what identification
    /// actually depends on.
    static let maxUploadEdge: CGFloat = 1568

    /// Downscale and JPEG-encode off the main actor.
    ///
    /// `Task.detached` matters: `UIGraphicsImageRenderer` on a 12 MP image costs
    /// 80–150 ms and would drop frames if it ran inline on the main actor while
    /// the analysing overlay animates in.
    static func encodeForUpload(
        _ image: UIImage,
        maxEdge: CGFloat = maxUploadEdge,
        quality: CGFloat = 0.8
    ) async -> Data? {
        await Task.detached(priority: .userInitiated) {
            downscale(image, maxEdge: maxEdge).jpegData(compressionQuality: quality)
        }.value
    }

    /// Longest edge, in pixels, of the copy persisted to SwiftData.
    ///
    /// History shows this image in a 2-column grid card and as a hero on the
    /// result sheet — roughly 340pt at 3× — so anything beyond ~1024px is
    /// storing detail no surface displays. At full resolution each scan was
    /// costing 2–3 MB of disk, which is ~1.5 GB for a user with 500 finds.
    static let maxStoredEdge: CGFloat = 1024

    /// Downscale and encode for local persistence, off the main actor.
    ///
    /// This previously ran inline in `ScanViewModel.startScan`, which is
    /// `@MainActor`: encoding a 12 MP capture is 80–150 ms of main-thread work
    /// happening exactly as the analysing overlay animates out and the result
    /// sheet presents. That is a visible hitch at the single most important
    /// moment in the app.
    static func encodeForStorage(
        _ image: UIImage,
        maxEdge: CGFloat = maxStoredEdge,
        quality: CGFloat = 0.75
    ) async -> Data? {
        await Task.detached(priority: .utility) {
            downscale(image, maxEdge: maxEdge).jpegData(compressionQuality: quality)
        }.value
    }

    /// Aspect-preserving downscale. Returns the original when already small
    /// enough, so a library pick of a tiny image is never upscaled.
    nonisolated static func downscale(_ image: UIImage, maxEdge: CGFloat) -> UIImage {
        let longest = max(image.size.width, image.size.height)
        guard longest > maxEdge, longest > 0 else { return image }

        let scale = maxEdge / longest
        let target = CGSize(width: (image.size.width * scale).rounded(),
                            height: (image.size.height * scale).rounded())

        let format = UIGraphicsImageRendererFormat.default()
        // Points == pixels. The default is the screen scale, which would
        // silently render a 3× larger bitmap and undo the downscale.
        format.scale = 1
        // Photos have no alpha; an opaque context skips a channel.
        format.opaque = true

        return UIGraphicsImageRenderer(size: target, format: format).image { _ in
            image.draw(in: CGRect(origin: .zero, size: target))
        }
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

/// Decodes FastAPI's `detail` field, which is *not* always a string.
///
/// Request-validation failures (422) return `detail` as an array of objects.
/// Decoding straight into `[String: String]` therefore fails on exactly the
/// responses that carry the most diagnostic value, and the user saw the
/// "Unknown error" fallback instead.
enum APIErrorDetail {
    static func parse(_ data: Data) -> String {
        guard !data.isEmpty,
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let detail = root["detail"]
        else { return fallback }

        if let text = detail as? String, !text.isEmpty { return text }

        // 422 shape: [{"loc": [...], "msg": "...", "type": "..."}]
        if let items = detail as? [[String: Any]] {
            let messages = items.compactMap { $0["msg"] as? String }
            if !messages.isEmpty { return messages.joined(separator: " ") }
        }
        return fallback
    }

    private static let fallback = "Something went wrong. Please try again."
}

enum ScanAPIError: LocalizedError {
    case serverError(Int, String)
    case imageEncodingFailed

    var errorDescription: String? {
        switch self {
        case .serverError(_, let detail):
            // The status code is diagnostic noise to a user standing in a shop —
            // `detail` already carries a user-safe message from the backend.
            return detail
        case .imageEncodingFailed:
            return "That photo couldn't be prepared for analysis. Please try taking it again."
        }
    }

    /// Status code, retained for analytics and paywall routing.
    var statusCode: Int? {
        if case .serverError(let code, _) = self { return code }
        return nil
    }
}
