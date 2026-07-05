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

    // ── Free scan tracking ────────────────────────────────────────────
    @ObservationIgnored
    private let freeScansKey = "snapworth_free_scans_used"

    var freeScansUsed: Int {
        get { UserDefaults.standard.integer(forKey: freeScansKey) }
        set { UserDefaults.standard.set(newValue, forKey: freeScansKey) }
    }

    var hasFreeScanRemaining: Bool {
        freeScansUsed < Config.freeScansAllowed
    }

    // ── Scan trigger ─────────────────────────────────────────────────
    func startScan(image: UIImage, purchaseService: any PurchaseService, context: ModelContext) async {
        // Gate on subscription or free scan budget
        guard purchaseService.isSubscribed || hasFreeScanRemaining else {
            showPaywall = true
            return
        }

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

            context.insert(result)
            try? context.save()

            if !purchaseService.isSubscribed {
                freeScansUsed += 1
            }

            UINotificationFeedbackGenerator().notificationOccurred(.success)
            scanResult = result

        } catch {
            UINotificationFeedbackGenerator().notificationOccurred(.error)
            errorMessage = friendlyError(error)
        }
    }

    func loadSelectedPhoto() async {
        guard let item = selectedPhotoItem else { return }
        if let data = try? await item.loadTransferable(type: Data.self),
           let image = UIImage(data: data) {
            capturedImage = image
        }
        selectedPhotoItem = nil
    }

    private func friendlyError(_ error: Error) -> String {
        let msg = error.localizedDescription.lowercased()
        if msg.contains("429") || msg.contains("rate limit") {
            return "You've hit the scan limit. Try again in an hour."
        }
        if msg.contains("network") || msg.contains("offline") || msg.contains("internet") {
            return "No internet connection. Check your network and try again."
        }
        if msg.contains("timeout") || msg.contains("timed out") {
            return "The request timed out. Please try again."
        }
        return "Something went wrong. Please try again."
    }

    func reset() {
        capturedImage = nil
        scanResult = nil
        errorMessage = nil
        isAnalyzing = false
    }
}
