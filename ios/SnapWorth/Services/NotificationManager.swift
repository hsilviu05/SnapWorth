import Foundation
import UserNotifications
import SwiftData

// Deep-link routes fired when a notification is tapped. Mirrors the existing
// widget deep-link pattern (NotificationCenter → MainTabView switches tab).
extension Notification.Name {
    static let snapOpenFlips    = Notification.Name("snapOpenFlips")
    static let snapOpenSettings = Notification.Name("snapOpenSettings")
}

/// The single entry point for all local notifications. Centralizing scheduling
/// here keeps permission state, per-category opt-outs, the global daily cap, and
/// identifier schemes from drifting across the codebase.
///
/// Everything is LOCAL (UserNotifications only) — no push server, no new
/// dependency, and nothing is collected off-device, so this adds no App Privacy
/// disclosure.
///
/// ## Categories, triggers, identifiers
/// | Category | Trigger                                   | Identifier            |
/// |----------|-------------------------------------------|-----------------------|
/// | recap    | ≥3 scans this month → 1st of next mo 10:00 | `recap.monthly`       |
/// | ledger   | item marked *listed* → +14 days 10:00      | `ledger.day.<yyyymmdd>` (coalesced per fire-day) |
/// | trial    | ~24h before trial end                      | `trial.ending`        |
/// | freeScan | opt-in; next day without a scan, at the user's hour | `freeScan.daily` |
///
/// Recap/trial use fixed identifiers so re-scheduling replaces rather than
/// duplicates. Ledger coalesces every follow-up landing on the same day into a
/// single notification, tracked by a persisted day→items map.
@MainActor
final class NotificationManager: NSObject, UNUserNotificationCenterDelegate {
    static let shared = NotificationManager()
    private override init() { super.init() }

    private let center = UNUserNotificationCenter.current()

    // MARK: - Categories

    enum Category: String, CaseIterable {
        case trial      // highest priority for the daily cap
        case ledger
        case portfolio
        case freeScan
        case recap      // lowest

        var priority: Int {
            switch self {
            case .trial:     return 5
            case .ledger:    return 4
            // Above the monthly recap, below anything time-critical: this is a
            // weekly habit nudge, so losing one to a trial warning costs
            // nothing, but it should outrank a once-a-month summary.
            case .portfolio: return 3
            // The daily free-scan nudge sits under the weekly digest: if both
            // land on a Sunday the digest carries more, and the nudge comes
            // back tomorrow anyway.
            case .freeScan:  return 2
            case .recap:     return 1
            }
        }

        var toggleKey: String { "notif_\(rawValue)_enabled" }

        /// Everything functional is on until turned off. The daily free-scan
        /// reminder is the one exception: a daily notification the user did
        /// not ask for is the definition of nagging, so it is opt-in.
        var defaultEnabled: Bool { self != .freeScan }
    }

    // MARK: - Identifiers

    private static let recapID = "recap.monthly"
    private static let portfolioID = "portfolio.weekly"
    private static let trialID = "trial.ending"
    // Prefix == Category.freeScan.rawValue: `category(fromID:)` relies on it.
    private static let freeScanID = "freeScan.daily"
    private static func ledgerDayID(_ dayKey: String) -> String { "ledger.day.\(dayKey)" }

    /// Recovers the category from any identifier ("ledger.day.20260801" → .ledger).
    private static func category(fromID id: String) -> Category? {
        Category(rawValue: id.components(separatedBy: ".").first ?? "")
    }

    // MARK: - Per-category opt-outs (default ON, individually disableable)

    func isEnabled(_ category: Category) -> Bool {
        UserDefaults.standard.object(forKey: category.toggleKey) as? Bool ?? category.defaultEnabled
    }

    func setEnabled(_ category: Category, _ on: Bool) {
        UserDefaults.standard.set(on, forKey: category.toggleKey)
        if !on { cancel(category) }
    }

    // MARK: - Authorization & priming

    private let primingShownKey = "notif_priming_shown"
    private var primingShown: Bool {
        get { UserDefaults.standard.bool(forKey: primingShownKey) }
        set { UserDefaults.standard.set(newValue, forKey: primingShownKey) }
    }

