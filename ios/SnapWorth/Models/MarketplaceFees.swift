import Foundation

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

    /// Optional override table cached in UserDefaults. Shape:
    /// `{ "ebay": { "pct": 0.13, "fixed": 0.40 }, ... }`. Any malformed or
    /// out-of-range entry is ignored in favour of the default, so a bad push can
    /// never break the math.
    static var overrideTable: [Marketplace: MarketplaceFee] {
        guard let data = UserDefaults.standard.data(forKey: overrideKey),
              let raw = try? JSONDecoder().decode([String: [String: Double]].self, from: data)
        else { return [:] }

        var table: [Marketplace: MarketplaceFee] = [:]
        for (key, values) in raw {
            guard let marketplace = Marketplace(rawValue: key),
                  let pct = values["pct"], let fixed = values["fixed"],
                  pct >= 0, pct < 1, fixed >= 0 else { continue }
            table[marketplace] = MarketplaceFee(sellingFeePercent: Decimal(pct), fixedFee: Decimal(fixed))
        }
        return table
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
