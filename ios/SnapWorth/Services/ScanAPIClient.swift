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
    /// Legacy field. The backend stopped sending it in September 2026 (#49):
    /// it had been a hardcoded 0 since 1.2, the model never produced it and no
    /// comps source exists, and no UI surface renders it. Decoded as optional
    /// with a 0 default, so its absence is the normal case and its presence in
    /// an old cached response is harmless.
    ///
    /// Kept here only because `ScanResult.soldListingsCount` is persisted in
    /// SwiftData, and the store's schema is additive-only. A real
    /// comparable-sales count, when it ships, uses a new name.
    let soldListingsCount: Int
    let listingTitle: String
    let listingDescription: String
    /// Free scans left after this one, straight from the server. `nil` for Pro
    /// and when the quota store was unreachable — the caller must fall back to
    /// its local count rather than invent a number.
    let freeScansRemaining: Int?

    // ── v2 detail (#87) ──────────────────────────────────────────────────────
    // Everything the backend has returned since July and the app ignored: the
    // four price points, the computed confidence with its reasons, what drives
    // the value, what was assumed, how to sharpen the estimate, and the
    // authenticity read. All optional — an older server simply omits them.
    let confidenceScore: Int?
    let confidenceSummary: String?
    let confidenceReasons: [String]
    let quickSalePriceUsd: Double?
    let expectedPriceUsd: Double?
    let bestCasePriceUsd: Double?
    let worstCasePriceUsd: Double?
    let valueDrivers: [String]
    let assumptions: [String]
    let uncertaintyFactors: [String]
    let improveEstimate: [String]
    let authenticityAssessment: String?
    let authenticityReasoning: String?
    let demand: String?
    let supply: String?
    let conditionGrade: String?
    let size: String?
    let era: String?
    let material: String?

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
        case freeScansRemaining  = "free_scans_remaining"
        case confidenceScore     = "confidence_score"
        case confidenceSummary   = "confidence_summary"
        case confidenceReasons   = "confidence_reasons"
        case quickSalePriceUsd   = "quick_sale_price_usd"
        case expectedPriceUsd    = "expected_price_usd"
        case bestCasePriceUsd    = "best_case_price_usd"
        case worstCasePriceUsd   = "worst_case_price_usd"
        case valueDrivers        = "value_drivers"
        case assumptions
        case uncertaintyFactors  = "uncertainty_factors"
        case improveEstimate     = "improve_estimate"
        case authenticityAssessment = "authenticity_assessment"
        case authenticityReasoning  = "authenticity_reasoning"
        case demand, supply
        case conditionGrade      = "condition_grade"
        case size, era, material
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
        freeScansRemaining  = try c.decodeIfPresent(Int.self, forKey: .freeScansRemaining)
        listingTitle        = try c.decode(String.self, forKey: .listingTitle)
        listingDescription  = try c.decode(String.self, forKey: .listingDescription)

        confidenceScore     = try c.decodeIfPresent(Int.self, forKey: .confidenceScore)
        confidenceSummary   = try c.decodeIfPresent(String.self, forKey: .confidenceSummary)
        confidenceReasons   = try c.decodeIfPresent([String].self, forKey: .confidenceReasons) ?? []
        quickSalePriceUsd   = try c.decodeIfPresent(Double.self, forKey: .quickSalePriceUsd)
        expectedPriceUsd    = try c.decodeIfPresent(Double.self, forKey: .expectedPriceUsd)
        bestCasePriceUsd    = try c.decodeIfPresent(Double.self, forKey: .bestCasePriceUsd)
        worstCasePriceUsd   = try c.decodeIfPresent(Double.self, forKey: .worstCasePriceUsd)
        valueDrivers        = try c.decodeIfPresent([String].self, forKey: .valueDrivers) ?? []
        assumptions         = try c.decodeIfPresent([String].self, forKey: .assumptions) ?? []
        uncertaintyFactors  = try c.decodeIfPresent([String].self, forKey: .uncertaintyFactors) ?? []
        improveEstimate     = try c.decodeIfPresent([String].self, forKey: .improveEstimate) ?? []
        authenticityAssessment = try c.decodeIfPresent(String.self, forKey: .authenticityAssessment)
        authenticityReasoning  = try c.decodeIfPresent(String.self, forKey: .authenticityReasoning)
        demand              = try c.decodeIfPresent(String.self, forKey: .demand)
        supply              = try c.decodeIfPresent(String.self, forKey: .supply)
        conditionGrade      = try c.decodeIfPresent(String.self, forKey: .conditionGrade)
        size                = try c.decodeIfPresent(String.self, forKey: .size)
        era                 = try c.decodeIfPresent(String.self, forKey: .era)
        material            = try c.decodeIfPresent(String.self, forKey: .material)
    }

    /// Memberwise init retained for mocks and previews.
    init(itemName: String, brand: String, category: String, conditionNotes: String,
         estValueLowUsd: Double, estValueHighUsd: Double, confidence: String,
         soldListingsCount: Int = 0, listingTitle: String, listingDescription: String,
         freeScansRemaining: Int? = nil,
         confidenceScore: Int? = nil, confidenceSummary: String? = nil,
         confidenceReasons: [String] = [], quickSalePriceUsd: Double? = nil,
         expectedPriceUsd: Double? = nil, bestCasePriceUsd: Double? = nil,
         worstCasePriceUsd: Double? = nil, valueDrivers: [String] = [],
         assumptions: [String] = [], uncertaintyFactors: [String] = [],
         improveEstimate: [String] = [], authenticityAssessment: String? = nil,
         authenticityReasoning: String? = nil, demand: String? = nil, supply: String? = nil,
         conditionGrade: String? = nil, size: String? = nil, era: String? = nil,
         material: String? = nil) {
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
        self.freeScansRemaining = freeScansRemaining
        self.confidenceScore = confidenceScore
        self.confidenceSummary = confidenceSummary
        self.confidenceReasons = confidenceReasons
        self.quickSalePriceUsd = quickSalePriceUsd
        self.expectedPriceUsd = expectedPriceUsd
        self.bestCasePriceUsd = bestCasePriceUsd
        self.worstCasePriceUsd = worstCasePriceUsd
        self.valueDrivers = valueDrivers
        self.assumptions = assumptions
        self.uncertaintyFactors = uncertaintyFactors
        self.improveEstimate = improveEstimate
        self.authenticityAssessment = authenticityAssessment
        self.authenticityReasoning = authenticityReasoning
        self.demand = demand
        self.supply = supply
        self.conditionGrade = conditionGrade
        self.size = size
        self.era = era
        self.material = material
    }
}

