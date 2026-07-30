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
            }

            Haptics.success()
            scanResult = result
            Analytics.shared.track(
                .scanCompleted(success: true, category: ItemCategory(normalizing: response.category))
            )

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

// ── Shared daily free-scan counter ────────────────────────────────────────────

/// The daily free-scan allowance, backed by UserDefaults and stamped with the
/// day it was written — so it resets each local calendar day ("3 free scans every
/// day", matching the App Store listing). Shared by the camera scan and Thrift
/// Flip so a free user can't bypass the cap through either entry point.
///
/// Legacy installs have a count but no date stamp → they read 0 and are
/// immediately unstuck, which is the intended behavior.
enum FreeScanCounter {
    private static let usedKey = "snapworth_free_scans_used"
    private static let dateKey = "snapworth_free_scans_date"

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

    static var hasRemaining: Bool { used < Config.freeScansAllowed }

    static func increment() { used += 1 }
}
