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
    @ObservationIgnored
    private let freeScansKey = "snapworth_free_scans_used"
    @ObservationIgnored
    private let freeScansDateKey = "snapworth_free_scans_date"

    /// Free scans used *today*. The count is stamped with the day it was
    /// written; once the local calendar day rolls over the stamp is stale, so
    /// the getter reads 0 and the user gets a fresh `Config.freeScansAllowed`
    /// — i.e. "3 free scans every day," matching the App Store listing.
    /// (Legacy installs have a count but no date stamp → they read 0 and are
    /// immediately unstuck, which is the intended behavior.)
    var freeScansUsed: Int {
        get {
            let defaults = UserDefaults.standard
            guard let stamped = defaults.object(forKey: freeScansDateKey) as? Date,
                  Calendar.current.isDateInToday(stamped) else {
                return 0
            }
            return defaults.integer(forKey: freeScansKey)
        }
        set {
            let defaults = UserDefaults.standard
            defaults.set(newValue, forKey: freeScansKey)
            defaults.set(Date(), forKey: freeScansDateKey)
        }
    }

    var hasFreeScanRemaining: Bool {
        freeScansUsed < Config.freeScansAllowed
    }

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
