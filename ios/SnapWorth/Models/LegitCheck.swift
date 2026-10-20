import Foundation
import SwiftUI

// ═══════════════════════════════════════════════════════════════════
// MARK: - Legit check (#90)
// ═══════════════════════════════════════════════════════════════════
//
// Two to four guided close-ups of one item, read by `/legit` for the usual
// signs of a replica. The server holds the prompt and the closed verdict set
// (`backend/scan/legit.py`); this file holds what the app says about each verdict,
// the photos it asks for, and which brands it offers the check for.
//
// The wording rule is legal, not style: everything here is an observation
// about photos, never a certification. No verdict says "authentic", "genuine"
// or "fake", and every read is shown with `LegitVerdict.disclaimer`. The
// share card never carries a read — see `ScanResult.legitCheckData`.

/// `verdict` — `legit.VERDICTS`. A value this build does not know is
/// `cannotTell`, which is also what the server sends when it is unsure.
enum LegitVerdict: String, CaseIterable, Codable {
    case consistent
    case someConcerns  = "some_concerns"
    case likelyReplica = "likely_replica"
    case cannotTell    = "cannot_tell"

    init(serverValue raw: String) {
        self = LegitVerdict(rawValue: raw.trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()) ?? .cannotTell
    }

    /// The headline. What the photos showed, never what the item is.
    var title: String {
        switch self {
        case .consistent:
            return String(localized: "No signs of a replica found", comment: "Legit check verdict")
        case .someConcerns:
            return String(localized: "Some details look off", comment: "Legit check verdict")
        case .likelyReplica:
            return String(localized: "Shows signs of a replica", comment: "Legit check verdict")
        case .cannotTell:
            return String(localized: "Couldn't tell from these photos", comment: "Legit check verdict")
        }
    }

    /// One sentence under the headline.
    var explanation: String {
        switch self {
        case .consistent:
            return String(localized: "The details we could see match how this brand's genuine items are usually made.")
        case .someConcerns:
            return String(localized: "One or more details differ from what genuine items usually show. Look closer before you pay.")
        case .likelyReplica:
            return String(localized: "Several details are typical of replicas.")
        case .cannotTell:
            return String(localized: "The photos didn't show enough to judge. Closer, sharper shots in good light help.")
        }
    }

    var systemImage: String {
        switch self {
        case .consistent:    return "checkmark.circle.fill"
        case .someConcerns:  return "exclamationmark.circle.fill"
        case .likelyReplica: return "xmark.octagon.fill"
        case .cannotTell:    return "questionmark.circle.fill"
        }
    }

    var tint: Color {
        switch self {
        case .consistent:    return .snapSageText
        case .someConcerns:  return .snapAmber
        case .likelyReplica: return .snapTerracottaText
        case .cannotTell:    return .snapWarmGray
        }
    }

    /// Shown under every read, whatever it says.
    static var disclaimer: String {
        String(localized: "An observation about these photos, not a certification of authenticity.")
    }
}

/// The close-ups a check asks for, in the order it asks. The raw values are
/// the multipart field names `/legit` reads and the values `unclear_photos`
/// may carry (`prompts.LEGIT_PHOTO_LABELS`).
enum LegitPhotoSlot: String, CaseIterable, Codable, Identifiable {
    case logo
    case tag
    case stitching
    case hardware

    var id: String { rawValue }

    static let minimumPhotos = 2

    var title: String {
        switch self {
        case .logo:      return String(localized: "Logo close-up", comment: "Legit check step")
        case .tag:       return String(localized: "Inside label", comment: "Legit check step")
        case .stitching: return String(localized: "Stitching", comment: "Legit check step")
        case .hardware:  return String(localized: "Zipper or hardware", comment: "Legit check step")
        }
    }

    var instruction: String {
        switch self {
        case .logo:
            return String(localized: "Fill the frame with the logo or wordmark. Sharp edges matter more than the whole item.")
        case .tag:
            return String(localized: "The care or size label, flat and in focus, so the small print is readable.")
        case .stitching:
            return String(localized: "A seam, or the stitching around the logo, close enough to see each stitch.")
        case .hardware:
            return String(localized: "A zipper pull, snap or button, with any engraving facing the camera.")
        }
    }

    /// The faint example inside the guide.
    var systemImage: String {
        switch self {
        case .logo:      return "textformat"
        case .tag:       return "tag"
        case .stitching: return "scribble"
        case .hardware:  return "smallcircle.filled.circle"
        }
    }

