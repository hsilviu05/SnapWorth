import Foundation

// ── Money entered by hand ─────────────────────────────────────────────────────

/// Parses an amount typed into a `.decimalPad` field.
///
/// `Double("12,50")` is `nil`, and every money field in the app used to write
/// `Double(newValue)` straight onto the model on each keystroke — so on a
/// German, French, Romanian or Brazilian keypad, whose decimal separator *is*
/// the comma, the amount silently vanished with nothing shown to the user.
///
/// The comma cannot simply be folded to a point, because `$1,250` is a real
/// thing a US user types and folding would read it as 1.25. `normalized`
/// resolves that; the rule is documented there.
enum MoneyInput {
    /// Rewrites a typed amount as a plain `.`-decimal string, or nil when there
    /// is no number in it.
    ///
    /// Which separator is the decimal one:
    ///
    /// - **Both present** — the *last* one is the decimal separator and the
    ///   other is grouping. `1.234,56` is 1234.56 and `1,234.56` is also
    ///   1234.56, which is exactly what each writer meant.
    /// - **Only a point** — it is the decimal separator. `45.5` is 45.5.
    /// - **Only a comma** — ambiguous, and settled by what follows it: exactly
    ///   three digits is grouping (`1,250` → 1250, how a US user writes it),
    ///   one or two is a decimal (`12,50` → 12.50, how most of Europe writes a
    ///   price). The case this gets wrong is a European writing `1,250` for one
    ///   euro twenty-five — three decimal places in a price field, which no
    ///   keypad encourages and no marketplace charges.
    ///
    /// Everything that is not a digit or a separator is dropped, so a pasted
    /// currency symbol costs nothing.
    static func normalized(_ text: String) -> String? {
        let kept = text.filter { $0.isNumber || $0 == "." || $0 == "," }
        guard kept.contains(where: \.isNumber) else { return nil }

        let lastComma = kept.lastIndex(of: ",")
        let lastDot = kept.lastIndex(of: ".")

        let decimalIndex: String.Index?
        switch (lastComma, lastDot) {
        case let (comma?, dot?):
            decimalIndex = comma > dot ? comma : dot
        case (nil, let dot?):
            decimalIndex = dot
        case let (comma?, nil):
            let digitsAfter = kept.distance(from: kept.index(after: comma), to: kept.endIndex)
            decimalIndex = digitsAfter == 3 ? nil : comma
        case (nil, nil):
            decimalIndex = nil
        }

        var out = ""
        var index = kept.startIndex
        while index < kept.endIndex {
            let character = kept[index]
            if character.isNumber {
                out.append(character)
            } else if index == decimalIndex {
                out.append(".")
            }
            // Any other separator is grouping, and is dropped.
            index = kept.index(after: index)
        }
        return out
    }

    /// Nil for empty or unparseable input — the caller keeps the previous
    /// value rather than clobbering it with a zero.
    static func parse(_ text: String) -> Double? {
        normalized(text).flatMap(Double.init)
    }

    /// Same rules, as a `Decimal` — money math elsewhere in the app avoids
    /// binary floating point.
    static func decimal(_ text: String) -> Decimal? {
        normalized(text).flatMap { Decimal(string: $0) }
    }
}

// ── Marketplace fees ──────────────────────────────────────────────────────────

/// One marketplace's seller-side fees. `sellingFeePercent` is a fraction
/// (0.1325 == 13.25%); `fixedFee` is a flat per-sale charge in USD.
struct MarketplaceFee: Equatable {
    let sellingFeePercent: Decimal
    let fixedFee: Decimal
    /// Some marketplaces replace the percentage with a flat charge on cheap
    /// sales — Poshmark takes $2.95 on anything under $15 instead of 20%.
    /// Nil means the percentage and fixed fee always apply.
    var lowPriceFlatFee: LowPriceFlatFee? = nil

    struct LowPriceFlatFee: Equatable {
        /// Sales strictly below this price pay `fee` and nothing else.
        let below: Decimal
        let fee: Decimal
    }

    /// Seller-side fees on a sale at `resale`.
    func fees(on resale: Decimal) -> Decimal {
        if let low = lowPriceFlatFee, resale < low.below {
            return low.fee
        }
        return resale * sellingFeePercent + fixedFee
    }
}

