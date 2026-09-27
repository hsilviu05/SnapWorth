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
/// | portfolio | next 4 Sundays 11:00                      | `portfolio.weekly.<n>` (a ladder) |
/// | trial    | ≥24h before trial end, 09:00–21:00 local   | `trial.ending`        |
/// | freeScan | opt-in; the user's hour, once the UTC allowance is back | `freeScan.daily.<yyyymmdd>` (a ladder) |
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
    // The weekly digest is a ladder too — see `portfolioIDs`.
    private static let trialID = "trial.ending"
    // Prefix == Category.freeScan.rawValue: `category(fromID:)` relies on it.
    //
    // Retained as the *legacy* identifier. A build before the ladder scheduled
    // one request under exactly this id, so an upgrading install can have one
    // pending and it has to be cancellable.
    // `nonisolated`, because `freeScanIDs` is — referencing a main-actor
    // static from a nonisolated context is a warning today and an error in the
    // Swift 6 language mode. A `String` literal is `Sendable`, so there is
    // nothing here for the isolation to protect.
    nonisolated private static let freeScanID = "freeScan.daily"

    /// How many days of free-scan reminders are scheduled at a time.
    ///
    /// The reminder was a single dated one-shot with `repeats: false`, and the
    /// only things that ever re-armed it — after a scan, on foreground, on a
    /// settings change — all require the app to be open. There is no
    /// `BGTaskScheduler` anywhere in the project either. So the reminder whose
    /// entire purpose is to bring back someone who has stopped opening the app
    /// fired exactly once and then went silent forever, while Settings kept
    /// showing "Daily free scan" ON with a time picker and a footer promising
    /// "one reminder at the time you pick".
    ///
    /// A ladder of dated one-shots rather than `repeats: true`: the copy names
    /// the streak, and a repeating trigger freezes its body, so a user who
    /// lapsed with a 5-day streak would be told "Day 6 of your streak is
    /// waiting" every day indefinitely — a daily false statement. Dated
    /// requests let day one carry the streak (which is known) and the rest
    /// carry the plain copy (which stays true).
    ///
    /// Seven days is also a deliberate stopping point. Someone who has ignored
    /// a week of reminders should stop receiving them; the ladder refills on
    /// every foreground, so anyone still using the app never reaches the end.
    //
    // A computed `nonisolated` property rather than a `nonisolated static let`:
    // the latter's availability on a global-actor-isolated type is version
    // dependent, and there is no Swift toolchain in the environment this was
    // written in to settle it. A computed one is unambiguous and the literal
    // is free.
    nonisolated static var freeScanLadderDays: Int { 7 }

    /// `freeScan.daily.yyyyMMdd`, the same shape `dayKey` produces.
    ///
    /// `nonisolated`, like `nextFreeScanDate` and `freeScanBody` below: this is
    /// calendar arithmetic, it touches no actor state, and `cancel(_:)` needs
    /// it from a synchronous context — as do the tests.
    ///
    /// Built from `dateComponents` rather than through the shared `dayKey`,
    /// which reads a `static let DateFormatter`. A `DateFormatter` is not
    /// `Sendable`, so reaching it from a nonisolated context is exactly the
    /// shared-mutable-state hazard the isolation is there to flag. The two
    /// produce identical strings — both use `Calendar.current` — and this one
    /// needs nothing shared.
    nonisolated private static func freeScanLadderID(
        forDay day: Date, calendar: Calendar = .current
    ) -> String {
        let parts = calendar.dateComponents([.year, .month, .day], from: day)
        return String(format: "freeScan.daily.%04d%02d%02d",
                      parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }

    /// Every identifier the ladder can be occupying, including the legacy one.
    ///
    /// Computed rather than discovered, so `cancel(_:)` stays synchronous —
    /// it is called from `setEnabled`, which SwiftUI calls from a toggle. The
    /// range runs a day wider than the ladder at both ends so a device whose
    /// clock or timezone moved cannot orphan a request.
    ///
    /// The ladder can start two local days out, not one: it waits for the UTC
    /// reset, and west of UTC an evening scan's allowance comes back only the
    /// evening after next. Its last rung can then sit at +8, which is where
    /// the far end of this range used to stop — no margin left for the move
    /// the margin is there for.
    nonisolated static func freeScanIDs(around now: Date,
                                        calendar: Calendar = .current) -> [String] {
        var ids = [freeScanID]
        for offset in -1...(freeScanLadderDays + 2) {
            guard let day = calendar.date(byAdding: .day, value: offset, to: now) else { continue }
            ids.append(freeScanLadderID(forDay: day, calendar: calendar))
        }
        return ids
    }
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

    /// True when iOS has never been asked — so nothing this app schedules will
    /// ever be delivered, and the user has no way to tell.
    ///
    /// `requestAuthorization` used to appear at exactly one place in the whole
    /// project: inside `enableFromPriming`, reachable only through
    /// `shouldPrimeAfterScan()`, which is gated on `!primingShown` — and
    /// `declinePriming()` sets that permanently. So "Not now" on the priming
    /// alert left the status `.notDetermined` for the life of the install,
    /// `isAuthorized()` mapped that to false, and `add()` returned silently for
    /// every category forever. Meanwhile Settings rendered Weekly portfolio,
    /// Monthly recap, Ledger and Trial reminders all ON (they default on), the
    /// user could switch on Daily free scan and pick a time, and not one
    /// notification would ever arrive — not even the heads-up before their
    /// first trial charge. The "turn them on in iOS Settings" banner was gated
    /// on `.denied`, which is never this user's status.
    func needsAuthorizationRequest() async -> Bool {
        await authorizationStatus() == .notDetermined
    }

    /// Ask iOS, if it has never been asked. Safe to call from a Settings
    /// toggle: `requestAuthorization` is a no-op once a decision exists.
    ///
    /// Returns whether notifications can now be delivered, so the caller can
    /// show the "turn them on in iOS Settings" banner if the user declines the
    /// system alert here.
    @discardableResult
    func requestAuthorizationIfNeeded() async -> Bool {
        guard await needsAuthorizationRequest() else { return await isAuthorized() }
        primingShown = true          // iOS has now been asked; never prime again
        return (try? await center.requestAuthorization(options: [.alert, .sound, .badge])) ?? false
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
                  body: String(localized: "Your \(label) Recap is ready 👀"))
    }

    // Recap-ready fallback state (drives the History banner when notifications
    // are off).
    private enum RecapKeys {
        static let fire = "notif_recap_fire"
        static let label = "notif_recap_label"
        static let viewed = "notif_recap_viewed"
        // Where next month's recap waits when this month's is still owed.
        static let deferredFire = "notif_recap_deferred_fire"
        static let deferredLabel = "notif_recap_deferred_label"
    }

    /// Whether writing `incomingLabel` now would erase a recap the user is
    /// still owed.
    ///
    /// `storeRecapPending` overwrote the fire date and label unconditionally
    /// and reset `viewed` whenever the label changed, so scheduling *next*
    /// month's recap pushed the fire date a month out and made
    /// `readyRecapLabel()` return nil — destroying a recap that was already due
    /// and never seen. The comment on this state says it exists "so the in-app
    /// banner works even if denied", which is to say: for the users who will
    /// never receive the notification. They are exactly the population that
    /// lost it, and on the free tier three scans spread across 1–3 September
    /// are enough to erase the August banner before History is opened once.
    ///
    /// Pure, so the case can be tested without `UserDefaults` or a clock.
    nonisolated static func recapIsStillOwed(storedLabel: String?,
                                             storedFire: Date?,
                                             viewed: Bool,
                                             incomingLabel: String,
                                             now: Date) -> Bool {
        guard let storedLabel, storedLabel != incomingLabel, !viewed,
              let storedFire, now >= storedFire
        else { return false }
        return true
    }

    private func storeRecapPending(fireDate: Date, label: String, now: Date = Date()) {
        let d = UserDefaults.standard
        let storedFire = (d.object(forKey: RecapKeys.fire) as? Double)
            .map { Date(timeIntervalSince1970: $0) }

        if Self.recapIsStillOwed(storedLabel: d.string(forKey: RecapKeys.label),
                                 storedFire: storedFire,
                                 viewed: d.bool(forKey: RecapKeys.viewed),
                                 incomingLabel: label,
                                 now: now) {
            // Parked, not discarded. Skipping the write alone would have lost
            // *this* recap instead for anyone who does not scan again after
            // reading the banner — trading one silent loss for another.
            d.set(fireDate.timeIntervalSince1970, forKey: RecapKeys.deferredFire)
            d.set(label, forKey: RecapKeys.deferredLabel)
            return
        }

        // Anything parked is superseded by a write that is allowed to land.
        d.removeObject(forKey: RecapKeys.deferredFire)
        d.removeObject(forKey: RecapKeys.deferredLabel)
        if d.string(forKey: RecapKeys.label) != label {
            d.set(false, forKey: RecapKeys.viewed)
        }
        d.set(fireDate.timeIntervalSince1970, forKey: RecapKeys.fire)
        d.set(label, forKey: RecapKeys.label)
    }

    /// The recapped month's name if a recap is due and not yet viewed, else nil.
    func readyRecapLabel(now: Date = Date()) -> String? {
        let d = UserDefaults.standard
        guard let label = d.string(forKey: RecapKeys.label),
              !d.bool(forKey: RecapKeys.viewed) else { return nil }
        let fire = Date(timeIntervalSince1970: d.double(forKey: RecapKeys.fire))
        return now >= fire ? label : nil
    }

    func markRecapViewed() {
        let d = UserDefaults.standard
        d.set(true, forKey: RecapKeys.viewed)
        // Promote whatever was parked while this one was still owed.
        guard let label = d.string(forKey: RecapKeys.deferredLabel),
              let fire = d.object(forKey: RecapKeys.deferredFire) as? Double
        else { return }
        d.removeObject(forKey: RecapKeys.deferredLabel)
        d.removeObject(forKey: RecapKeys.deferredFire)
        d.set(fire, forKey: RecapKeys.fire)
        d.set(label, forKey: RecapKeys.label)
        d.set(false, forKey: RecapKeys.viewed)
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
            body = String(localized: "Did \(name) sell? Update your ledger to keep your profit accurate.")
        } else {
            body = String(localized: "You have items to update in your ledger.")
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
              let fireDate = Self.trialReminderFireDate(endDate: endDate, now: Date())
        else {
            center.removePendingNotificationRequests(withIdentifiers: [id])
            return
        }
        await add(id: id, category: .trial, fireDate: fireDate,
                  body: Self.trialBody(fireDate: fireDate, endDate: endDate))
    }

    /// The latest moment between 09:00 and 21:00 local that is still at least
    /// 24 hours before the trial ends.
    ///
    /// It was exactly 24 hours before, to the second — and since a trial ends
    /// at the minute it was started, a trial begun at 01:40 woke its owner
    /// with a sound at 01:40 two nights later, as the one category that also
    /// evicts anything else due that day. Later than the 24-hour mark is no
    /// use (a renewal can be charged inside it), so the move is always
    /// earlier: to 21:00 the same evening when the mark falls late at night,
    /// and to 21:00 the evening before when it falls in the small hours.
    ///
    /// Pure, and takes its calendar, for the tests.
    nonisolated static func trialReminderDate(endDate: Date,
                                              calendar: Calendar = .current) -> Date? {
        guard let deadline = calendar.date(byAdding: .hour, value: -24, to: endDate),
              let morning = calendar.date(bySettingHour: 9, minute: 0, second: 0, of: deadline),
              let evening = calendar.date(bySettingHour: 21, minute: 0, second: 0, of: deadline)
        else { return nil }
        if deadline < morning { return calendar.date(byAdding: .day, value: -1, to: evening) }
        return min(deadline, evening)
    }

    /// What to schedule as of `now`: the waking-hours slot while it is still
    /// ahead, else the 24-hour mark while that is, else nothing.
    ///
    /// The slot is earlier than the mark by up to twelve hours, and the first
    /// sync can land in between — notifications allowed mid-trial, a restore
    /// on a new device, the first foreground after an update from a build that
    /// had the warning pending at the mark. Requiring the slot alone dropped
    /// the warning there, and the update case removed one that was already
    /// pending, while a full day's notice could still be given. The fallback
    /// is the mark, which can be at night: a night warning beats none. It is
    /// the same instant on every sync, so a foreground in the gap replaces the
    /// request rather than firing it again — which "a minute from now" would
    /// not, and inside the gap that is never in waking hours either.
    ///
    /// Pure, and takes its calendar, for the tests.
    nonisolated static func trialReminderFireDate(endDate: Date, now: Date,
                                                  calendar: Calendar = .current) -> Date? {
        if let slot = trialReminderDate(endDate: endDate, calendar: calendar), slot > now {
            return slot
        }
        guard let mark = calendar.date(byAdding: .hour, value: -24, to: endDate), mark > now
        else { return nil }
        return mark
    }

    /// "Ends tomorrow" is only true when it is. Moved to the evening before a
    /// small-hours deadline, the trial ends the day after the next one — so
    /// that case names the day and the time rather than saying "tomorrow".
    nonisolated static func trialBody(fireDate: Date, endDate: Date,
                                      calendar: Calendar = .current) -> String {
        let days = calendar.dateComponents([.day], from: calendar.startOfDay(for: fireDate),
                                           to: calendar.startOfDay(for: endDate)).day
        if days == 1 {
            return String(localized: "Your SnapWorth trial ends tomorrow.")
        }
        var style = Date.FormatStyle(date: .omitted, time: .shortened)
        style.timeZone = calendar.timeZone
        let time = endDate.formatted(style)
        return String(localized: "Your SnapWorth trial ends the day after tomorrow, at \(time).")
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
    /// Never for Pro (there is no free scan to come back), never before the
    /// allowance has actually come back, and only when the user opted in.
    /// Idempotent via the fixed identifiers, so calling it after every scan
    /// and every foreground is the intended use: the previous request is
    /// simply replaced by the next correct one.
    ///
    /// `lastScan` is `ScanStreak.lastScan`, the instant of the most recent
    /// scan on any tier. It answers two different questions on two different
    /// clocks: whether today's streak day is already made (the local day, as
    /// the streak is kept) and when the allowance comes back (the UTC day, as
    /// the server counts it).
    func syncFreeScanReminder(isPro: Bool, lastScan: Date?, streak: Int = 0,
                              now: Date = Date()) async {
        // Always clear the whole ladder first, including the legacy single id.
        // Every path below either rebuilds it or wants it gone, and a stale rung
        // left behind fires on its old day with its old body.
        center.removePendingNotificationRequests(
            withIdentifiers: Self.freeScanIDs(around: now))
        guard isEnabled(.freeScan), !isPro else { return }

        let time = freeScanReminderTime
        // `hasRemaining` as well as the last scan: the server can have refused
        // today's scan without one being recorded here — a 402, or a reinstall
        // whose allowance was withheld at mint.
        let returns = Self.freeScanReturns(lastScan: lastScan,
                                           spentNow: !FreeScanCounter.hasRemaining,
                                           now: now)
        guard let first = Self.nextFreeScanDate(after: now, hour: time.hour, minute: time.minute,
                                                notBefore: returns) else { return }
        let calendar = Calendar.current
        let scannedToday = lastScan.map { calendar.isDate($0, inSameDayAs: now) } ?? false
        // Only the first rung can even try to name the streak: beyond that the
        // user may have scanned, or lapsed, and either claim would be
        // invented. And it may only try — `streakOutlives` decides whether the
        // streak that is alive now is still alive on the day that rung fires.
        let namedStreak = Self.streakOutlives(fireDate: first, now: now,
                                              scannedToday: scannedToday) ? streak : 0
        var scheduledAny = false
        for offset in 0..<Self.freeScanLadderDays {
            guard let fireDate = calendar.date(byAdding: .day, value: offset, to: first)
            else { continue }
            let body = offset == 0 ? Self.freeScanBody(streak: namedStreak)
                                   : Self.freeScanBody(streak: 0)
            let added = await add(id: Self.freeScanLadderID(forDay: fireDate),
                                  category: .freeScan, fireDate: fireDate,
                                  body: body, track: false)
            scheduledAny = scheduledAny || added
        }
        // One event for one logical reminder, not seven per foreground.
        if scheduledAny {
            Analytics.shared.track(.notificationScheduled(category: Category.freeScan.rawValue))
        }
    }

    /// When the free scan comes back, or nil when it is available now.
    ///
    /// The allowance is counted per UTC day — `quota.py`, and on this side
    /// `FreeScanCounter.isServerToday` and the Scans-left widget — so it comes
    /// back at the first UTC midnight after the last scan. The reminder asked
    /// the local calendar instead, which is a different question everywhere
    /// but UTC+0: a user in New York who scanned at 21:00 was told at 18:00
    /// the next day that the scan was back, and the tap opened the paywall,
    /// because it came back at 20:00. East of UTC it ran the other way — a
    /// scan at 08:00 in Sydney is yesterday's allowance, back by 10:00, and
    /// that evening's reminder was skipped.
    ///
    /// Pure, and takes its UTC calendar, for the tests.
    nonisolated static func freeScanReturns(
        lastScan: Date?, spentNow: Bool, now: Date,
        serverCalendar: Calendar = WidgetHaulData.serverCalendar
    ) -> Date? {
        func nextReset(after date: Date) -> Date? {
            serverCalendar.date(byAdding: .day, value: 1,
                                to: serverCalendar.startOfDay(for: date))
        }
        var returns = lastScan.flatMap(nextReset(after:))
        if spentNow, let reset = nextReset(after: now) {
            returns = max(returns ?? reset, reset)
        }
        guard let returns, returns > now else { return nil }
        return returns
    }

    /// The first time at the chosen hour and minute that is after `now` and
    /// not before `notBefore` — the moment the allowance comes back. Today if
    /// both allow it, otherwise the first day that does. Pure, for the tests.
    nonisolated static func nextFreeScanDate(after now: Date, hour: Int, minute: Int,
                                             notBefore: Date?,
                                             calendar: Calendar = .current) -> Date? {
        let earliest = max(now, notBefore ?? now)
        var comps = calendar.dateComponents([.year, .month, .day], from: earliest)
        comps.hour = hour; comps.minute = minute
        guard let sameDay = calendar.date(from: comps) else { return nil }
        if sameDay > now && sameDay >= earliest { return sameDay }
        return calendar.date(byAdding: .day, value: 1, to: sameDay)
    }

    /// Whether the streak's next day is the day `fireDate` lands on — the only
    /// day "Day N of your streak is waiting" is true.
    ///
    /// The body is frozen into the request when it is scheduled, and
    /// `ScanStreak.current()` counts a streak as alive while the last scan was
    /// today or yesterday. So a reminder scheduled tonight for *tomorrow*
    /// evening, on a day the user did not scan, names a streak that will have
    /// lapsed by the time it fires: the last scan is yesterday now and the day
    /// before yesterday then. Naming it anyway promises a day number the app
    /// has already discarded and cannot give them — against the rule
    /// `freeScanBody` states for itself, that there is no guilt when the
    /// streak broke, it simply isn't mentioned.
    ///
    /// Two cases survive. The user has not scanned today and the rung fires
    /// today, so nothing has moved; or the user scanned today, which makes
    /// today the streak's last day, and the rung fires tomorrow. Since the
    /// reminder waits for the UTC reset, two more cases now arise and both
    /// fail: a rung the same local day as today's scan (a scan now cannot
    /// make a new streak day), and one the day after tomorrow (west of UTC,
    /// an evening scan's allowance returns only on the following evening).
    nonisolated static func streakOutlives(fireDate: Date, now: Date,
                                           scannedToday: Bool,
                                           calendar: Calendar = .current) -> Bool {
        guard scannedToday else { return calendar.isDate(fireDate, inSameDayAs: now) }
        guard let tomorrow = calendar.date(byAdding: .day, value: 1, to: now) else { return false }
        return calendar.isDate(fireDate, inSameDayAs: tomorrow)
    }

    /// "Remind me", from wherever the user asked.
    ///
    /// The reminder stays opt-in; this is the opt-in. It was reachable only
    /// from Settings → Notifications, and the moment it is wanted — the free
    /// scan just spent — is on the Scan tab, where the only thing on offer was
    /// "Upgrade to Pro". Accepting the post-scan priming alert does not turn
    /// it on either, and should not: that alert promises a recap and ledger
    /// nudges, not a daily notification.
    ///
    /// Asks iOS if it has never been asked, which is what makes one tap
    /// enough. Returns whether notifications can be delivered, so the caller
    /// can send a user who refused them at the system level to Settings
    /// rather than claim a reminder that cannot arrive.
    ///
    /// Then schedules everything eligible, as `enableFromPriming` does, not
    /// the ladder alone. The caller reads the next fire straight back and
    /// names its day, so the ladder must already be the one the daily cap
    /// leaves. On a first grant nothing else has ever been scheduled — every
    /// `add` returned at `isAuthorized` — so a ladder built on its own could
    /// keep a Sunday rung that the weekly digest evicts moments later, when
    /// the foreground sync the closing alert set off reaches it. That sync
    /// waits on StoreKit first, so the read usually came before it, and the
    /// row said "Reminder set for Sun" over a first reminder due on Monday.
    func optInToFreeScanReminder(source: ReminderOptInSource, context: ModelContext,
                                 purchaseService: any PurchaseService) async -> Bool {
        switchOnFreeScanReminder(source: source)
        guard await requestAuthorizationIfNeeded() else { return false }
        await syncEligible(context: context, purchaseService: purchaseService)
        return true
    }

    /// The toggle half of the opt-in, reported only when it is the change.
    ///
    /// "Remind me" is offered while the toggle is on and iOS has never been
    /// asked, which an install that switched it on before the toggle asked
    /// iOS (4499251) can carry. The tap there asks iOS and switches nothing,
    /// and `reminder_opt_in` means the reminder was switched on. Settings
    /// reports from `onChange`, which is only ever a change. Internal, for
    /// the tests: the rest of the opt-in asks iOS, which a test cannot answer.
    func switchOnFreeScanReminder(source: ReminderOptInSource) {
        let wasOn = isEnabled(.freeScan)
        setEnabled(.freeScan, true)
        if !wasOn { Analytics.shared.track(.reminderOptIn(source: source)) }
    }

    /// When the free-scan reminder will next fire, read back from what is
    /// actually pending rather than recomputed: the daily cap can move or
    /// drop a rung, and the screen should name the one iOS holds.
    func pendingFreeScanReminder() async -> Date? {
        await center.pendingNotificationRequests()
            .filter { Self.category(fromID: $0.identifier) == .freeScan }
            .compactMap { ($0.trigger as? UNCalendarNotificationTrigger)?.nextTriggerDate() }
            .min()
    }

    /// The copy. A streak of two or more is worth naming — "day 5" is a reason
    /// to open the app that "your scan is back" is not. No guilt when it broke:
    /// the streak simply isn't mentioned.
    nonisolated static func freeScanBody(streak: Int) -> String {
        if streak >= 2 {
            return String(localized: "Day \(streak + 1) of your streak is waiting — your free scan is back.")
        }
        return String(localized: "Your free scan is back. What did you find today?")
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

        await syncFreeScanReminder(isPro: purchaseService.isSubscribed,
                                   lastScan: ScanStreak.lastScan,
                                   streak: ScanStreak.current())
    }

    // MARK: - Weekly portfolio nudge

    /// What the weekly reminder is allowed to say.
    ///
    /// Deliberately narrow. The obvious copy for a return hook is "your PS5 is
    /// worth $40 more this week" — and it would be false here. An item's value
    /// only ever moves when the *user* acts — changing its condition
    /// (`ResultView`) or re-reading the care tag (`applySharpened`), both of
    /// which call `refreshPortfolioValue` — or when an update corrects how the
    /// app prices a grade. Nothing re-values a saved item from the market;
    /// `ScanAPIClient.scan` runs only for a photo the user supplied. Reporting
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

            // Each count is inflected by itself and the amount is added after: a
            // plural key agrees with one number, and "you added N … your M are
            // worth X" has two of them and an amount besides. The amount is last
            // in both languages, so it can be appended rather than interpolated.
            let held = String(localized: "Your \(itemCount) finds are worth") + " \(money)."
            guard addedThisWeek > 0 else {
                // Nothing new: a plain status line rather than manufactured urgency.
                return held
            }
            return String(localized: "You added \(addedThisWeek) finds this week.") + " " + held
        }
    }

    /// Builds the digest from the library. Pure, so the copy rules are testable
    /// without a notification centre or a ModelContainer.
    ///
    /// `now` is the moment the sentence is read — the scheduler passes each
    /// rung's fire date, so "this week" means the week before the Sunday it
    /// lands on.
    nonisolated static func digest(for results: [ScanResult],
                                   now: Date = Date()) -> WeeklyDigest {
        let weekAgo = now.addingTimeInterval(-7 * 86_400)
        return WeeklyDigest(
            // Held items only, the same rows the total below is made of. This
            // counted every row while the total covered only what is still
            // held, so a user who had sold 4 of 10 was told "Your 10 finds are
            // worth $120" when the $120 was six of them — and one who had sold
            // everything, "Your 10 finds are worth $0.00".
            itemCount: LedgerMath.held(results).count,
            // `LedgerMath.heldValue`, the same "still held" figure the History
            // header shows. This summed every row including sold ones, so the
            // weekly push repeated the inflated total the header used to show
            // — two surfaces stating a number that matched neither the realised
            // profit nor the held value.
            total: LedgerMath.heldValue(results),
            addedThisWeek: results.filter { $0.timestamp >= weekAgo }.count
        )
    }

    /// How many Sundays of the digest are scheduled at a time.
    ///
    /// It was one dated request with `repeats: false`, and the only thing that
    /// re-armed it was `syncEligible` — which runs when the app comes forward.
    /// So the reminder meant for someone who has stopped opening the app
    /// reached them once and then never again, the same defect the free-scan
    /// ladder was built to fix. Four, not forever: a month of ignored Sundays
    /// is an answer, and anyone still using the app refills the ladder on
    /// every foreground and never reaches the end of it.
    nonisolated static var portfolioLadderWeeks: Int { 4 }

    /// Every identifier the digest can occupy: the rungs, plus the single id a
    /// build before the ladder used, which an upgrading install can still have
    /// pending and nothing else would clear.
    nonisolated static var portfolioIDs: [String] {
        ["portfolio.weekly"] + (0..<portfolioLadderWeeks).map(portfolioID(rung:))
    }

    /// Prefix == Category.portfolio.rawValue: `category(fromID:)` relies on it.
    nonisolated static func portfolioID(rung: Int) -> String { "portfolio.weekly.\(rung)" }

    /// The Sundays the ladder fires on, soonest first. Pure, for the tests.
    nonisolated static func digestDates(after now: Date,
                                        calendar: Calendar = .current) -> [Date] {
        guard let first = nextDigestDate(after: now, calendar: calendar) else { return [] }
        return (0..<portfolioLadderWeeks).compactMap {
            calendar.date(byAdding: .weekOfYear, value: $0, to: first)
        }
    }

    /// Schedules the next few weekly nudges.
    ///
    /// Re-scheduled on every eligible sync rather than repeating: the body has
    /// to be recomputed from the current library, and a `repeats: true` trigger
    /// would keep firing last month's numbers forever.
    ///
    /// Each rung's body is computed as of its own Sunday. Nothing can be added
    /// without the app open, and opening it rebuilds the ladder, so the first
    /// rung can say what was added in the week before it lands and the later
    /// ones find nothing added and say only what is held — which stays true
    /// for exactly as long as the user leaves the app shut. Nothing re-values a
    /// saved item on its own (see `WeeklyDigest`).
    func schedulePortfolioDigest(results: [ScanResult], now: Date = Date()) async {
        // Always clear the whole ladder first. Every path below either rebuilds
        // it or wants it gone, and a rung left behind fires with its old body.
        cancel(.portfolio)
        guard isEnabled(.portfolio) else { return }

        var scheduledAny = false
        for (rung, fireDate) in Self.digestDates(after: now).enumerated() {
            // An empty portfolio has nothing to report — nor does one whose
            // every find has been sold — so nothing is scheduled at all.
            guard let body = Self.digest(for: results, now: fireDate).body else { return }
            let added = await add(id: Self.portfolioID(rung: rung), category: .portfolio,
                                  fireDate: fireDate, body: body, track: false)
            scheduledAny = scheduledAny || added
        }
        // One event for one logical reminder, as the free-scan ladder does.
        if scheduledAny {
            Analytics.shared.track(.notificationScheduled(category: Category.portfolio.rawValue))
        }
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
        case .portfolio: center.removePendingNotificationRequests(withIdentifiers: Self.portfolioIDs)
        case .trial: center.removePendingNotificationRequests(withIdentifiers: [Self.trialID])
        case .freeScan:
            center.removePendingNotificationRequests(
                withIdentifiers: Self.freeScanIDs(around: Date()))
        case .ledger: cancelAllLedger()
        }
    }

    // MARK: - Core scheduling (authorization + global daily cap)

    /// The scheduling currently in flight, if any.
    ///
    /// `add` reads the notification daemon's pending set, decides from it, and
    /// then writes — a read-modify-write with two suspension points inside it
    /// (`isAuthorized`, `pendingNotificationRequests`, `center.add`). Being
    /// `@MainActor` serialises this class's code only *between* awaits, so two
    /// `add` calls could each take a snapshot that did not contain the other's
    /// request, each conclude the day was free, and each schedule — breaking
    /// the one-per-day cap this type documents for itself at the top of the
    /// file. The interleave is not hypothetical: every scheduling entry point
    /// is its own unstructured `Task`, and `ScanViewModel.startScan` starts
    /// two back to back.
    ///
    /// Each `add` therefore chains onto whatever was in flight, so the
    /// snapshot and the write that depends on it cannot be separated by
    /// another scheduler. The link is made synchronously — `previous` is read
    /// and `scheduling` written with no await between them — which is what
    /// makes the chain a chain rather than a race of its own.
    private var scheduling: Task<Bool, Never>?

    /// Adds a request, honoring the per-category toggle, authorization, and the
    /// global "1 notification per day" cap (priority: trial > ledger > recap).
    /// `track: false` suppresses the `notification_scheduled` event, for a
    /// caller that schedules several requests for one logical reminder and
    /// reports it once. Without it the free-scan ladder would emit seven events
    /// on every foreground — the same event-spam the past-date guard above was
    /// added to stop.
    @discardableResult
    private func add(id: String, category: Category, fireDate: Date, body: String,
                     track: Bool = true) async -> Bool {
        let previous = scheduling
        let mine = Task { @MainActor () -> Bool in
            _ = await previous?.value
            return await self.schedule(id: id, category: category, fireDate: fireDate,
                                       body: body, track: track)
        }
        scheduling = mine
        return await mine.value
    }

    private func schedule(id: String, category: Category, fireDate: Date, body: String,
                          track: Bool) async -> Bool {
        guard isEnabled(category) else { return false }
        // A fire date in the past is not a reminder. `syncEligible` runs on
        // every foreground and re-schedules the ledger follow-up for every
        // listed item, including ones listed more than 14 days ago — so each
        // stale listing re-added a past-dated request and tracked a
        // `notification_scheduled` event on every single foreground.
        // `syncTrialReminder` already guards this way; nothing else did.
        guard fireDate > Date() else {
            center.removePendingNotificationRequests(withIdentifiers: [id])
            return false
        }
        guard await isAuthorized() else { return false }         // no-op until permitted
        guard await resolveDailyCap(for: category, fireDate: fireDate, ownID: id) else {
            // Cancel, do not just return. Every fixed-ID category (recap,
            // portfolio, trial, freeScan) gets its idempotence from
            // `center.add` replacing a pending request under the same
            // identifier — so a path that returns *before* the add leaves the
            // previous request alive, and it fires on its old date with its old
            // body. That contradicts the contract `syncFreeScanReminder`
            // documents for itself ("the previous request is simply replaced by
            // the next correct one") and breaks the one-per-day cap this
            // function exists to enforce, because the leaked request still
            // occupies its old day. The past-date guard above already cancels;
            // this one did not.
            center.removePendingNotificationRequests(withIdentifiers: [id])
            return false
        }

        let content = UNMutableNotificationContent()
        content.title = "SnapWorth"
        content.body = body
        content.sound = .default
        content.userInfo = ["category": category.rawValue]

        let trigger = UNCalendarNotificationTrigger(
            dateMatching: Self.triggerComponents(for: category, fireDate: fireDate),
            repeats: false)
        let request = UNNotificationRequest(identifier: id, content: content, trigger: trigger)

        do {
            try await center.add(request)   // same identifier replaces any pending
            if track {
                Analytics.shared.track(.notificationScheduled(category: category.rawValue))
            }
            return true
        } catch {
            // Scheduling is best-effort; a failure just means no reminder.
            return false
        }
    }

    /// Whether the category's fire date means an instant or a wall-clock time.
    ///
    /// Four of the five mean wall clock. "Your free scan is back" at 18:00 is
    /// 18:00 wherever the user wakes up; the recap, the ledger follow-up and
    /// the weekly digest are all the same — 10:00 local, whatever local turns
    /// out to be. Those must float with the device's zone.
    ///
    /// `.trial` is the exception, and the only one. Its fire date is derived
    /// from an absolute deadline — 24 hours before Apple charges the card —
    /// and the body says "ends tomorrow". Floating it means a user who flies
    /// gets the courtesy warning at the wrong remove from the charge: Tokyo to
    /// Los Angeles is sixteen hours, which turns a day's notice into eight
    /// hours, potentially past the point where cancelling still avoids the
    /// first bill.
    nonisolated static func isAnchoredToAnInstant(_ category: Category) -> Bool {
        switch category {
        case .trial:                              return true
        case .recap, .ledger, .portfolio, .freeScan: return false
        }
    }

    /// The components the trigger is built from — floating or pinned.
    ///
    /// `DateComponents` with no `timeZone` is resolved against whatever zone
    /// the device is in when the trigger is evaluated, which is the floating
    /// behaviour four of the categories want. Setting `timeZone` pins them to
    /// the zone they were computed in, so the same instant comes back out.
    /// `.second` comes along for the pinned case: the deadline is an instant,
    /// and truncating it to the minute would move the warning by up to 59
    /// seconds for no reason.
    ///
    /// Pure, and takes its calendar, so a test can compute in one zone and
    /// resolve in another — which is the whole claim.
    nonisolated static func triggerComponents(for category: Category,
                                              fireDate: Date,
                                              calendar: Calendar = .current) -> DateComponents {
        var comps = calendar.dateComponents([.year, .month, .day, .hour, .minute],
                                            from: fireDate)
        guard isAnchoredToAnInstant(category) else { return comps }
        comps.second = calendar.component(.second, from: fireDate)
        comps.timeZone = calendar.timeZone
        return comps
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

    /// Built per call rather than held in a `static let`.
    ///
    /// `Calendar.current` is a non-autoupdating snapshot carrying a concrete
    /// `TimeZone`, and a `static let` is initialised once per process — so this
    /// formatter's zone was pinned at first use while every other helper in
    /// this section re-reads `Calendar.current` freshly, including
    /// `date(fromDayKey:)`, which parsed with the pinned zone and then took the
    /// components with a fresh one.
    ///
    /// Fly from Los Angeles to Tokyo and resume without relaunching: an item
    /// listed Sep 11 gives a fire date of Sep 25 10:00 JST, `dayKey` formats
    /// that instant in the pinned LA zone as "20260924", and rescheduling from
    /// that key lands the 14-day follow-up on Sep 24 — a day early. Two items
    /// on adjacent days can also coalesce into the wrong bucket.
    ///
    /// `monthName` below is built per call for exactly this reason, so this is
    /// the file's own convention rather than a new one. The cost is one
    /// `DateFormatter` per ledger scheduling, which happens when an item is
    /// marked listed.
    private static var dayKeyFormatter: DateFormatter {
        let calendar = Calendar.current
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.calendar = calendar
        // Set explicitly: `DateFormatter` keeps its own `timeZone` and does not
        // take one from the calendar assigned above.
        f.timeZone = calendar.timeZone
        f.dateFormat = "yyyyMMdd"
        return f
    }

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
