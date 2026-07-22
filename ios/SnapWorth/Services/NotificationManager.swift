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
        case recap      // lowest

        var priority: Int {
            switch self {
            case .trial:  return 3
            case .ledger: return 2
            case .recap:  return 1
            }
        }

        var toggleKey: String { "notif_\(rawValue)_enabled" }
    }

    // MARK: - Identifiers

    private static let recapID = "recap.monthly"
    private static let trialID = "trial.ending"
    private static func ledgerDayID(_ dayKey: String) -> String { "ledger.day.\(dayKey)" }

    /// Recovers the category from any identifier ("ledger.day.20260801" → .ledger).
    private static func category(fromID id: String) -> Category? {
        Category(rawValue: id.components(separatedBy: ".").first ?? "")
    }

    // MARK: - Per-category opt-outs (default ON, individually disableable)

    func isEnabled(_ category: Category) -> Bool {
        UserDefaults.standard.object(forKey: category.toggleKey) as? Bool ?? true
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

        await syncTrialReminder(endDate: purchaseService.trialEndDate)
    }

    // MARK: - Cancellation by category

    private func cancel(_ category: Category) {
        switch category {
        case .recap: center.removePendingNotificationRequests(withIdentifiers: [Self.recapID])
        case .trial: center.removePendingNotificationRequests(withIdentifiers: [Self.trialID])
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