// MARK: - Valuation detail (#87)

/// The "why this price" payload, kept with the scan.
///
/// One Codable blob stored on `ScanResult.valuationDetailData` rather than
/// nineteen new SwiftData properties: it is only ever read whole for one
/// result's panel, never queried across rows, and a single optional `Data`
/// column is the smallest possible additive migration (the same reasoning as
/// `valueHistoryData`). Nil for every scan saved before this shipped and for
/// any server that does not send the fields; the panel simply does not appear.
struct ValuationDetail: Codable, Equatable {
    var confidenceScore: Int?
    var confidenceSummary: String?
    var confidenceReasons: [String] = []
    var quickSale: Double?
    var expected: Double?
    var bestCase: Double?
    var worstCase: Double?
    var valueDrivers: [String] = []
    var assumptions: [String] = []
    var uncertaintyFactors: [String] = []
    var improveEstimate: [String] = []
    var authenticityAssessment: String?
    var authenticityReasoning: String?
    var demand: String?
    var supply: String?
    var conditionGrade: String?
    var size: String?
    var era: String?
    var material: String?

    /// Nil when the response carried nothing beyond the v1 fields, so an old
    /// server never produces an empty panel.
    init?(response r: ScanAPIResponse) {
        confidenceScore = r.confidenceScore
        confidenceSummary = r.confidenceSummary
        confidenceReasons = r.confidenceReasons
        quickSale = r.quickSalePriceUsd
        expected = r.expectedPriceUsd
        bestCase = r.bestCasePriceUsd
        worstCase = r.worstCasePriceUsd
        valueDrivers = r.valueDrivers
        assumptions = r.assumptions
        uncertaintyFactors = r.uncertaintyFactors
        improveEstimate = r.improveEstimate
        authenticityAssessment = r.authenticityAssessment
        authenticityReasoning = r.authenticityReasoning
        demand = r.demand
        supply = r.supply
        conditionGrade = r.conditionGrade
        size = r.size
        era = r.era
        material = r.material
        guard !isEmpty else { return nil }
    }

    init() {}

    var isEmpty: Bool {
        confidenceScore == nil && confidenceSummary == nil && confidenceReasons.isEmpty
            && quickSale == nil && expected == nil && bestCase == nil && worstCase == nil
            && valueDrivers.isEmpty && assumptions.isEmpty && uncertaintyFactors.isEmpty
            && improveEstimate.isEmpty && authenticityAssessment == nil
            && demand == nil && supply == nil && conditionGrade == nil
            && size == nil && era == nil && material == nil
    }

    /// The price points that exist, floor to ceiling, ready to render.
    var ladder: [(label: String, value: Double)] {
        var rows: [(String, Double)] = []
        if let v = worstCase, v > 0 { rows.append(("Floor", v)) }
        if let v = quickSale, v > 0 { rows.append(("Quick sale", v)) }
        if let v = expected, v > 0 { rows.append(("Expected", v)) }
        if let v = bestCase, v > 0 { rows.append(("Best case", v)) }
        return rows.map { (label: $0.0, value: $0.1) }
    }

