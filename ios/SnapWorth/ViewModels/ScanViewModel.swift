import SwiftUI
import SwiftData
import PhotosUI

@MainActor
@Observable
final class ScanViewModel {
    // ── State ─────────────────────────────────────────────────────────
    var capturedImage: UIImage?
    var isAnalyzing: Bool = false
    var scanResult: ScanResult?
    var errorMessage: String?
    /// `errorMessage` says this build is no longer served, so the alert
    /// offers the App Store beside OK. Set and cleared with it.
    var errorOffersUpdate = false
    var showPaywall: Bool = false
    var showImagePicker: Bool = false
    var selectedPhotoItem: PhotosPickerItem?

    /// True when the scan succeeded but writing it to SwiftData did not.
    ///
    /// The result is still presented — by the time persistence runs, the server
    /// has already charged a quota unit, so discarding a correct valuation
    /// because the local write failed costs the user something they paid for.
    /// The result sheet reflects this instead of claiming it was saved.
    var saveFailed: Bool = false

    /// Which trigger opened the paywall — read by the presenting sheet so
    /// `paywall_viewed` is attributed correctly (scan wall vs. upgrade tap).
    var paywallTrigger: PaywallTrigger = .scanLimit

    /// StoreKit shows a subscription and the server refused this scan even
    /// after it was re-sent. Shown instead of the paywall — see
    /// `PurchaseService.confirmingSubscription`.
    var showSubscriptionUnconfirmed = false

    /// The photo a scan-limit paywall interrupted, so a purchase can finish
    /// the scan the user started — see `takePhotoForResume`.
    ///
    /// Nothing kept it: the paywall opened, the capture was released, and a
    /// new subscriber's first Pro moment was an empty viewfinder and a second
    /// shot of the same item. Held downscaled to the upload size, which is all
    /// a scan uses, rather than as a full-resolution capture for as long as
    /// the paywall is up.
    @ObservationIgnored private var photoAwaitingPurchase: UIImage?

    // ── Free scan tracking ────────────────────────────────────────────
    // Backed by the shared `FreeScanCounter` (below) so the daily cap is enforced
    // consistently across the camera scan and Thrift Flip. Public API unchanged.
    var freeScansUsed: Int {
        get { FreeScanCounter.used }
        set { FreeScanCounter.used = newValue }
    }

    var hasFreeScanRemaining: Bool { FreeScanCounter.hasRemaining }

    /// Free scans left — the server's count when it has told us, else local.
    var freeScansRemaining: Int { FreeScanCounter.remaining }

    /// Consecutive days with at least one scan, today or yesterday included.
    var streak: Int { ScanStreak.current() }

