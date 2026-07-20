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

    // ── Summary ────────────────────────────────────────────────────────────────

    struct Summary {
        var realizedProfit: Decimal = 0
        var itemsSold: Int = 0
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

        s.itemsSold = scopedSold.count
        s.realizedProfit = scopedSold.compactMap(\.realizedProfit).reduce(0, +)

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

    func renderMonthCard(_ all: [ScanResult], displayScale: CGFloat) -> UIImage? {
        guard hasSalesThisMonth(all) else { return nil }
        let s = summary(all, scope: .month)
        let card = MonthShareCardView(
            monthTitle: Self.monthYearLabel(Date()),
            realizedProfit: s.realizedProfit,
            itemsSold: s.itemsSold,
            bestFlipName: s.bestFlip?.itemName,
            bestFlipProfit: s.bestFlip?.realizedProfit
        )
        let renderer = ImageRenderer(content: card)
        renderer.scale = max(displayScale, 2)
        return renderer.uiImage
    }

    // ── CSV export ──────────────────────────────────────────────────────────────

    /// Plain UTF-8 CSV of sold flips for the user's bookkeeping. RFC-4180 quoting.
    /// Columns: Date, Item, Paid, Sold, Fees, Profit, ROI.
    func csv(_ all: [ScanResult]) -> String {
        let sold = all.filter { $0.status == .sold }
            .sorted { ($0.soldDate ?? $0.timestamp) < ($1.soldDate ?? $1.timestamp) }

        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withFullDate]

        var rows = ["Date,Item,Paid,Sold,Fees,Profit,ROI"]
        for r in sold {
            let date = r.soldDate.map { iso.string(from: $0) } ?? ""
            let paid = r.paidPrice.map { Self.decimalString($0) } ?? ""
            let soldStr = r.soldPrice.map { Self.decimalString($0) } ?? ""
            let fees = r.feesEstimate.map { Self.decimalString($0) } ?? ""
            let profit = r.realizedProfit.map { Self.decimalString($0) } ?? ""
            let roi = r.roi.map { Self.roiPercentPlain($0) } ?? ""
            let cols = [date, r.itemName, paid, soldStr, fees, profit, roi].map(Self.csvEscape)
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

    private static func decimalString(_ value: Double) -> String {
        String(format: "%.2f", value)
    }

    private static func decimalString(_ value: Decimal) -> String {
        NSDecimalNumber(decimal: value).stringValue
    }

    private static func roiPercentPlain(_ fraction: Decimal) -> String {
        let value = Int((NSDecimalNumber(decimal: fraction).doubleValue * 100).rounded())
        return "\(value)%"
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
}
