import Foundation

/// One payment for Pro, as StoreKit recorded it: a purchase or a renewal.
/// A free trial is a payment of zero, which still dates when Pro began.
struct ProPayment: Equatable {
    let date: Date
    let amount: Decimal
    /// ISO 4217, the storefront's currency for that payment.
    let currency: String
}

/// "Since you went Pro": the flip profit made since the first Pro payment,
/// beside what Pro has cost so far, for My Flips.
///
/// Two figures side by side, not a return on Pro: the profit is the user's own
/// work, and nothing here claims Pro caused it. Pure, like `LedgerMath`, so
/// the rules are testable with plain rows.
///
/// Shown only when both figures are in one currency. Pro is billed in the
/// storefront's currency and a flip in whatever it was typed in, the phone has
/// no exchange rate for the ledger (`LedgerMath.Amounts`), and "+400 lei
/// against $39.99" would invite a comparison nobody can make by eye.
struct ProPayback: Equatable {
    let since: Date
    let currency: String
    /// What Pro has cost, summed over every paid period.
    let spent: Decimal
    /// Realised flip profit in `currency` from sales since `since`.
    let profit: Decimal

    /// The profit covers the cost. False while Pro has cost nothing (a trial).
    var hasPaidForItself: Bool { spent > 0 && profit >= spent }

    /// Nil when there is nothing honest to show: no payment, payments in more
    /// than one currency, no paid period yet, or no profit in that currency.
    init?(payments: [ProPayment], results: [ScanResult], now: Date = Date(),
          locale: Locale = .current) {
        guard let first = payments.map(\.date).min(), first < now else { return nil }
        let paid = payments.filter { $0.amount > 0 }
        let currencies = Set(paid.map(\.currency))
        guard currencies.count == 1, let code = currencies.first else { return nil }

        let sold = LedgerMath.sold(results, in: DateInterval(start: first, end: now))
        let profit = LedgerMath.sales(sold, locale: locale).profit.amount(in: code)
        guard profit > 0 else { return nil }

        since = first
        currency = code
        spent = LedgerMath.total(paid.map(\.amount))
        self.profit = profit
    }
}