    // ── Scan trigger ─────────────────────────────────────────────────
    /// - Parameter afterPurchase: the scan a purchase has just unblocked.
    ///   The purchase tells the server in a detached task, so this one waits
    ///   for the server to have heard first — otherwise the new subscriber's
    ///   first scan could be read as a free one, off the free allowance and
    ///   without the Pro detail.
    func startScan(image: UIImage, purchaseService: any PurchaseService, repository: ScanRepository,
                   afterPurchase: Bool = false) async {
        guard !isAnalyzing else { return }
        guard purchaseService.isSubscribed || hasFreeScanRemaining else {
            Analytics.shared.track(.freeScanLimitHit)
            paywallTrigger = .scanLimit
            showPaywall = true
            await holdForPurchase(image)
            return
        }

        // Read once, before `ScanTally.record()` moves it, so every event this
        // scan emits agrees about whether it was the first.
        let isFirst = ScanTally.isFirstScan()
        Analytics.shared.track(.scanStarted(isFirst: isFirst))
        isAnalyzing = true
        errorMessage = nil
        errorOffersUpdate = false
        saveFailed = false
        defer { isAnalyzing = false }
        // The request and the save, if the phone locks mid-scan.
        let background = BackgroundScanActivity.begin("Scan")
        defer { background.end() }
        if afterPurchase {
            // Its outcome is not needed here: a server that still refuses
            // answers 402, which `confirmingSubscription` below handles.
            _ = await purchaseService.resyncEntitlement()
        }

        do {
            let response = try await purchaseService.confirmingSubscription {
                try await ScanAPIClient.shared.scan(image: image)
            }

            // Downscaled and encoded off the main actor — see
            // ScanAPIClient.encodeForStorage. Doing this inline on the
            // MainActor cost 80-150ms of hitch exactly as the analysing
            // overlay animated out and the result sheet presented.
            let jpegData = await ScanAPIClient.encodeForStorage(image)
            let result = ScanResult(
                itemName: response.itemName,
                brand: response.brand,
                category: response.category,
                conditionNotes: response.conditionNotes,
                valueLow: response.estValueLowUsd,
                valueHigh: response.estValueHighUsd,
                confidence: response.confidence,
                soldListingsCount: response.soldListingsCount,
                listingTitle: response.listingTitle,
                listingDescription: response.listingDescription,
                imageData: jpegData,
                valuationDetailData: ValuationDetail(response: response)?.encoded()
            )

            // Present first, persist second.
            //
            // The server charges a quota unit the moment the scan succeeds, so
            // by this point the user has already paid for this valuation. A
            // local SwiftData failure must not take it away from them — and it
            // must not skip the quota increment either, or the client would
            // believe it has a free scan the server has already spent.
            if !purchaseService.isSubscribed {
                freeScansUsed += 1
                // The server just told us what is actually left. Prefer it over
                // our own arithmetic, which is based on a compiled-in limit.
                FreeScanCounter.serverRemaining = response.freeScansRemaining
            }

            Haptics.success()
            scanResult = result
            Analytics.shared.track(
                .scanCompleted(success: true, category: ScanCategory(normalizing: response.category))
            )
            if let milestone = ScanTally.record() {
                Analytics.shared.track(.scanCountMilestone(count: milestone))
            }
            Self.noteScanForStreakAndReminder(isPro: purchaseService.isSubscribed)

            // Taken before the call, because the rollback inside `save` cannot
            // reach it. The sheet is about to display `result`; if the write
            // fails, `result` is no longer registered with any context and this
            // is what the sheet holds instead. See `ScanPersistenceError`.
            let backup = result.detachedCopy()
            do {
                try repository.save(result)
            } catch {
                // Non-blocking: the result stays on screen, and the sheet's
                // footer reports that it wasn't added to My Finds rather than
                // claiming a save that didn't happen.
                //
                // Deliberately no `scanFailed` event — the scan itself
                // succeeded and already emitted `scanCompleted(success: true)`.
                // Emitting a failure for the same scan would double-count it
                // and make the funnel wrong.
                saveFailed = true
                // The repository rolls the shared context back on failure, so
                // `result` is no longer registered anywhere. Swap in the copy
                // taken above: same values, no context, safe to read for as
                // long as the sheet is up. Without this the sheet would be
                // holding a model SwiftData has discarded.
                //
                // Only `.saveFailed` needs the swap. `.storeUnavailable` is
                // thrown *before* the insert, so the result was never
                // registered with a context and the sheet is already holding
                // the right object. The footer reads the same either way:
                // "Couldn't save to My Finds — this result won't be kept",
                // which is exactly true of a fallback launch.
                if let failure = error as? ScanPersistenceError,
                   case .saveFailed = failure {
                    scanResult = backup
                }
            }

            // Counted here; the rating request itself waits for the price to
            // be revealed on the result sheet — see `ReviewPrompt`.
            ReviewPrompt.recordSuccessfulScan()

            // Only scans schedule the monthly recap — never app launch — so a
            // quiet month fires nothing. Fires once this month reaches 3 scans.
            let monthScans = repository.countScansThisMonth()
            Task { await NotificationManager.shared.scheduleMonthlyRecap(monthScanCount: monthScans) }

        } catch {
            let appError = AppError.from(error)

            // A 402 is the paywall, not a failure. It reaches here whenever the
            // pre-flight gate above and the server disagree about which day it
            // is — the client resets at local midnight, the server counts UTC
            // days — so a user mid-window sees "Scan Failed / OK" at the exact
            // moment of highest purchase intent, with no way to subscribe.
            //
            // It also filed the event as `scan_failed{reason:no_result}`, which
            // is the wrong bucket in the one funnel the free-scan experiment is
            // read against: free_scan_limit_hit -> paywall_viewed ->
            // purchase_started. A quota refusal counted as a scan failure both
            // undercounts the limit hits and inflates the failures.
            if appError.isPaywall {
                // The server has refused: there is nothing left today,
                // whatever the local count believes. The success path a
                // hundred lines up already reconciles against
                // `response.freeScansRemaining` — "prefer it over our own
                // arithmetic, which is based on a compiled-in limit" — and
                // this path, the one where the two demonstrably disagree, did
                // not write anything at all. So the counter went on
                // advertising a scan the server had already refused: the top
                // bar said "1 left", the next tap uploaded the photo only to
                // arrive at the same paywall (the server refuses in
                // `reserve_quota`, before any model call), and
                // `hasFreeScanRemaining` let Thrift Flip through on the same
                // false premise. Thrift Flip's own 402 does the same.
                FreeScanCounter.serverRemaining = 0
                Analytics.shared.track(.freeScanLimitHit)
                paywallTrigger = .scanLimit
                showPaywall = true
                await holdForPurchase(image)
                return
            }

            Haptics.failure()
            Analytics.shared.track(.scanFailed(reason: ScanFailureReason(appError), isFirst: isFirst))
            // A subscriber the server would not recognise gets a state of its
            // own, with Restore and support — never the plan they pay for.
            if appError == .subscriptionUnconfirmed {
                showSubscriptionUnconfirmed = true
                return
            }
            errorOffersUpdate = appError == .updateRequired
            errorMessage = appError.errorDescription
        }
    }