    func authorizationStatus() async -> UNAuthorizationStatus {
        await center.notificationSettings().authorizationStatus
    }

    private func isAuthorized() async -> Bool {
        switch await authorizationStatus() {
        case .authorized, .provisional, .ephemeral: return true
        default: return false
        }
    }

    /// True only when we've never asked and iOS hasn't recorded a decision —
    /// so we prime exactly once, at a moment of demonstrated value.
    func shouldPrimeAfterScan() async -> Bool {
        guard !primingShown else { return false }
        return (await authorizationStatus()) == .notDetermined
    }

    /// User accepted the in-app priming → ask iOS, then schedule anything already
    /// eligible so a mid-session grant doesn't wait for the next trigger.
    func enableFromPriming(context: ModelContext, purchaseService: any PurchaseService) async {
        primingShown = true
        let granted = (try? await center.requestAuthorization(options: [.alert, .sound, .badge])) ?? false
        if granted { await syncEligible(context: context, purchaseService: purchaseService) }
    }

    /// User declined the in-app priming — record it and never nag again.
    func declinePriming() { primingShown = true }

    func registerAsDelegate() { center.delegate = self }

    // MARK: - 1) Monthly recap

    /// Called after every successful scan. Only scans schedule the recap, so a
    /// quiet month fires nothing. Idempotent: the fixed identifier replaces any
    /// pending recap.
    func scheduleMonthlyRecap(monthScanCount: Int) async {
        guard isEnabled(.recap), monthScanCount >= 3 else { return }

        let cal = Calendar.current
        guard let thisMonthStart = cal.dateInterval(of: .month, for: Date())?.start,
              let nextMonthStart = cal.date(byAdding: .month, value: 1, to: thisMonthStart)
        else { return }

        var comps = cal.dateComponents([.year, .month, .day], from: nextMonthStart)
        comps.hour = 10; comps.minute = 0
        guard let fireDate = cal.date(from: comps) else { return }

        let label = Self.monthName(from: Date())   // the month being recapped
        // Persist recap-ready state so the in-app banner works even if denied.
        storeRecapPending(fireDate: fireDate, label: label)

        await add(id: Self.recapID, category: .recap, fireDate: fireDate,
                  body: "Your \(label) Recap is ready 👀")
    }

    // Recap-ready fallback state (drives the History banner when notifications
    // are off). Reset whenever a new month's recap is scheduled.
    private func storeRecapPending(fireDate: Date, label: String) {
        let d = UserDefaults.standard
        if d.string(forKey: "notif_recap_label") != label {
            d.set(false, forKey: "notif_recap_viewed")
        }
        d.set(fireDate.timeIntervalSince1970, forKey: "notif_recap_fire")
        d.set(label, forKey: "notif_recap_label")
    }

    /// The recapped month's name if a recap is due and not yet viewed, else nil.
    func readyRecapLabel() -> String? {
        let d = UserDefaults.standard
        guard let label = d.string(forKey: "notif_recap_label"),
              !d.bool(forKey: "notif_recap_viewed") else { return nil }
        let fire = Date(timeIntervalSince1970: d.double(forKey: "notif_recap_fire"))
        return Date() >= fire ? label : nil
    }

    func markRecapViewed() {
        UserDefaults.standard.set(true, forKey: "notif_recap_viewed")
    }

    // MARK: - 2) Ledger follow-up (14 days after "listed")

    /// Schedule (or coalesce) a follow-up 14 days after an item is listed. If
    /// another follow-up already lands on the same day, both collapse into one
    /// generic reminder so we never fire more than one ledger nudge per day.
    func scheduleLedgerFollowUp(itemID: UUID, itemName: String, from listedDate: Date) async {
        guard isEnabled(.ledger) else { return }
        guard let fireDate = Self.ledgerFireDate(from: listedDate) else { return }
        let dayKey = Self.dayKey(fireDate)

        var buckets = ledgerBuckets()
        var items = buckets[dayKey] ?? []
        if !items.contains(itemID.uuidString) { items.append(itemID.uuidString) }
        buckets[dayKey] = items
        saveLedgerBuckets(buckets)
        setLedgerName(itemID.uuidString, name: itemName)

        await rescheduleLedgerDay(dayKey)
    }