    /// Identification facts worth a line: grade, size, era, material.
    var facts: [String] {
        [conditionGrade, size, era, material].compactMap { $0 }.filter { !$0.isEmpty }
    }

    func encoded() -> Data? { try? JSONEncoder().encode(self) }

    static func decode(_ data: Data?) -> ValuationDetail? {
        guard let data, let detail = try? JSONDecoder().decode(ValuationDetail.self, from: data),
              !detail.isEmpty else { return nil }
        return detail
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

    // Keychain-backed, so it survives reinstall — see `DeviceIdentity`.
    private var deviceID: String { DeviceIdentity.shared.id }

    /// Uploads `image` to the backend and returns the AI analysis.
    /// When `Config.mockScans` is true, returns realistic canned data instantly.
    /// `tagImage` is an optional close-up of the item's label (#88). When
    /// present it is uploaded alongside the item photo and the backend hands
    /// both to one model call; when absent nothing changes.
    func scan(image: UIImage, tagImage: UIImage? = nil) async throws -> ScanAPIResponse {
        if Config.mockScans {
            return try await mockScan(sharpened: tagImage != nil)
        }
        return try await liveScan(image: image, tagImage: tagImage)
    }

    // ── Mock ──────────────────────────────────────────────────────────────────
    private func mockScan(sharpened: Bool = false) async throws -> ScanAPIResponse {
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
                listingDescription: "Classic Patagonia Better Sweater in great used condition. Light pilling typical of normal wear — no stains, holes, or fading. Retails for $149 new. Ships same day in smoke-free home.",
                confidenceScore: 78,
                confidenceSummary: "The brand and model are clearly legible and the item is common on the secondhand market.",
                confidenceReasons: ["Logo and zip pull visible", "Well-known model with steady demand", "Photo is sharp"],
                quickSalePriceUsd: 45, expectedPriceUsd: 62, bestCasePriceUsd: 90, worstCasePriceUsd: 38,
                valueDrivers: ["Classic neutral colourway", "Patagonia's repair reputation keeps resale strong"],
                assumptions: ["Size M as read from the label", "No hidden damage on the reverse"],
                improveEstimate: ["Photograph the inside care tag", "Show the cuffs where pilling gathers"],
                authenticityAssessment: "Consistent with genuine",
                authenticityReasoning: "Label typography, zip and stitching match the current production run.",
                demand: "steady", supply: "plentiful", conditionGrade: "Good", size: "M", material: "Fleece"
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
                listingDescription: "Authentic Levi's 501 in excellent secondhand condition. Minimal wear with original dark wash intact. Classic fit that never goes out of style.",
                confidenceScore: 66,
                confidenceSummary: "The model is unmistakable; the era and origin are not visible from this angle.",
                confidenceReasons: ["Red tab and back patch visible", "Wash and fit read as modern", "Inside label not shown"],
                quickSalePriceUsd: 28, expectedPriceUsd: 38, bestCasePriceUsd: 55, worstCasePriceUsd: 22,
                valueDrivers: ["501 is the most-searched vintage denim model", "Dark, even wash"],
                assumptions: ["Imported, not made in USA", "Measured waist matches the tag"],
                improveEstimate: ["Photograph the inside label — a made-in-USA or big-E tab changes the range", "Lay flat and show the hem"],
                demand: "high", supply: "plentiful", conditionGrade: "Very good", size: "32x32", era: "2010s", material: "Denim"
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
                listingDescription: "Nike Air Max 90 in good used condition. Some normal creasing on the toe box but soles are clean and cushioning is excellent. Includes original laces.",
                confidenceScore: 54,
                confidenceSummary: "The silhouette is clear, but the colourway and the sole condition drive this price and neither is fully visible.",
                confidenceReasons: ["Air Max 90 silhouette is distinctive", "Colourway partly out of frame", "Sole wear not shown"],
                quickSalePriceUsd: 55, expectedPriceUsd: 72, bestCasePriceUsd: 110, worstCasePriceUsd: 45,
                valueDrivers: ["General-release colourways resell steadily; collaborations far higher"],
                assumptions: ["General release, not a collaboration", "Midsole not yellowed"],
                improveEstimate: ["Photograph the sole and heel tab", "Show the size on the tongue label"],
                authenticityAssessment: "Cannot tell from this photo",
                authenticityReasoning: "The tongue label and box label are the usual tells, and neither is in frame.",
                demand: "steady", supply: "plentiful", conditionGrade: "Good", size: "US 10"
            ),
        ]

