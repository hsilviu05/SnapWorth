import Foundation
import ImageIO
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
        let observations = try await recognizeText(
            cg, orientation: CGImagePropertyOrientation(image.imageOrientation))

        // Prefer the most prominent (tallest) line that parses to a price — on a
        // shelf tag the headline price is almost always the largest text, not a
        // SKU, unit price, or "was" price in small print.
        //
        // But *strength* outranks height. This used to call `firstPrice`, which
        // returns a bare `Decimal` and throws the strong/weak flag away, so the
        // cross-line comparison was by glyph height alone — abandoning the rule
        // stated below exactly where the tallest-text heuristic is weakest. On
        // a clearance tag whose largest text is "70% OFF" it returned 70 with a
        // symbol-bearing $12.99 sitting in the same image; on a Goodwill tag
        // printing the size largest, "SIZE 14" beat the price.
        let priced: [(match: PriceMatch, height: CGFloat)] = observations.compactMap { obs in
            bestMatch(of: matches(in: obs.text)).map { ($0, obs.height) }
        }
        guard let best = priced.max(by: { a, b in
            if a.match.strong != b.match.strong { return !a.match.strong }
            return a.height < b.height
        }) else {
            throw OCRError.noPriceFound
        }
        return best.match.value
    }

    // ── Vision ──────────────────────────────────────────────────────────────
    private static func recognizeText(
        _ cg: CGImage, orientation: CGImagePropertyOrientation
    ) async throws -> [(text: String, height: CGFloat)] {
        // No completion handler. There used to be one, and it made two
        // independent paths resume the same continuation: Vision reports a
        // failed request through *both* channels — it invokes the request's
        // `completionHandler` with the error and then throws that same error
        // out of `perform`. The handler runs synchronously inside `perform`, so
        // the order was resume-with-error, then throw, then a second resume of
        // an already-resumed `CheckedContinuation`, which is a hard trap
        // (`SWIFT TASK CONTINUATION MISUSE`) and not a catchable error.
        //
        // So any Vision failure on a tag photo took the app down instead of
        // reaching `readPriceTag`'s "Couldn't read the tag — enter the price
        // manually" — the fallback the whole feature rests on, unreachable on
        // the one path it exists for.
        //
        // `request.results` is populated by the time `perform` returns, so
        // reading it afterwards needs no handler and leaves exactly one resume
        // per outcome. The continuation itself is kept so the threading is
        // unchanged: the body ran synchronously on the caller before too.
        try await withCheckedThrowingContinuation { continuation in
            let request = VNRecognizeTextRequest()
            request.recognitionLevel = .accurate
            request.usesLanguageCorrection = false

            let handler = VNImageRequestHandler(
                cgImage: cg, orientation: orientation, options: [:])
            do {
                try handler.perform([request])
                // `results` on a `VNRecognizeTextRequest` is already
                // `[VNRecognizedTextObservation]?`, so the conditional
                // downcast did nothing but emit a warning.
                let observations = request.results ?? []
                continuation.resume(returning: observations.compactMap { obs -> (String, CGFloat)? in
                    guard let text = obs.topCandidates(1).first?.string else { return nil }
                    return (text, obs.boundingBox.height)
                })
            } catch {
                continuation.resume(throwing: error)
            }
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
        bestMatch(of: candidates)?.value
    }

    /// Same choice as `best`, keeping the strength so a caller comparing across
    /// lines can rank on it. `detectPrice` needs that; `best` throws it away.
    private static func bestMatch(of candidates: [PriceMatch]) -> PriceMatch? {
        let strong = candidates.filter(\.strong)
        if !strong.isEmpty { return strong.max { $0.value < $1.value } }
        return candidates.max { $0.value < $1.value }
    }

    /// Space characters that group thousands in print: plain, no-break
    /// (U+00A0) and narrow no-break (U+202F). A French, Nordic or Polish tag
    /// prints 1299 as "1 299", usually with one of the latter two.
    private static let groupingSpaces = " \u{00A0}\u{202F}"

    private static func matches(in line: String) -> [PriceMatch] {
        // Group 1: optional leading currency symbol. Group 2: the numeric
        // token. Group 3: optional *trailing* symbol — "12,99 €" is how most of
        // Europe prints a price, and the docstring above already claimed to
        // handle it while nothing read it, so those tags were never `strong`.
        //
        // The grouping alternative now also admits the space class, so "1 299"
        // is one token. It was two before: `\s?` sat outside group 2, which let
        // the scanner restart cleanly on the second group and return 299 for a
        // €1299 item. The exact-three-digit requirement stays, so
        // "SKU 004821 12.99" is not fused.
        let pattern = #"([$€£])?\s?(\d{1,3}(?:[.,\#(groupingSpaces)]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?:\s?([$€£]))?"#
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return [] }

        let ns = line as NSString
        return regex.matches(in: line, range: NSRange(location: 0, length: ns.length)).compactMap { match in
            let tokenRange = match.range(at: 2)
            let token = ns.substring(with: tokenRange)
            guard !isRateOrPercentage(tokenRange, in: ns) else { return nil }
            guard let value = normalizedDecimal(token), value > 0, value < 100_000 else { return nil }
            let hasSymbol = match.range(at: 1).location != NSNotFound
                || match.range(at: 3).location != NSNotFound
            return PriceMatch(value: value, strong: hasSymbol || hasFractionTail(token))
        }
    }

    /// True for a number that is part of a percentage or a per-unit rate.
    ///
    /// "70% OFF" on a clearance tag, "100% COTTON" on a garment label and
    /// "$1.99/oz" on a shelf label were all first-class price candidates, and
    /// the first two are routinely the largest text in the photo.
    ///
    /// Both sides of the slash are rejected — a rate's denominator is no more a
    /// price than its numerator, and "12/25" is a date. Judged around the
    /// *token* rather than the whole match, because the character before "$5"
    /// in "Buy 2/$5" is a slash and that is still a price.
    private static func isRateOrPercentage(_ token: NSRange, in line: NSString) -> Bool {
        let end = token.location + token.length
        if end < line.length {
            let rest = line.substring(from: end)
                .drop(while: { $0 == " " })
            if rest.hasPrefix("%") || rest.hasPrefix("/") { return true }
        }
        if token.location > 0,
           line.substring(with: NSRange(location: token.location - 1, length: 1)) == "/" {
            return true
        }
        return false
    }

    /// True when the token itself carries a one- or two-digit fraction.
    ///
    /// Strength used to be `!isInteger(value)` — derived from the parsed
    /// *value*, while the rule it implements is about the token *text*. The two
    /// disagree whenever the fraction is zero: "19.00" is an unambiguous price
    /// signal that parses to the integer 19, so it was classed weak and lost to
    /// any larger bare number on the line. On a jeans tag read as one
    /// observation, "W32 L34 19.00" returned 34 — waist and length numbers
    /// routinely exceed thrift prices.
    ///
    /// Anchored at the end and limited to one or two digits, so a *grouped*
    /// token like "1.299" is still an integer price rather than a fraction.
    private static func hasFractionTail(_ token: String) -> Bool {
        token.range(of: #"[.,]\d{1,2}$"#, options: .regularExpression) != nil
    }

    /// Normalizes a numeric token to a `Decimal`, resolving thousands vs. decimal
    /// separators for both "1,299.00" (US) and "1.299,00"/"5,99" (EU) styles.
    ///
    /// There was a branch for both-separators and a branch for comma-only, and
    /// **none for dot-only** — so a dot-only token fell through to
    /// `Decimal(string:)`, which reads the dot as a decimal point. That
    /// contradicted the regex directly: its grouping alternative was written to
    /// match `.` as a *thousands* separator, so the matcher said "thousands"
    /// and the normalizer said "decimal". "€1.299" came back as 1.299, and
    /// because 1.299 is non-integral it was also classed `strong`, guaranteeing
    /// it beat every honest candidate on the tag. The flip verdict was then
    /// computed against a $1.30 cost basis for a €1299 item.
    ///
    /// It fires in a US store too, whenever Vision reads the comma in "$1,299"
    /// as a period — routine on a low-contrast tag.
    ///
    /// The rule is now one rule, applied to whichever separator appears, and
    /// it is the same one `MoneyInput.normalized` documents for hand-typed
    /// money: with a single separator, exactly three trailing digits is
    /// grouping and one or two is a decimal; more than one occurrence of the
    /// same separator is grouping throughout.
    static func normalizedDecimal(_ token: String) -> Decimal? {
        var t = token
        for space in groupingSpaces { t = t.replacingOccurrences(of: String(space), with: "") }

        let hasComma = t.contains(","), hasDot = t.contains(".")
        if hasComma && hasDot {
            // Both present: the right-most separator is the decimal point.
            if t.lastIndex(of: ",")! > t.lastIndex(of: ".")! {
                t = t.replacingOccurrences(of: ".", with: "")   // 1.299,00 → 1299,00
                t = t.replacingOccurrences(of: ",", with: ".")  //         → 1299.00
            } else {
                t = t.replacingOccurrences(of: ",", with: "")   // 1,299.00 → 1299.00
            }
        } else if hasComma || hasDot {
            let separator: Character = hasComma ? "," : "."
            let parts = t.split(separator: separator, omittingEmptySubsequences: false)
            if parts.count == 2, let tail = parts.last, tail.count <= 2 {
                // 5,99 / 12.99 — a decimal fraction.
                t = parts.joined(separator: ".")
            } else {
                // 1,299 / 1.299 / 1.500.000 — grouping throughout.
                t = parts.joined()
            }
        }
        return Decimal(string: t)
    }
}