    /// Cancel an item's follow-up (marked sold / deleted). Reschedules or removes
    /// the shared day notification so no orphaned reminder survives.
    func cancelLedgerFollowUp(itemID: UUID) async {
        var buckets = ledgerBuckets()
        var affectedDays: [String] = []
        for (day, items) in buckets where items.contains(itemID.uuidString) {
            let remaining = items.filter { $0 != itemID.uuidString }
            if remaining.isEmpty { buckets.removeValue(forKey: day) } else { buckets[day] = remaining }
            affectedDays.append(day)
        }
        guard !affectedDays.isEmpty else { return }
        saveLedgerBuckets(buckets)
        clearLedgerName(itemID.uuidString)
        for day in affectedDays { await rescheduleLedgerDay(day) }
    }

    /// Remove every ledger follow-up (e.g. "clear history").
    func cancelAllLedger() {
        let ids = ledgerBuckets().keys.map(Self.ledgerDayID)
        if !ids.isEmpty { center.removePendingNotificationRequests(withIdentifiers: ids) }
        saveLedgerBuckets([:])
        UserDefaults.standard.removeObject(forKey: "notif_ledger_names")
    }

    private func rescheduleLedgerDay(_ dayKey: String) async {
        let items = ledgerBuckets()[dayKey] ?? []
        let id = Self.ledgerDayID(dayKey)
        guard !items.isEmpty, let fireDate = Self.date(fromDayKey: dayKey) else {
            center.removePendingNotificationRequests(withIdentifiers: [id])
            return
        }
        let body: String
        if items.count == 1, let name = ledgerName(items[0]) {
            body = "Did \(name) sell? Update your ledger to keep your profit accurate."
        } else {
            body = "You have items to update in your ledger."
        }
        await add(id: id, category: .ledger, fireDate: fireDate, body: body)
    }

    // MARK: - 3) Trial lifecycle

    /// Schedule a courtesy heads-up ~24h before the trial ends, or cancel it if
    /// the trial is gone. Idempotent via the fixed identifier. This never
    /// duplicates App Store billing notifications — it's a convenience only.
    func syncTrialReminder(endDate: Date?) async {
        let id = Self.trialID
        guard isEnabled(.trial),
              let endDate,
              let fireDate = Calendar.current.date(byAdding: .hour, value: -24, to: endDate),
              fireDate > Date()
        else {
            center.removePendingNotificationRequests(withIdentifiers: [id])
            return
        }
        await add(id: id, category: .trial, fireDate: fireDate,
                  body: "Your SnapWorth trial ends tomorrow.")
    }

    // MARK: - 4) Daily free-scan reminder (opt-in)

    /// The hour and minute the user picked, local time. 18:00 by default —
    /// after work, when the thrift run or the evening scroll happens.
    static let defaultFreeScanHour = 18
    private static let freeScanHourKey = "notif_freescan_hour"
    private static let freeScanMinuteKey = "notif_freescan_minute"

    var freeScanReminderTime: (hour: Int, minute: Int) {
        get {
            let d = UserDefaults.standard
            let hour = d.object(forKey: Self.freeScanHourKey) as? Int ?? Self.defaultFreeScanHour
            let minute = d.object(forKey: Self.freeScanMinuteKey) as? Int ?? 0
            return (min(23, max(0, hour)), min(59, max(0, minute)))
        }
        set {
            UserDefaults.standard.set(newValue.hour, forKey: Self.freeScanHourKey)
            UserDefaults.standard.set(newValue.minute, forKey: Self.freeScanMinuteKey)
        }
    }