    func loadSelectedPhoto() async {
        guard let item = selectedPhotoItem else { return }
        if let data = try? await item.loadTransferable(type: Data.self),
           let image = UIImage(data: data) {
            capturedImage = image
        } else {
            errorMessage = String(localized: "Couldn't load the selected photo. Please try another.")
        }
        selectedPhotoItem = nil
    }

    /// Keeps the interrupted photo for `takePhotoForResume`. Downscaled off
    /// the main actor, after the paywall has been asked for, so the sheet is
    /// not held up by it.
    private func holdForPurchase(_ image: UIImage) async {
        let photo = await Task.detached(priority: .utility) {
            ScanAPIClient.downscale(image, maxEdge: ScanAPIClient.maxUploadEdge)
        }.value
        // Closed already: nothing is waiting for it, and a later paywall must
        // not resume a scan nobody asked for.
        guard showPaywall else { return }
        photoAwaitingPurchase = photo
    }

    /// The photo the scan-limit paywall interrupted, if the paywall has just
    /// closed on a subscriber; otherwise nil. Either way the photo is let go:
    /// a plain dismiss means the user chose not to buy, and the viewfinder is
    /// where they expect to be.
    func takePhotoForResume(purchaseService: any PurchaseService) -> UIImage? {
        defer { photoAwaitingPurchase = nil }
        guard purchaseService.isSubscribed else { return nil }
        return photoAwaitingPurchase
    }

    func reset() {
        photoAwaitingPurchase = nil
        capturedImage = nil
        scanResult = nil
        errorMessage = nil
        errorOffersUpdate = false
        saveFailed = false
        isAnalyzing = false
    }
}

// ── Scan streak ───────────────────────────────────────────────────────────────

/// Consecutive calendar days with at least one successful scan, any tier.
///
/// Local only — UserDefaults, day-stamped like `FreeScanCounter` — and
/// deliberately forgiving: a streak counts today and yesterday as alive, so
/// opening the app at 09:00 shows yesterday's streak rather than a zero that
/// scares the user off before their scan. It breaks quietly; nothing in the
/// app says "you lost it".
///
/// For the free tier this is what turns "one scan a day" from a limit into a
/// habit; for Pro it is a small badge of honour. Analytics only ever sees a
/// bucket, never the exact count.
/// How many scans this install has ever completed, and therefore what counts
/// as the user's first.
///
/// Nothing in the app knew this. `ScanStreak` counts *days*, `ReviewPrompt`
/// counts per *version*, and `FreeScanCounter` counts *today* — so "has this
/// person ever got a valuation out of us" was unanswerable, which is exactly
/// the question a retention funnel is made of.
///
/// `isFirst` is read *before* `record()`, so every event in one scan carries
/// the same answer: a first scan that fails and a first scan that succeeds both
/// report `is_first=true`, and the second scan reports false even if the first
/// never produced a result.
enum ScanTally {
    static let countKey = "snapworth_scans_completed_total"

    /// The rungs `scanCountMilestone` fires on. Sparse on purpose: the question
    /// is how far a new user gets before they stop, and past a handful of scans
    /// that is a retention question, not a first-run one.
    static let milestones = [1, 3, 5]

    static func completedCount(defaults: UserDefaults = .standard) -> Int {
        defaults.integer(forKey: countKey)
    }

    /// True until the first scan has been recorded.
    static func isFirstScan(defaults: UserDefaults = .standard) -> Bool {
        completedCount(defaults: defaults) == 0
    }

