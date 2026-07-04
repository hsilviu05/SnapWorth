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

            scanResult = result

        } catch {
            errorMessage = error.localizedDescription
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

    func reset() {
        capturedImage = nil
        scanResult = nil
        errorMessage = nil
        isAnalyzing = false
    }
}