/// ─────────────────────────────────────────────────────────────────────────────
///  MARKETPLACE FEE TABLE  —  ⚠️ UPDATE RATES HERE
///
///  Fees are approximate, in USD, and **change over time**. That is exactly why
///  they live in this one table instead of being hardcoded inside the profit
///  calculator: a stale number here silently produces a wrong buy/skip verdict.
///  Re-check each marketplace's published rates periodically.
///
///  The table can also be overridden at runtime — without an app release — via
///  the `overrideKey` UserDefaults entry (see `fee(for:)`), so a future backend
///  push can keep fees current between updates.
/// ─────────────────────────────────────────────────────────────────────────────
enum MarketplaceFees {
    /// Shipped defaults. Percentages are the marketplace's seller commission;
    /// buyer-paid fees (e.g. Vinted Buyer Protection) are intentionally excluded
    /// because they don't come out of the seller's proceeds.
    // Built from string literals, not Double, so the money math stays exact
    // (Decimal(0.1325) would capture the Double's rounding error).
    static let defaults: [Marketplace: MarketplaceFee] = [
        // eBay: 13.25% final value fee + $0.40 per order (most categories).
        // Source: ebay.com/help/selling/fees/selling-fees
        .ebay:     MarketplaceFee(sellingFeePercent: Decimal(string: "0.1325")!, fixedFee: Decimal(string: "0.40")!),
        // Poshmark (US): flat $2.95 on sales under $15, 20% at $15 and above.
        // No fixed fee — the shipping label is prepaid and paid by the buyer.
        // Source: poshmark.com/fees ("How Poshmark fees work"), re-checked
        // against 2026 seller-fee guides before shipping.
        .poshmark: MarketplaceFee(sellingFeePercent: Decimal(string: "0.20")!, fixedFee: 0,
                                  lowPriceFlatFee: .init(below: 15, fee: Decimal(string: "2.95")!)),
        // Mercari (US): flat 10% selling fee since January 2025, when the
        // separate 2.9% + $0.50 processing fee was removed. The 3.6% buyer
        // protection fee is paid by the buyer and excluded here.
        // Source: mercari.com/us/help_center/article/169 ("Fees on Mercari")
        .mercari:  MarketplaceFee(sellingFeePercent: Decimal(string: "0.10")!, fixedFee: 0),
        // Depop (US): 0% selling fee since July 2024; payment processing of
        // 3.3% + $0.45 remains. Depop charges it on item + shipping; applied to
        // the item price here, which understates it by 3.3% of postage on
        // shipped sales — conservative in the seller's favour by cents.
        // Source: depop.com/sell/fees (US)
        .depop:    MarketplaceFee(sellingFeePercent: Decimal(string: "0.033")!, fixedFee: Decimal(string: "0.45")!),
        .vinted:   MarketplaceFee(sellingFeePercent: 0, fixedFee: 0),
        .facebook: MarketplaceFee(sellingFeePercent: 0, fixedFee: 0),
        .olx:      MarketplaceFee(sellingFeePercent: 0, fixedFee: 0),
    ]

    /// UserDefaults key holding an optional override table (JSON). A future
    /// remote-config fetch can write here to update fees without shipping a build.
    static let overrideKey = "snapworth_fee_table_override"

    /// Fee for a marketplace: a valid runtime override if present, else the
    /// shipped default. Returns nil only if an entry is genuinely missing — the
    /// calculator surfaces that as "fees unknown" rather than guessing.
    static func fee(for marketplace: Marketplace) -> MarketplaceFee? {
        overrideTable[marketplace] ?? defaults[marketplace]
    }

    /// One marketplace's entry in the override JSON.
    ///
    /// Shape, matching the website's mirror of the same table
    /// (`website/index.html`, the `FEES` object):
    /// `{ "ebay": { "pct": "0.1325", "fixed": "0.40" },
    ///    "poshmark": { "pct": "0.20", "fixed": "0",
    ///                  "flatBelow": "15", "flatFee": "2.95" } }`
    ///
    /// Every field is optional: an entry replaces only what it names and keeps
    /// the shipped default for the rest. That is the fix for a silent
    /// money bug — the decoder used to build
    /// `MarketplaceFee(sellingFeePercent:fixedFee:)` and nothing else, so
    /// `lowPriceFlatFee` fell back to its `nil` default. Because `fee(for:)`
    /// prefers an override over the default outright, a push that touched
    /// Poshmark's percentage *deleted* the "$2.95 under $15" rule, and a $10
    /// sale started being charged 20% ($2.00) instead of $2.95. The old wire
    /// shape could not express the rule at all.
    ///
    /// To remove a low-price rule on purpose, send `"flatBelow": 0`.
    private struct FeeOverride: Decodable {
        let pct: DecimalValue?
        let fixed: DecimalValue?
        let flatBelow: DecimalValue?
        let flatFee: DecimalValue?
    }

    /// A rate or amount from the override JSON, decoded **exactly**.
    ///
    /// `defaults` above is built from string literals with a comment saying
    /// why: "so the money math stays exact (Decimal(0.1325) would capture the
    /// Double's rounding error)". The override decoder then did precisely
    /// that — `Decimal(pct)` from a `Double` — so a pushed 0.1325 became
    /// 0.132500000000000006661338147750939242541790008544921875 and every
    /// fee computed from it carried the error.
    ///
    /// A string is the preferred wire form and is parsed exactly. A JSON
    /// number is accepted for compatibility with the website's shape, via
    /// `Double.description` — documented to be the shortest string that
    /// round-trips to the same value, so `0.1325` comes back as `"0.1325"`
    /// and parses exactly.
    private struct DecimalValue: Decodable {
        let value: Decimal