    /// The guide's size: wide for a label, squarer for hardware.
    var guideSize: CGSize {
        switch self {
        case .logo:      return CGSize(width: 280, height: 180)
        case .tag:       return CGSize(width: 300, height: 190)
        case .stitching: return CGSize(width: 280, height: 210)
        case .hardware:  return CGSize(width: 220, height: 220)
        }
    }
}

/// A finished check, as kept on the find (`ScanResult.legitCheckData`).
struct LegitRead: Codable, Equatable {
    var verdict: LegitVerdict
    var reasons: [String]
    var unclearPhotos: [LegitPhotoSlot]
    var checkedAt: Date

    init(verdict: LegitVerdict, reasons: [String], unclearPhotos: [LegitPhotoSlot],
         checkedAt: Date = .now) {
        self.verdict = verdict
        self.reasons = reasons
        self.unclearPhotos = unclearPhotos
        self.checkedAt = checkedAt
    }

    init(response: LegitAPIResponse, checkedAt: Date = .now) {
        self.init(verdict: LegitVerdict(serverValue: response.verdict),
                  reasons: response.reasons,
                  unclearPhotos: response.unclearPhotos.compactMap(LegitPhotoSlot.init(rawValue:)),
                  checkedAt: checkedAt)
    }

    func encoded() -> Data? { try? JSONEncoder().encode(self) }

    static func decode(_ data: Data?) -> LegitRead? {
        guard let data else { return nil }
        return try? JSONDecoder().decode(LegitRead.self, from: data)
    }
}

/// `/legit`'s 200 body (`contract/legit-response.json`).
struct LegitAPIResponse: Decodable, Equatable {
    let verdict: String
    let reasons: [String]
    let unclearPhotos: [String]
    let promptVersion: String

    enum CodingKeys: String, CodingKey {
        case verdict, reasons
        case unclearPhotos = "unclear_photos"
        case promptVersion = "prompt_version"
    }
}

/// The brands the check is offered for: the ones most often faked in the
/// secondhand market, and the ones whose construction a model is likely to
/// know well enough to compare against. Everything else gets no card — a
/// check of an unbranded sweater would only ever come back "Couldn't tell".
enum LegitBrands {
    /// Lower case, without accents or punctuation; matched as whole words.
    static let commonlyFaked: [String] = [
        "nike", "jordan", "adidas", "yeezy", "new balance", "converse", "vans",
        "the north face", "north face", "patagonia", "arcteryx", "canada goose",
        "moncler", "stone island", "carhartt", "ralph lauren", "polo ralph lauren",
        "tommy hilfiger", "lacoste", "burberry", "barbour", "levis", "lululemon",
        "supreme", "off white", "bape", "a bathing ape", "stussy", "palace",
        "champion",
        "coach", "michael kors", "kate spade", "tory burch", "longchamp", "mcm",
        "louis vuitton", "gucci", "chanel", "prada", "hermes", "dior",
        "christian dior", "fendi", "saint laurent", "yves saint laurent", "ysl",
        "balenciaga", "versace", "givenchy", "celine", "bottega veneta", "loewe",
        "goyard", "miu miu", "valentino", "moschino",
        "dr martens", "ugg", "timberland", "birkenstock", "golden goose",
        "christian louboutin", "louboutin",
        "ray ban", "oakley", "rolex", "omega", "cartier", "tag heuer", "tiffany",
        "pandora", "swarovski", "apple", "beats",
    ]

    static func normalised(_ text: String) -> String {
        let folded = text.folding(options: [.caseInsensitive, .diacriticInsensitive],
                                  locale: Locale(identifier: "en_US_POSIX"))
        let lettersOnly = folded.unicodeScalars.map { scalar -> Character in
            if CharacterSet.alphanumerics.contains(scalar) { return Character(scalar) }
            // "Levi's" is "levis" and "Off-White" is "off white".
            return scalar == "'" || scalar == "’" ? "\u{0}" : " "
        }
        return String(lettersOnly)
            .replacingOccurrences(of: "\u{0}", with: "")
            .split(separator: " ")
            .joined(separator: " ")
    }

    /// Whether `brand`, as the scan named it, is one the check is offered for.
    static func isCommonlyFaked(_ brand: String) -> Bool {
        let name = " \(normalised(brand)) "
        guard name.trimmingCharacters(in: .whitespaces) != "unknown" else { return false }
        return commonlyFaked.contains { name.contains(" \($0) ") }
    }
}