        let picked = mocks[Int.random(in: 0..<mocks.count)]
        return sharpened ? Self.sharpened(picked) : picked
    }

    /// What a label close-up buys, for the mock-scans scheme: a tighter range,
    /// higher confidence, and evidence that names the tag. The real thing comes
    /// from the model; this only makes the flow visible in the Simulator.
    private static func sharpened(_ base: ScanAPIResponse) -> ScanAPIResponse {
        let low = base.estValueLowUsd + (base.estValueHighUsd - base.estValueLowUsd) * 0.25
        let high = base.estValueHighUsd - (base.estValueHighUsd - base.estValueLowUsd) * 0.1
        return ScanAPIResponse(
            itemName: base.itemName, brand: base.brand, category: base.category,
            conditionNotes: base.conditionNotes,
            estValueLowUsd: low.rounded(), estValueHighUsd: high.rounded(),
            confidence: "High",
            listingTitle: base.listingTitle, listingDescription: base.listingDescription,
            confidenceScore: min(96, (base.confidenceScore ?? 60) + 17),
            confidenceSummary: "The label confirms the model, size and fabric.",
            confidenceReasons: ["Care tag read: size and composition confirmed",
                                "Style code matches the current production run",
                                "Item photo clear enough to grade condition"],
            quickSalePriceUsd: base.quickSalePriceUsd, expectedPriceUsd: base.expectedPriceUsd,
            bestCasePriceUsd: base.bestCasePriceUsd, worstCasePriceUsd: base.worstCasePriceUsd,
            valueDrivers: base.valueDrivers, assumptions: [],
            uncertaintyFactors: base.uncertaintyFactors,
            improveEstimate: [],
            authenticityAssessment: base.authenticityAssessment,
            authenticityReasoning: base.authenticityReasoning,
            demand: base.demand, supply: base.supply,
            conditionGrade: base.conditionGrade, size: base.size, era: base.era,
            material: base.material)
    }

    // ── Live ──────────────────────────────────────────────────────────────────
    private func liveScan(image: UIImage, tagImage: UIImage? = nil) async throws -> ScanAPIResponse {
        guard let jpegData = await Self.encodeForUpload(image) else {
            throw ScanAPIError.imageEncodingFailed
        }
        // Same longest edge as the item photo: the label's small print is
        // exactly the detail this feature exists to read, so downscaling it
        // further would defeat the point. A failure to encode it is not fatal —
        // the scan proceeds on the item photo alone.
        let tagData = tagImage == nil ? nil : await Self.encodeForUpload(tagImage!)

        let endpoint = Config.baseURL.appendingPathComponent("scan")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        // Retained during rollout: the server falls back to this when
        // attestation isn't enforced yet.
        request.setValue(deviceID, forHTTPHeaderField: "x-device-id")
        await request.attachBearerToken()

        let boundary = "Boundary-\(UUID().uuidString)"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.httpBody = buildMultipart(data: jpegData, tagData: tagData, boundary: boundary)

        let (data, http) = try await request.sendRetryingAuth(on: session)

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
    ///
    /// **EXIF/GPS note.** The early return means a small image is *not*
    /// re-rendered, which looks like it could forward the source photo's EXIF
    /// GPS IFD — the coordinates of wherever the item was photographed, from an
    /// app that never asks for location permission. It does not, and the reason
    /// is upstream of this function: `UIImage(data:)` decodes to a `CGImage`
    /// plus orientation and scale, and does not carry the source's metadata
    /// dictionary at all, so `jpegData(compressionQuality:)` has nothing to
    /// write back. By the time an image reaches here the GPS is already gone.
    ///
    /// This is asserted rather than assumed — `UploadEXIFStrippingTests` builds
    /// a genuine geotagged JPEG with ImageIO and checks both the redraw and the
    /// no-redraw path, with a fixture self-check so the suite cannot pass
    /// vacuously. If a future change ever routes original file `Data` to the
    /// network instead of a re-encoded `UIImage`, those tests are what will
    /// catch it.
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

    private func buildMultipart(data: Data, tagData: Data? = nil, boundary: String) -> Data {
        var body = Data()
        let crlf = "\r\n"
        func part(name: String, filename: String, payload: Data) {
            body.append(Data("--\(boundary)\(crlf)".utf8))
            body.append(Data("Content-Disposition: form-data; name=\"\(name)\"; filename=\"\(filename)\"\(crlf)".utf8))
            body.append(Data("Content-Type: image/jpeg\(crlf)\(crlf)".utf8))
            body.append(payload)
            body.append(Data(crlf.utf8))
        }
        part(name: "file", filename: "scan.jpg", payload: data)
        // The field name the backend reads for the label close-up. Omitted
        // entirely when there is no second photo, so the request is
        // byte-identical to what every earlier version sent.
        if let tagData { part(name: "tag", filename: "tag.jpg", payload: tagData) }
        body.append(Data("--\(boundary)--\(crlf)".utf8))
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