    /// Schedule the next "your free scan is back" — or cancel it.
    ///
    /// Never for Pro (there is no free scan to come back), never for today if
    /// the user has already scanned (the allowance is spent), and only when the
    /// user opted in. Idempotent via the fixed identifier, so calling it after
    /// every scan and every foreground is the intended use: the previous
    /// request is simply replaced by the next correct one.
    func syncFreeScanReminder(isPro: Bool, scannedToday: Bool, streak: Int = 0,
                              now: Date = Date()) async {
        guard isEnabled(.freeScan), !isPro else {
            center.removePendingNotificationRequests(withIdentifiers: [Self.freeScanID])
            return
        }
        let time = freeScanReminderTime
        guard let fireDate = Self.nextFreeScanDate(after: now, hour: time.hour, minute: time.minute,
                                                   scannedToday: scannedToday) else { return }
        await add(id: Self.freeScanID, category: .freeScan, fireDate: fireDate,
                  body: Self.freeScanBody(streak: streak))
    }

    /// Today at the chosen time if that is still ahead and no scan has happened
    /// today; otherwise tomorrow at that time. Pure, for the tests.
    nonisolated static func nextFreeScanDate(after now: Date, hour: Int, minute: Int,
                                             scannedToday: Bool,
                                             calendar: Calendar = .current) -> Date? {
        var comps = calendar.dateComponents([.year, .month, .day], from: now)
        comps.hour = hour; comps.minute = minute
        guard let today = calendar.date(from: comps) else { return nil }
        if !scannedToday && today > now { return today }
        return calendar.date(byAdding: .day, value: 1, to: today)
    }

    /// The copy. A streak of two or more is worth naming — "day 5" is a reason
    /// to open the app that "your scan is back" is not. No guilt when it broke:
    /// the streak simply isn't mentioned.
    nonisolated static func freeScanBody(streak: Int) -> String {
        if streak >= 2 {
            return "Day \(streak + 1) of your streak is waiting — your free scan is back."
        }
        return "Your free scan is back. What did you find today?"
    }

    // MARK: - Re-sync everything eligible (grant-later / app active)

    /// Rebuilds all schedules from current state. Safe to call repeatedly —
    /// every scheduler here is idempotent.
    func syncEligible(context: ModelContext, purchaseService: any PurchaseService) async {
        let all = (try? context.fetch(FetchDescriptor<ScanResult>())) ?? []

        let monthCount = all.filter {
            Calendar.current.isDate($0.timestamp, equalTo: Date(), toGranularity: .month)
        }.count
        await scheduleMonthlyRecap(monthScanCount: monthCount)

        for item in all where item.status == .listed {
            await scheduleLedgerFollowUp(itemID: item.id, itemName: item.itemName,
                                         from: item.listedDate ?? item.timestamp)
        }

        await schedulePortfolioDigest(results: all)

        await syncTrialReminder(endDate: purchaseService.trialEndDate)

        let scannedToday = all.contains { Calendar.current.isDateInToday($0.timestamp) }
        await syncFreeScanReminder(isPro: purchaseService.isSubscribed, scannedToday: scannedToday,
                                   streak: ScanStreak.current())
    }

    // MARK: - Weekly portfolio nudge

    /// What the weekly reminder is allowed to say.
    ///
    /// Deliberately narrow. The obvious copy for a return hook is "your PS5 is
    /// worth $40 more this week" — and it would be false here. An item's value
    /// only ever moves when the *user* changes its condition
    /// (`ResultView` → `refreshPortfolioValue`); nothing re-values a saved item,
    /// because `ScanAPIClient.scan` is only called for a new photo. Reporting
    /// the user's own edit back to them as market movement would be inventing a
    /// signal, and detecting real movement needs background re-valuation — a
    /// larger feature with a per-user model cost.
    ///
    /// So the copy states two things that are true from local data: what the
    /// portfolio is worth, and what was added since the last reminder.
    struct WeeklyDigest: Equatable {
        let itemCount: Int
        let total: Decimal
        let addedThisWeek: Int

        /// Nil when there is nothing worth interrupting someone for.
        var body: String? {
            guard itemCount > 0 else { return nil }
            let money = HistoryViewModel.money(total)

            if addedThisWeek > 0 {
                let noun = addedThisWeek == 1 ? "find" : "finds"
                return "You added \(addedThisWeek) \(noun) this week. Your \(itemCount) "
                     + "\(itemCount == 1 ? "find is" : "finds are") worth \(money)."
            }
            // Nothing new: a plain status line rather than manufactured urgency.
            return "Your \(itemCount) \(itemCount == 1 ? "find is" : "finds are") worth \(money)."
        }
    }

