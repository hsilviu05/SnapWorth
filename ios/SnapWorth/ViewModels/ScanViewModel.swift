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
    func startScan(image: UIImage, purchaseService: any PurchaseService, repository: ScanRepository) async {
        guard !isAnalyzing else { return }
        guard purchaseService.isSubscribed || hasFreeScanRemaining else {
            Analytics.shared.track(.freeScanLimitHit)
            paywallTrigger = .scanLimit
            showPaywall = true
            return
        }

        Analytics.shared.track(.scanStarted)
        isAnalyzing = true
        errorMessage = nil
        saveFailed = false
        defer { isAnalyzing = false }

        do {
            let response = try await ScanAPIClient.shared.scan(image: image)

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
                imageData: jpegData
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
                .scanCompleted(success: true, category: ItemCategory(normalizing: response.category))
            )
            Self.noteScanForStreakAndReminder(isPro: purchaseService.isSubscribed)

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
            }

            // Ask for a rating on a high point — after the result is on screen.
            Task {
                try? await Task.sleep(for: .seconds(1.2))
                ReviewPrompt.recordSuccessfulScan()
            }

            // Only scans schedule the monthly recap — never app launch — so a
            // quiet month fires nothing. Fires once this month reaches 3 scans.
            let monthScans = repository.countScansThisMonth()
            Task { await NotificationManager.shared.scheduleMonthlyRecap(monthScanCount: monthScans) }

        } catch {
            Haptics.failure()
            let appError = AppError.from(error)
            errorMessage = appError.errorDescription
            Analytics.shared.track(.scanFailed(reason: ScanFailureReason(appError)))
        }
    }

    func loadSelectedPhoto() async {
        guard let item = selectedPhotoItem else { return }
        if let data = try? await item.loadTransferable(type: Data.self),
           let image = UIImage(data: data) {
            capturedImage = image
        } else {
            errorMessage = "Couldn't load the selected photo. Please try another."
        }
        selectedPhotoItem = nil
    }

    func reset() {
        capturedImage = nil
        scanResult = nil
        errorMessage = nil
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
enum ScanStreak {
    static let countKey = "snapworth_streak_count"
    static let lastKey = "snapworth_streak_last"

    /// Record a scan that just succeeded. Returns the streak after it.
    @discardableResult
    static func record(now: Date = Date(), defaults: UserDefaults = .standard,
                       calendar: Calendar = .current) -> Int {
        let count = defaults.integer(forKey: countKey)
        if let last = defaults.object(forKey: lastKey) as? Date {
            if calendar.isDate(last, inSameDayAs: now) { return max(count, 1) }
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

    /// Whether a scan has been recorded today — any tier, so the reminder
    /// logic does not depend on the free counter.
    static func scannedToday(now: Date = Date(), defaults: UserDefaults = .standard,
                             calendar: Calendar = .current) -> Bool {
        guard let last = defaults.object(forKey: lastKey) as? Date else { return false }
        return calendar.isDate(last, inSameDayAs: now)
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

extension ScanViewModel {
    /// Shared by the camera scan and Thrift Flip: advance the streak, report
    /// its bucket, and move the free-scan reminder to tomorrow — today's
    /// allowance is spent, so today's nudge would be a lie.
    static func noteScanForStreakAndReminder(isPro: Bool) {
        let streak = ScanStreak.record()
        Analytics.shared.track(.scanStreak(bucket: ScanStreak.bucket(streak)))
        Task {
            await NotificationManager.shared.syncFreeScanReminder(
                isPro: isPro, scannedToday: true, streak: streak)
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

    static var used: Int {
        get {
            let defaults = UserDefaults.standard
            guard let stamped = defaults.object(forKey: dateKey) as? Date,
                  Calendar.current.isDateInToday(stamped) else {
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
                  Calendar.current.isDateInToday(stamped),
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