    /// True through the user's first valuation: before it, and while it is
    /// still the only one.
    ///
    /// For events that can only fire *after* `record()`. The paywall a new
    /// user actually sees opens once their first result is in — the intro
    /// paywall when the result sheet closes, or the scan-limit one after the
    /// allowance is spent — and `isFirstScan()` is false by then, so
    /// `paywall_viewed{is_first}` was false for almost every first-run
    /// paywall and a Day-0 funnel lost its bottom half. `scan_result_shown`
    /// already read `<= 1` for the same reason.
    static func isFirstRun(defaults: UserDefaults = .standard) -> Bool {
        completedCount(defaults: defaults) <= 1
    }

    /// Record a scan that produced a result. Returns the milestone this scan
    /// just crossed, or nil.
    @discardableResult
    static func record(defaults: UserDefaults = .standard) -> Int? {
        let total = completedCount(defaults: defaults) + 1
        defaults.set(total, forKey: countKey)
        return milestones.contains(total) ? total : nil
    }
}

enum ScanStreak {
    static let countKey = "snapworth_streak_count"
    static let lastKey = "snapworth_streak_last"

    /// Record a scan that just succeeded. Returns the streak after it.
    @discardableResult
    static func record(now: Date = Date(), defaults: UserDefaults = .standard,
                       calendar: Calendar = .current) -> Int {
        let count = defaults.integer(forKey: countKey)
        if let last = defaults.object(forKey: lastKey) as? Date {
            if calendar.isDate(last, inSameDayAs: now) {
                // The same streak day, but the latest instant. The free-scan
                // reminder reads this as *when* the last scan was, to find the
                // UTC reset after it, and the first scan of a local day can sit
                // in an earlier UTC day than the last one. Every streak test is
                // by local day, so moving it within the day changes none.
                defaults.set(now, forKey: lastKey)
                return max(count, 1)
            }
            if isYesterday(last, relativeTo: now, calendar: calendar) {
                defaults.set(count + 1, forKey: countKey)
                defaults.set(now, forKey: lastKey)
                return count + 1
            }
        }
        defaults.set(1, forKey: countKey)
        defaults.set(now, forKey: lastKey)
        return 1
    }

    /// The live streak: the stored count if the last scan was today or
    /// yesterday, else 0.
    static func current(now: Date = Date(), defaults: UserDefaults = .standard,
                        calendar: Calendar = .current) -> Int {
        guard let last = defaults.object(forKey: lastKey) as? Date else { return 0 }
        if calendar.isDate(last, inSameDayAs: now) || isYesterday(last, relativeTo: now, calendar: calendar) {
            return defaults.integer(forKey: countKey)
        }
        return 0
    }

    /// When the last scan happened, for the widget blob and the free-scan
    /// reminder.
    ///
    /// The widget cannot call `current()` — this store is in
    /// `UserDefaults.standard`, not the App Group — so it needs the date to
    /// apply the same today-or-yesterday test itself.
    static var lastScan: Date? {
        UserDefaults.standard.object(forKey: lastKey) as? Date
    }

    /// Coarse buckets for analytics — never the exact count.
    static func bucket(_ streak: Int) -> String {
        switch streak {
        case ..<2: return "1"
        case 2...3: return "2-3"
        case 4...6: return "4-6"
        default:    return "7+"
        }
    }

    private static func isYesterday(_ date: Date, relativeTo now: Date, calendar: Calendar) -> Bool {
        guard let yesterday = calendar.date(byAdding: .day, value: -1, to: now) else { return false }
        return calendar.isDate(date, inSameDayAs: yesterday)
    }
}

// ── Finishing a scan the phone locked on ──────────────────────────────────────

/// Asks iOS for time to finish a scan when the app leaves the foreground
/// mid-request.
///
/// Nothing did, and every scan runs on the default session: lock the phone
/// during "Analyzing…" and the process is suspended with the upload or the
/// response in flight. The server charges a scan unless it sees the client
/// disconnect first, so a free user could come back to "No internet
/// connection" after the day's only scan had been spent — and a retry is a
/// 402. A background task keeps the request and the save running for the
/// few seconds they need; it does nothing at all while the app stays in
/// front.
///
/// Begun before the request, ended in a `defer`, and ended by the expiration
/// handler if iOS runs out of patience first. `end()` is idempotent, so the
/// two cannot both end it.
@MainActor
final class BackgroundScanActivity {
    private var identifier: UIBackgroundTaskIdentifier = .invalid

    private init() {}

    static func begin(_ name: String) -> BackgroundScanActivity {
        let activity = BackgroundScanActivity()
        activity.identifier = UIApplication.shared.beginBackgroundTask(withName: name) {
            activity.end()
        }
        return activity
    }

