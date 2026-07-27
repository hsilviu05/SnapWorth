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
        defer { isAnalyzing = false }

        do {
            let response = try await ScanAPIClient.shared.scan(image: image)

            let jpegData = image.jpegData(compressionQuality: 0.75)
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

            try repository.save(result)

            if !purchaseService.isSubscribed {
                freeScansUsed += 1
            }

            UINotificationFeedbackGenerator().notificationOccurred(.success)
            scanResult = result
            Analytics.shared.track(
                .scanCompleted(success: true, category: ItemCategory(normalizing: response.category))
            )

            // Ask for a rating on a high point — after the result is on screen.
            Task {
                try? await Task.sleep(for: .seconds(1.2))
                ReviewPrompt.recordSuccessfulScan()
            }

            // Only scans schedule the monthly recap — never app launch — so a
            // quiet month fires nothing. Fires once this month reaches 3 scans.
            let monthScans = repository.fetchAll().filter {
                Calendar.current.isDate($0.timestamp, equalTo: Date(), toGranularity: .month)
            }.count
            Task { await NotificationManager.shared.scheduleMonthlyRecap(monthScanCount: monthScans) }

        } catch {
            UINotificationFeedbackGenerator().notificationOccurred(.error)
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
