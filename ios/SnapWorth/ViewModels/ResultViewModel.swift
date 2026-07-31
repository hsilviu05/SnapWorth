import SwiftUI

@MainActor
@Observable
final class ResultViewModel {
    var didCopyListing: Bool = false
    var shareCard: UIImage?

    // ── Snap → Sell ────────────────────────────────────────────────────────────
    var selectedMarketplace: Marketplace = .ebay
    var generatedListing: GeneratedListing?
    var isGeneratingListing: Bool = false
    var listingError: String?
    var didCopyGenerated: Bool = false
    /// Text to hand the system share sheet, when a listing has been generated.
    var listingShareItems: [Any]?

    @ObservationIgnored private var resetTask: Task<Void, Never>?
    @ObservationIgnored private var shareCardDebounce: Task<Void, Never>?
    @ObservationIgnored private var copyGeneratedResetTask: Task<Void, Never>?

    deinit {
        resetTask?.cancel()
        shareCardDebounce?.cancel()
        copyGeneratedResetTask?.cancel()
    }

    func prepareShareCard(result: ScanResult, photo: UIImage?, displayScale: CGFloat) {
        let view = ShareCardView(result: result, photo: photo)
        let renderer = ImageRenderer(content: view)
        renderer.scale = max(displayScale, 2)
        shareCard = renderer.uiImage
    }

    func scheduleShareCardUpdate(result: ScanResult, photo: UIImage?, displayScale: CGFloat) {
        shareCardDebounce?.cancel()
        shareCardDebounce = Task {
            try? await Task.sleep(for: .milliseconds(300))
            guard !Task.isCancelled else { return }
            prepareShareCard(result: result, photo: photo, displayScale: displayScale)
        }
    }

    // ── Snap → Sell actions ─────────────────────────────────────────────────

    /// Switches the target marketplace. Clears any listing generated for the
    /// previous one so the user never sees eBay copy under a Vinted tab.
    func selectMarketplace(_ marketplace: Marketplace) {
        guard marketplace != selectedMarketplace else { return }
        selectedMarketplace = marketplace
        generatedListing = nil
        listingError = nil
    }

    /// Generates a marketplace listing for the current condition + marketplace.
    /// On failure sets `listingError` (the UI shows a retry) — never a blank listing.
    func generateListing(result: ScanResult) async {
        guard !isGeneratingListing else { return }
        isGeneratingListing = true
        listingError = nil
        defer { isGeneratingListing = false }

        do {
            let listing = try await ListingAPIClient.shared.generate(
                for: result, condition: result.condition, marketplace: selectedMarketplace
            )
            generatedListing = listing
            // Fired on success, not on attempt. Previously this ran before the
            // network call, so every timeout and failure counted as a generated
            // listing — inflating the headline adoption metric for a brand-new
            // feature with exactly the cases where it didn't work.
            Analytics.shared.track(.listingGenerated(marketplace: selectedMarketplace.rawValue))
        } catch {
            generatedListing = nil
            listingError = AppError.from(error).errorDescription
        }
    }

    func copyGeneratedListing() {
        guard let listing = generatedListing else { return }
        UIPasteboard.general.string = listing.shareText
        withAnimation { didCopyGenerated = true }

        copyGeneratedResetTask?.cancel()
        copyGeneratedResetTask = Task {
            try? await Task.sleep(for: .seconds(2))
            guard !Task.isCancelled else { return }
            withAnimation { didCopyGenerated = false }
        }
    }

    func shareGeneratedListing() {
        guard let listing = generatedListing else { return }
        listingShareItems = [listing.shareText]
    }

    /// Opens the marketplace so the user can paste the copied listing. Prefers a
    /// real app URL scheme (foregrounds the installed app), else the public
    /// "create listing" web page. Never auto-posts — see `Marketplace.webSellURL`.
    func openMarketplace(_ marketplace: Marketplace) {
        if let scheme = marketplace.appURLScheme, UIApplication.shared.canOpenURL(scheme) {
            UIApplication.shared.open(scheme)
        } else {
            UIApplication.shared.open(marketplace.webSellURL)
        }
    }

    func copyListing(result: ScanResult) {
        let text = """
        \(result.listingTitle)

        \(result.listingDescription)

        Asking: \(result.formattedRange)
        Condition: \(result.conditionNotes)
        """
        UIPasteboard.general.string = text

        withAnimation { didCopyListing = true }

        resetTask?.cancel()
        resetTask = Task {
            try? await Task.sleep(for: .seconds(2))
            guard !Task.isCancelled else { return }
            withAnimation { didCopyListing = false }
        }
    }
}
