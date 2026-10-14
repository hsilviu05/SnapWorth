import CoreGraphics
import Foundation

// ═══════════════════════════════════════════════════════════════════
// MARK: - Rack scan (#89)
// ═══════════════════════════════════════════════════════════════════
//
// One photo of a rail, shelf or bin; at most three items worth picking up,
// each pinned on the photo. The server decides what may be pinned
// (`backend/rack.py`); this file is the wire shape and the little geometry
// the pins need.

/// `/rack`'s 200 body (`contract/rack-response.json`).
struct RackAPIResponse: Decodable, Equatable {
    let candidates: [RackCandidate]
    let promptVersion: String

    enum CodingKeys: String, CodingKey {
        case candidates
        case promptVersion = "prompt_version"
    }
}

/// One item worth a closer look. Never saved: a rack read is advice for the
/// next minute, and the item it points at becomes a find only when scanned.
struct RackCandidate: Decodable, Equatable, Identifiable {
    /// Where it is, as fractions of the photo from its top left.
    struct Box: Decodable, Equatable {
        let x: Double
        let y: Double
        let width: Double
        let height: Double

        /// The box in a rectangle the photo is drawn into.
        func rect(in frame: CGRect) -> CGRect {
            CGRect(x: frame.minX + x * frame.width, y: frame.minY + y * frame.height,
                   width: width * frame.width, height: height * frame.height)
        }
    }

    let rank: Int
    let box: Box
    let position: String
    let name: String
    let brand: String
    let category: String
    let lowUsd: Double
    let highUsd: Double
    let why: String

    var id: Int { rank }

    enum CodingKeys: String, CodingKey {
        case rank, box, position, name, brand, category, why
        case lowUsd = "low_usd"
        case highUsd = "high_usd"
    }

    /// "$25–$60", in dollars in every language like every estimate.
    var formattedRange: String {
        let fmt = NumberFormatter.snapCurrency
        let lo = fmt.string(from: NSNumber(value: lowUsd)) ?? "$\(Int(lowUsd))"
        let hi = fmt.string(from: NSNumber(value: highUsd)) ?? "$\(Int(highUsd))"
        return "\(lo)–\(hi)"
    }

    /// The brand when it was read, for a line under the name.
    var shownBrand: String? {
        let trimmed = brand.trimmingCharacters(in: .whitespaces)
        return trimmed.isEmpty || trimmed.lowercased() == "unknown" ? nil : trimmed
    }
}

enum RackGeometry {
    /// Where an image of `imageSize` lands when fitted, aspect preserved and
    /// centred, into `container` — the rectangle the pins are placed in.
    static func fittedRect(imageSize: CGSize, in container: CGSize) -> CGRect {
        guard imageSize.width > 0, imageSize.height > 0,
              container.width > 0, container.height > 0 else { return .zero }
        let scale = min(container.width / imageSize.width, container.height / imageSize.height)
        let size = CGSize(width: imageSize.width * scale, height: imageSize.height * scale)
        return CGRect(x: (container.width - size.width) / 2,
                      y: (container.height - size.height) / 2,
                      width: size.width, height: size.height)
    }

    /// `rack_scan_completed`'s `candidates`: the count, which is already a
    /// bucket — the server sends at most three.
    static func countBucket(_ count: Int) -> String {
        String(min(max(count, 0), 3))
    }
}
