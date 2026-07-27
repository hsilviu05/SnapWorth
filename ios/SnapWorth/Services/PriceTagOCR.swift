import Foundation
import Vision
import UIKit

/// On-device price-tag OCR using the platform-native Vision framework. No third-
/// party dependency, and the photo never leaves the device for text recognition.
enum PriceTagOCR {

    enum OCRError: LocalizedError {
        case noImage
        case noPriceFound

        var errorDescription: String? {
            switch self {
            case .noImage:      return "Couldn't read that image."
            case .noPriceFound: return "No price found on the tag."
            }
        }
    }

    /// Reads `image` and returns the most likely shelf price. Throws when nothing
    /// price-like is found so the caller can fall back to manual entry.
    static func detectPrice(in image: UIImage) async throws -> Decimal {
        guard let cg = image.cgImage else { throw OCRError.noImage }
        let observations = try await recognizeText(cg)

        // Prefer the most prominent (tallest) line that parses to a price — on a
        // shelf tag the headline price is almost always the largest text, not a
        // SKU, unit price, or "was" price in small print.
        let priced: [(price: Decimal, height: CGFloat)] = observations.compactMap { obs in
            firstPrice(in: obs.text).map { ($0, obs.height) }
        }
        guard let best = priced.max(by: { $0.height < $1.height }) else {
            throw OCRError.noPriceFound
        }
        return best.price
    }

    // ── Vision ──────────────────────────────────────────────────────────────
    private static func recognizeText(_ cg: CGImage) async throws -> [(text: String, height: CGFloat)] {
        try await withCheckedThrowingContinuation { continuation in
            let request = VNRecognizeTextRequest { req, error in
                if let error { continuation.resume(throwing: error); return }
                let observations = (req.results as? [VNRecognizedTextObservation]) ?? []
                let lines = observations.compactMap { obs -> (String, CGFloat)? in
                    guard let text = obs.topCandidates(1).first?.string else { return nil }
                    return (text, obs.boundingBox.height)
                }
                continuation.resume(returning: lines)
            }
            request.recognitionLevel = .accurate
            request.usesLanguageCorrection = false

            let handler = VNImageRequestHandler(cgImage: cg, options: [:])
            do { try handler.perform([request]) }
            catch { continuation.resume(throwing: error) }
        }
    }

    // ── Parsing (pure + testable) ─────────────────────────────────────────────

    /// A price candidate. `strong` means it had a currency symbol or a decimal
    /// fraction — bare integers are often SKUs, sizes or quantities, so strong
    /// candidates are always preferred over them.
    private struct PriceMatch { let value: Decimal; let strong: Bool }

    /// The best price-looking number in a single OCR line (handles "$12.99",
    /// "Sale 12,99 €", etc.). Prefers a strong candidate; nil when none found.
    static func firstPrice(in line: String) -> Decimal? {
        best(of: matches(in: line))
    }

    /// The most likely price across many OCR lines. Kept separate from
    /// `detectPrice` so parsing is unit-testable without a Vision image.
    static func parsePrice(from lines: [String]) -> Decimal? {
        best(of: lines.flatMap(matches(in:)))
    }

    /// Prefer the largest strong candidate; fall back to the largest bare one.
    private static func best(of candidates: [PriceMatch]) -> Decimal? {
        let strong = candidates.filter(\.strong).map(\.value)
        if let s = strong.max() { return s }
        return candidates.map(\.value).max()
    }

    private static func matches(in line: String) -> [PriceMatch] {
        // Group 1: optional currency symbol. Group 2: the numeric token, which
        // may use , / . as thousands or decimal separators.
        let pattern = #"([$€£])?\s?(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"#
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return [] }

        let ns = line as NSString
        return regex.matches(in: line, range: NSRange(location: 0, length: ns.length)).compactMap { match in
            let token = ns.substring(with: match.range(at: 2))
            guard let value = normalizedDecimal(token), value > 0, value < 100_000 else { return nil }
            let hasSymbol = match.range(at: 1).location != NSNotFound
            return PriceMatch(value: value, strong: hasSymbol || !isInteger(value))
        }
    }

    private static func isInteger(_ value: Decimal) -> Bool {
        var input = value
        var rounded = Decimal()
        NSDecimalRound(&rounded, &input, 0, .down)
        return rounded == value
    }

    /// Normalizes a numeric token to a `Decimal`, resolving thousands vs. decimal
    /// separators for both "1,299.00" (US) and "1.299,00"/"5,99" (EU) styles.
    static func normalizedDecimal(_ token: String) -> Decimal? {
        var t = token.replacingOccurrences(of: " ", with: "")

        if t.contains(",") && t.contains(".") {
            // Both present: the right-most separator is the decimal point.
            if t.lastIndex(of: ",")! > t.lastIndex(of: ".")! {
                t = t.replacingOccurrences(of: ".", with: "")   // 1.299,00 → 1299,00
                t = t.replacingOccurrences(of: ",", with: ".")  //         → 1299.00
            } else {
                t = t.replacingOccurrences(of: ",", with: "")   // 1,299.00 → 1299.00
            }
        } else if t.contains(",") {
            // Only a comma: decimal if 1–2 digits trail (5,99), else thousands (1,299).
            let parts = t.split(separator: ",")
            if parts.count == 2, let last = parts.last, last.count <= 2 {
                t = t.replacingOccurrences(of: ",", with: ".")  // 5,99 → 5.99
            } else {
                t = t.replacingOccurrences(of: ",", with: "")   // 1,299 → 1299
            }
        }
        return Decimal(string: t)
    }
}