    /// Builds the digest from the library. Pure, so the copy rules are testable
    /// without a notification centre or a ModelContainer.
    nonisolated static func digest(for results: [ScanResult],
                                   now: Date = Date()) -> WeeklyDigest {
        let weekAgo = now.addingTimeInterval(-7 * 86_400)
        return WeeklyDigest(
            itemCount: results.count,
            total: HistoryViewModel.total(of: results.map(\.portfolioValue)),
            addedThisWeek: results.filter { $0.timestamp >= weekAgo }.count
        )
    }

    /// Schedules the next weekly nudge.
    ///
    /// Re-scheduled on every eligible sync rather than repeating: the body has
    /// to be recomputed from the current library, and a `repeats: true` trigger
    /// would keep firing last month's numbers forever.
    func schedulePortfolioDigest(results: [ScanResult], now: Date = Date()) async {
        let digest = Self.digest(for: results, now: now)
        guard let body = digest.body else {
            // An empty portfolio has nothing to report. Clear any stale request
            // so a user who deleted everything is not reminded about it.
            cancel(.portfolio)
            return
        }
        guard let fireDate = Self.nextDigestDate(after: now) else { return }
        await add(id: Self.portfolioID, category: .portfolio,
                  fireDate: fireDate, body: body)
    }

    /// Sunday at 11:00 local — a time people browse, not a weekday morning
    /// competing with work notifications.
    nonisolated static func nextDigestDate(after now: Date,
                                           calendar: Calendar = .current) -> Date? {
        var comps = DateComponents()
        comps.weekday = 1        // Sunday
        comps.hour = 11
        comps.minute = 0
        return calendar.nextDate(after: now, matching: comps,
                                 matchingPolicy: .nextTime)
    }

    // MARK: - Cancellation by category

    private func cancel(_ category: Category) {
        switch category {
        case .recap: center.removePendingNotificationRequests(withIdentifiers: [Self.recapID])
        case .portfolio: center.removePendingNotificationRequests(withIdentifiers: [Self.portfolioID])
        case .trial: center.removePendingNotificationRequests(withIdentifiers: [Self.trialID])
        case .freeScan: center.removePendingNotificationRequests(withIdentifiers: [Self.freeScanID])
        case .ledger: cancelAllLedger()
        }
    }

    // MARK: - Core scheduling (authorization + global daily cap)

    /// Adds a request, honoring the per-category toggle, authorization, and the
    /// global "1 notification per day" cap (priority: trial > ledger > recap).
    private func add(id: String, category: Category, fireDate: Date, body: String) async {
        guard isEnabled(category) else { return }
        guard await isAuthorized() else { return }              // no-op until permitted
        guard await resolveDailyCap(for: category, fireDate: fireDate, ownID: id) else { return }

        let content = UNMutableNotificationContent()
        content.title = "SnapWorth"
        content.body = body
        content.sound = .default
        content.userInfo = ["category": category.rawValue]

        let comps = Calendar.current.dateComponents(
            [.year, .month, .day, .hour, .minute], from: fireDate)
        let trigger = UNCalendarNotificationTrigger(dateMatching: comps, repeats: false)
        let request = UNNotificationRequest(identifier: id, content: content, trigger: trigger)

        do {
            try await center.add(request)   // same identifier replaces any pending
            Analytics.shared.track(.notificationScheduled(category: category.rawValue))
        } catch {
            // Scheduling is best-effort; a failure just means no reminder.
        }
    }

    /// Enforces at most one notification per calendar day across all categories.
    /// Higher priority evicts a lower-priority same-day notification; an equal or
    /// higher existing one blocks the new schedule. Same-category collisions are
    /// handled upstream (fixed IDs / ledger day-buckets) and are ignored here.
    private func resolveDailyCap(for category: Category, fireDate: Date, ownID: String) async -> Bool {
        let cal = Calendar.current
        let pending = await center.pendingNotificationRequests()
        var toEvict: [String] = []

        for req in pending where req.identifier != ownID {
            guard let trigger = req.trigger as? UNCalendarNotificationTrigger,
                  let next = trigger.nextTriggerDate(),
                  cal.isDate(next, inSameDayAs: fireDate) else { continue }
            guard let otherCategory = Self.category(fromID: req.identifier),
                  otherCategory != category else { continue }

            if category.priority > otherCategory.priority {
                toEvict.append(req.identifier)
            } else {
                return false   // a same/higher-priority notification owns that day
            }
        }

        if !toEvict.isEmpty { center.removePendingNotificationRequests(withIdentifiers: toEvict) }
        return true
    }