        init(from decoder: Decoder) throws {
            let container = try decoder.singleValueContainer()
            if let text = try? container.decode(String.self) {
                guard let parsed = Decimal(string: text.trimmingCharacters(
                    in: .whitespaces)) else {
                    throw DecodingError.dataCorruptedError(
                        in: container, debugDescription: "unparseable amount")
                }
                value = parsed
                return
            }
            let number = try container.decode(Double.self)
            guard number.isFinite,
                  let parsed = Decimal(string: "\(number)") else {
                throw DecodingError.dataCorruptedError(
                    in: container, debugDescription: "unusable number")
            }
            value = parsed
        }
    }

    /// Optional override table cached in UserDefaults. A bad push can never
    /// reach the math, at one of two granularities:
    ///
    /// * a value of the wrong **type** fails the decode, so the whole push is
    ///   discarded — half a fee table is never applied;
    /// * a value of the right type but out of **range** discards only its own
    ///   entry, so one bad marketplace does not cost the others.
    static var overrideTable: [Marketplace: MarketplaceFee] {
        guard let data = UserDefaults.standard.data(forKey: overrideKey),
              let raw = try? JSONDecoder().decode(
                [String: FeeOverride].self, from: data)
        else { return [:] }

        var table: [Marketplace: MarketplaceFee] = [:]
        for (key, entry) in raw {
            guard let marketplace = Marketplace(rawValue: key),
                  let base = defaults[marketplace],
                  let resolved = merged(base, with: entry) else { continue }
            table[marketplace] = resolved
        }
        return table
    }

    /// `base` with the fields `entry` names replaced. Nil rejects the entry.
    private static func merged(_ base: MarketplaceFee,
                               with entry: FeeOverride) -> MarketplaceFee? {
        let pct = entry.pct?.value ?? base.sellingFeePercent
        let fixed = entry.fixed?.value ?? base.fixedFee
        guard pct >= 0, pct < 1, fixed >= 0 else { return nil }

        var flat = base.lowPriceFlatFee
        if entry.flatBelow != nil || entry.flatFee != nil {
            let below = entry.flatBelow?.value ?? 0
            let fee = entry.flatFee?.value ?? 0
            guard below >= 0, fee >= 0 else { return nil }
            if below == 0 {
                flat = nil                  // deliberate removal
            } else if fee >= below {
                // A flat charge at or above its own threshold means every sale
                // under it nets the seller nothing or less. That is a bad push,
                // not a fee.
                return nil
            } else {
                flat = MarketplaceFee.LowPriceFlatFee(below: below, fee: fee)
            }
        }
        return MarketplaceFee(sellingFeePercent: pct, fixedFee: fixed,
                              lowPriceFlatFee: flat)
    }
}

// ── Profit math ───────────────────────────────────────────────────────────────

/// Result of a thrift-flip profit calculation. All money is exact `Decimal`.
struct FlipCalculation: Equatable {
    let resalePrice: Decimal
    let purchasePrice: Decimal
    let shippingCost: Decimal
    let platformFees: Decimal
    let netProfit: Decimal
    /// Profit ÷ resale price. Nil when resale is 0.
    let margin: Decimal?
    /// Profit ÷ purchase price (ROI). Nil when purchase is 0 (free find / undefined).
    let roi: Decimal?
    /// True when the fee table had no entry and fees were assumed 0 — the UI
    /// must warn the verdict is fee-blind rather than present it as certain.
    let feesUnknown: Bool

    var isProfitable: Bool { netProfit > 0 }
}

enum FlipMath {
    /// `netProfit = resale − platformFees − shipping − purchase`, where
    /// `platformFees = fee.fees(on: resale)` — normally
    /// `resale × sellingFeePercent + fixedFee`, or a marketplace's flat
    /// low-price charge where one applies (see `MarketplaceFee`).
    ///
    /// A nil `fee` (missing table entry) means fees are assumed 0 and
    /// `feesUnknown` is set so the caller can flag the verdict as incomplete.
    static func calculate(resalePrice: Decimal,
                          purchasePrice: Decimal,
                          shippingCost: Decimal,
                          fee: MarketplaceFee?) -> FlipCalculation {
        let resale = max(0, resalePrice)
        let purchase = max(0, purchasePrice)
        let shipping = max(0, shippingCost)

        let platformFees: Decimal = fee.map { $0.fees(on: resale) } ?? 0
        let net = resale - platformFees - shipping - purchase

        return FlipCalculation(
            resalePrice: resale,
            purchasePrice: purchase,
            shippingCost: shipping,
            platformFees: platformFees,
            netProfit: net,
            margin: resale > 0 ? net / resale : nil,
            roi: purchase > 0 ? net / purchase : nil,
            feesUnknown: fee == nil
        )
    }
}
