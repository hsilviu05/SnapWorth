import Photos
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

    // ── Listing photo cleanup (#91) ──────────────────────────────────────────
    // The cut-out is kept, not just the export: changing marketplace or
    // backdrop re-composes in a few milliseconds instead of re-running Vision.
    var photoBackdrop: ListingPhotoBackdrop = .white {
        didSet { recomposePhoto() }
    }
    var isCleaningPhoto = false
    /// Set when the photo could not be cleaned; the original is untouched.
    var photoCleanupNote: String?
    var didCopyPhoto = false
    var photoSaveMessage: String?
    @ObservationIgnored private var photoCutOut: ListingPhotoCleanup.CutOut?
    private(set) var cleanedPhoto: UIImage?

    @ObservationIgnored private var resetTask: Task<Void, Never>?
    @ObservationIgnored private var shareCardDebounce: Task<Void, Never>?
    @ObservationIgnored private var copyGeneratedResetTask: Task<Void, Never>?
    @ObservationIgnored private var copyPhotoResetTask: Task<Void, Never>?

    deinit {
        resetTask?.cancel()
        shareCardDebounce?.cancel()
        copyGeneratedResetTask?.cancel()
        copyPhotoResetTask?.cancel()
    }

    // MARK: Listing photo cleanup

    var photoCanvas: ListingPhotoCanvas { ListingPhotoCanvas(marketplace: selectedMarketplace) }

    func cleanUpPhoto(_ photo: UIImage, masker: any ForegroundMasking = VisionForegroundMasker()) async {
        guard !isCleaningPhoto else { return }
        isCleaningPhoto = true
        defer { isCleaningPhoto = false }
        photoCleanupNote = nil
        photoSaveMessage = nil

        switch await ListingPhotoCleanup.cutOut(photo, masker: masker) {
        case .success(let cut):
            photoCutOut = cut
            recomposePhoto()
            Analytics.shared.track(.listingPhotoCleaned(marketplace: selectedMarketplace.rawValue))
        case .failure:
            // Never a half-cut item: keep the original and say so.
            photoCutOut = nil
            cleanedPhoto = nil
            photoCleanupNote = String(localized: "Couldn't find a clear item in this photo, so it's left as it was.")
        }
    }

    /// Re-place the kept cut-out for the current marketplace and backdrop.
    func recomposePhoto() {
        guard let photoCutOut else { return }
        cleanedPhoto = ListingPhotoCleanup.compose(photoCutOut, canvas: photoCanvas, backdrop: photoBackdrop)
        photoSaveMessage = nil
    }

    func copyCleanedPhoto() {
        guard let cleanedPhoto else { return }
        UIPasteboard.general.image = cleanedPhoto
        withAnimation { didCopyPhoto = true }
        copyPhotoResetTask?.cancel()
        copyPhotoResetTask = Task {
            try? await Task.sleep(for: .seconds(2))
            guard !Task.isCancelled else { return }
            withAnimation { didCopyPhoto = false }
        }
    }

    /// Add-only access: SnapWorth never needs to read the library to save.
    func saveCleanedPhoto() async {
        guard let cleanedPhoto else { return }
        let status = await PHPhotoLibrary.requestAuthorization(for: .addOnly)
        guard status == .authorized || status == .limited else {
            photoSaveMessage = String(localized: "SnapWorth can't add to your photos. Allow it in Settings → SnapWorth → Photos.")
            return
        }
        do {
            try await PHPhotoLibrary.shared().performChanges {
                PHAssetChangeRequest.creationRequestForAsset(from: cleanedPhoto)
            }
            photoSaveMessage = String(localized: "Saved to Photos")
        } catch {
            photoSaveMessage = String(localized: "Couldn't save the photo. Try again.")
        }
    }

    /// Render scale for every share card.
    ///
    /// Fixed at 2, not `max(displayScale, 2)`. The canvas is 540×960pt, so
    /// scale 2 is exactly the 1080×1920px these cards target — the size
    /// Instagram Stories, WhatsApp status and TikTok all want. On a 3× phone
    /// the old expression rendered 1620×2880 instead: an 18.7MB bitmap, on the
    /// main actor, for an image that is downsampled again by every one of those
    /// destinations. Nothing is gained and the hitch is real, so it is capped.
    static let shareCardScale: CGFloat = 2

    func prepareShareCard(result: ScanResult, photo: UIImage?) {
        let view = ShareCardView(result: result, photo: photo)
        let renderer = ImageRenderer(content: view)
        renderer.scale = Self.shareCardScale
        shareCard = renderer.uiImage
    }

    /// The two "Guess the price" cards — the question with the estimate
    /// covered, and the reveal. Rendered on demand when the game opens; the
    /// standard card above stays the one prepared eagerly for the toolbar.
    func renderGuessCards(result: ScanResult, photo: UIImage?) -> (guess: UIImage?, reveal: UIImage?) {
        func render(_ style: GuessCardStyle) -> UIImage? {
            let renderer = ImageRenderer(content: GuessShareCardView(result: result, photo: photo, style: style))
            renderer.scale = Self.shareCardScale
            return renderer.uiImage
        }
        return (render(.guess), render(.reveal))
    }

    func scheduleShareCardUpdate(result: ScanResult, photo: UIImage?) {
        shareCardDebounce?.cancel()
        shareCardDebounce = Task {
            try? await Task.sleep(for: .milliseconds(300))
            guard !Task.isCancelled else { return }
            prepareShareCard(result: result, photo: photo)
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
        // Depop is 4:5 and the rest square; the kept cut-out re-places in ms.
        recomposePhoto()
    }

    /// Generates a marketplace listing for the current condition + marketplace.
    /// On failure sets `listingError` (the UI shows a retry) — never a blank listing.
    func generateListing(result: ScanResult) async {
        guard !isGeneratingListing else { return }
        isGeneratingListing = true
        listingError = nil
        defer { isGeneratingListing = false }

        // Snapshot the model here, on the MainActor, before anything crosses to
        // the actor. `ListingAPIClient` used to take the `ScanResult` itself
        // and read its SwiftData-backed properties on a cooperative-pool
        // thread while the main thread was free to mutate the same object —
        // see `ListingInput`.
        let input = ListingInput(result: result, condition: result.condition)
        // The request is identified by what it was written for, the same way
        // `input.condition` identifies the price it quotes. Neither chip is
        // disabled while this runs — only the Generate button is — and both
        // clear the listing on the way out, `selectMarketplace` here and
        // `valuationDidChange` in the view. So the user could tap Vinted, see
        // the eBay draft correctly disappear, and then watch it reinstate
        // itself when the in-flight response landed: eBay's voice under the
        // Vinted chip, at eBay's Ask and Floor, behind an "Open eBay" button.
        // The backend tailors both voice and price per marketplace, so this is
        // not a cosmetic mismatch.
        let requested = selectedMarketplace

        do {
            let listing = try await ListingAPIClient.shared.generate(
                input, marketplace: requested
            )
            // Stale: the user moved on and has already seen this cleared, so
            // drop it rather than put it back. The `defer` above still resets
            // `isGeneratingListing`, which brings the Generate button back for
            // the new selection — and a listing nobody is shown is not a
            // generated listing, so the event below is skipped with it.
            guard requested == selectedMarketplace,
                  input.condition == result.condition else { return }
            generatedListing = listing
            // Fired on success, not on attempt. Previously this ran before the
            // network call, so every timeout and failure counted as a generated
            // listing — inflating the headline adoption metric for a brand-new
            // feature with exactly the cases where it didn't work.
            Analytics.shared.track(.listingGenerated(marketplace: requested.rawValue))
        } catch {
            // Same test on the failure path: a retry banner for a request the
            // user has already navigated away from is noise, and it would sit
            // under a chip whose own draft is perfectly fine.
            guard requested == selectedMarketplace,
                  input.condition == result.condition else { return }
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