    // MARK: - UNUserNotificationCenterDelegate (deep links)

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .sound]
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        let category = response.notification.request.content.userInfo["category"] as? String
        await MainActor.run { self.handleOpen(category) }
    }

    private func handleOpen(_ categoryRaw: String?) {
        guard let categoryRaw, let category = Category(rawValue: categoryRaw) else { return }
        Analytics.shared.track(.notificationOpened(category: categoryRaw))
        switch category {
        case .recap:
            markRecapViewed()
            NotificationCenter.default.post(name: .snapOpenFlips, object: nil)
        case .ledger:
            NotificationCenter.default.post(name: .snapOpenFlips, object: nil)
        case .trial:
            NotificationCenter.default.post(name: .snapOpenSettings, object: nil)
        case .portfolio:
            // Reuses the widget's existing My Finds route rather than adding a
            // second notification name for the same destination — MainTabView
            // already listens for it.
            NotificationCenter.default.post(name: .snapWidgetOpenHistory, object: nil)
        case .freeScan:
            // Straight to the camera: the notification promised a scan.
            NotificationCenter.default.post(name: .snapWidgetOpenScan, object: nil)
        }
    }

    // MARK: - Ledger bucket persistence (survives app kill)

    private func ledgerBuckets() -> [String: [String]] {
        guard let data = UserDefaults.standard.data(forKey: "notif_ledger_buckets"),
              let map = try? JSONDecoder().decode([String: [String]].self, from: data)
        else { return [:] }
        return map
    }

    private func saveLedgerBuckets(_ map: [String: [String]]) {
        UserDefaults.standard.set(try? JSONEncoder().encode(map), forKey: "notif_ledger_buckets")
    }

    private func ledgerNames() -> [String: String] {
        UserDefaults.standard.dictionary(forKey: "notif_ledger_names") as? [String: String] ?? [:]
    }
    private func ledgerName(_ itemID: String) -> String? { ledgerNames()[itemID] }
    private func setLedgerName(_ itemID: String, name: String) {
        var names = ledgerNames(); names[itemID] = name
        UserDefaults.standard.set(names, forKey: "notif_ledger_names")
    }
    private func clearLedgerName(_ itemID: String) {
        var names = ledgerNames(); names.removeValue(forKey: itemID)
        UserDefaults.standard.set(names, forKey: "notif_ledger_names")
    }

    // MARK: - Date helpers (timezone/calendar correct)

    /// 14 days after the listing, pinned to 10:00 local.
    private static func ledgerFireDate(from listedDate: Date) -> Date? {
        let cal = Calendar.current
        guard let base = cal.date(byAdding: .day, value: 14, to: listedDate) else { return nil }
        var comps = cal.dateComponents([.year, .month, .day], from: base)
        comps.hour = 10; comps.minute = 0
        return cal.date(from: comps)
    }

    private static let dayKeyFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.calendar = Calendar.current
        f.dateFormat = "yyyyMMdd"
        return f
    }()

    private static func dayKey(_ date: Date) -> String { dayKeyFormatter.string(from: date) }

    private static func date(fromDayKey key: String) -> Date? {
        guard let day = dayKeyFormatter.date(from: key) else { return nil }
        var comps = Calendar.current.dateComponents([.year, .month, .day], from: day)
        comps.hour = 10; comps.minute = 0
        return Calendar.current.date(from: comps)
    }

    private static func monthName(from date: Date) -> String {
        let f = DateFormatter()
        f.calendar = Calendar.current
        f.locale = .current
        f.setLocalizedDateFormatFromTemplate("MMMM")
        return f.string(from: date)
    }
}