/// Vision reads a `CGImage`, which is the raw sensor bitmap. `UIImage` keeps the
/// camera's rotation in `imageOrientation` and never turns the pixels, so the
/// two disagree for every photo this app takes: it is portrait-only, a back
/// camera capture arrives `.right`, and the bitmap underneath is landscape with
/// the tag on its side.
///
/// Handing that to Vision as `.up` — which is what omitting the argument does —
/// presents the tag a quarter turn out. `VNRecognizeTextRequest` corrects skew,
/// not quarter turns, so the reader failed on essentially every photo taken with
/// the camera, which is its primary and intended input. The user tapped "read
/// the tag", waited for accurate-level OCR, and got the manual-entry fallback.
///
/// Passing the orientation rather than redrawing: a redraw costs a full
/// resolution copy to achieve the same thing. It also fixes the "tallest line
/// wins" heuristic for free — Vision reports `boundingBox` in the *oriented*
/// space once told, so `height` starts measuring the axis the code always meant.
///
/// Internal, not private, so the mapping is testable. A transposed case here is
/// the classic way this is got wrong, and it is silent.
extension CGImagePropertyOrientation {
    init(_ orientation: UIImage.Orientation) {
        switch orientation {
        case .up:            self = .up
        case .upMirrored:    self = .upMirrored
        case .down:          self = .down
        case .downMirrored:  self = .downMirrored
        case .left:          self = .left
        case .leftMirrored:  self = .leftMirrored
        case .right:         self = .right
        case .rightMirrored: self = .rightMirrored
        @unknown default:    self = .up
        }
    }
}