    var isActive: Bool { identifier != .invalid }

    func end() {
        guard identifier != .invalid else { return }
        UIApplication.shared.endBackgroundTask(identifier)
        identifier = .invalid
    }
}

extension ScanViewModel {
    /// Shared by the camera scan and Thrift Flip: advance the streak, report
    /// its bucket, and move the free-scan reminder past the next UTC reset —
    /// the allowance is spent until then, so an earlier nudge would be a lie.
    static func noteScanForStreakAndReminder(isPro: Bool) {
        let streak = ScanStreak.record()
        Analytics.shared.track(.scanStreak(bucket: ScanStreak.bucket(streak)))
        Task {
            await NotificationManager.shared.syncFreeScanReminder(
                isPro: isPro, lastScan: ScanStreak.lastScan, streak: streak)
        }
    }
}

// ── Shared daily free-scan counter ────────────────────────────────────────────

/// The daily free-scan allowance, backed by UserDefaults and stamped with the
/// day it was written — so it resets each local calendar day. Shared by the
/// camera scan and Thrift Flip so a free user can't bypass the cap through
/// either entry point.
///
/// The count here is a fallback. The server is authoritative (see
/// `serverRemaining`), and `Config.freeScansAllowed` only has to match
/// `FREE_SCANS_PER_DAY` closely enough to be right before the first scan.
///
/// Legacy installs have a count but no date stamp → they read 0 and are
/// immediately unstuck, which is the intended behavior.
enum FreeScanCounter {
    private static let usedKey = "snapworth_free_scans_used"
    private static let dateKey = "snapworth_free_scans_date"
    private static let serverRemainingKey = "snapworth_free_scans_server_remaining"

    /// The allowance resets on the server's day, not the phone's.
    ///
    /// This used `Calendar.current.isDateInToday`, while `quota.py` counts UTC
    /// days. For anyone east of UTC the two disagree for as many hours as their
    /// offset — Romania (UTC+3) gets a window from local midnight to 03:00 in
    /// which the client believes the allowance has reset and the server does
    /// not. The user scans, the client lets it through, and the server answers
    /// 402: the dead-end alert I-23 fixes, arrived at from the other direction.
    ///
    /// Matching UTC here removes the disagreement rather than handling it. The
    /// cost is that a user in UTC-8 sees their allowance reset at 16:00 local,
    /// which is odd but honest — it is when the server actually resets it.
    private static func isServerToday(_ stamped: Date) -> Bool {
        var utc = Calendar(identifier: .gregorian)
        utc.timeZone = TimeZone(secondsFromGMT: 0) ?? .gmt
        return utc.isDateInToday(stamped)
    }

    static var used: Int {
        get {
            let defaults = UserDefaults.standard
            guard let stamped = defaults.object(forKey: dateKey) as? Date,
                  isServerToday(stamped) else {
                return 0
            }
            return defaults.integer(forKey: usedKey)
        }
        set {
            let defaults = UserDefaults.standard
            defaults.set(newValue, forKey: usedKey)
            defaults.set(Date(), forKey: dateKey)
        }
    }

    /// What the server last said was left, day-stamped like `used`.
    ///
    /// The server is authoritative — `ScanQuota` exists because the local count
    /// was advisory and reset on reinstall. Reading it back means the limit can
    /// change server-side without the app showing a number the backend will not
    /// honour, and a reinstall whose allowance was withheld reports 0 rather
    /// than a full allowance.
    ///
    /// `nil` means "never heard from the server today": Pro, an unreachable
    /// quota store, or no scan yet. Callers fall back to the local count.
    static var serverRemaining: Int? {
        get {
            let defaults = UserDefaults.standard
            guard let stamped = defaults.object(forKey: dateKey) as? Date,
                  isServerToday(stamped),
                  defaults.object(forKey: serverRemainingKey) != nil else {
                return nil
            }
            return defaults.integer(forKey: serverRemainingKey)
        }
        set {
            let defaults = UserDefaults.standard
            guard let newValue else {
                defaults.removeObject(forKey: serverRemainingKey)
                return
            }
            defaults.set(max(0, newValue), forKey: serverRemainingKey)
            defaults.set(Date(), forKey: dateKey)
        }
    }

    /// Free scans left: the server's figure when we have one, else the local
    /// estimate against the compiled-in allowance.
    static var remaining: Int {
        serverRemaining ?? max(0, Config.freeScansAllowed - used)
    }

    static var hasRemaining: Bool { remaining > 0 }

    static func increment() { used += 1 }
}
