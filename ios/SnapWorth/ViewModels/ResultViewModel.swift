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
    /// A subscriber's listing was refused 402 even after their subscription
    /// was re-sent — see `PurchaseService.confirmingSubscription`.
    var showSubscriptionUnconfirmed = false
    /// The listing request. `ListingAPIClient.shared` in the app; a test
    /// replaces it, as it does `scanner`.
    @ObservationIgnored var listingGenerator: (ListingInput, Marketplace) async throws -> GeneratedListing = {
        try await ListingAPIClient.shared.generate($0, marketplace: $1)
    }

    // ── Re-reads: the tag (#88) and the full breakdown (#87) ───────────────
    // Moved out of `ResultView` (#229) so the rules they keep are tested by
    // running them, not by reading the view's source.
    /// One paid re-read at a time; the buttons and every caller check it.
    var isRescanning = false
    var tagError: String?
    /// Success counterpart to `tagError` — see `rescanWithTag`.
    var tagSuccess: String?
    /// Why the full-breakdown re-read failed — see `rereadForFullDetail`.
    var fullDetailError: String?
    /// The paid scan both re-reads make: the stored item photo, and a tag
    /// photo for the tag re-read. `ScanAPIClient.shared` in the app; a test
    /// replaces it.
    @ObservationIgnored var scanner: (UIImage, UIImage?) async throws -> ScanAPIResponse = {
        try await ScanAPIClient.shared.scan(image: $0, tagImage: $1)
    }

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

    // MARK: Re-reads

    /// Whether closing the paywall should re-read the find: bought from "Unlock
    /// why this price" on a fresh result, which is what they paid to see. A
    /// find reopened from My Finds or My Flips is never re-read after a
    /// purchase; its teaser said so, and its panel says why (`FullDetailOffer`).
    nonisolated static func rereadsAfterPaywall(trigger: PaywallTrigger, offer: FullDetailOffer) -> Bool {
        trigger == .valuationDetail && offer == .reread
    }

    /// Re-scan with both photos and replace the estimate in place.
    ///
    /// The item photo is the one already stored on the result, so the user
    /// photographs the label only. A failure leaves the original estimate
    /// exactly as it was and says so — the first answer was paid for and must
    /// not be lost to a second attempt. `onApplied` runs after the new answer
    /// is on `result`, for what only the sheet holds (`valuationDidChange`,
    /// the reveal).
    func rescanWithTag(_ tagImage: UIImage, photo: UIImage?, result: ScanResult,
                       purchaseService: any PurchaseService,
                       onApplied: () -> Void) async {
        // The button checks this too, but the button is not the only caller:
        // the tag sheet is. Two re-scans in flight would each spend a scan,
        // the later answer would win, and the first to finish would clear
        // "Re-reading…" while the other was still running.
        guard !isRescanning else { return }
        guard let photo else {
            tagError = String(localized: "The original photo is no longer available for this find.")
            return
        }
        isRescanning = true
        defer { isRescanning = false }
        tagError = nil
        tagSuccess = nil
        // A paid model call, like any scan — see `BackgroundScanActivity`.
        let background = BackgroundScanActivity.begin("Tag re-read")
        defer { background.end() }
        do {
            let response = try await purchaseService.confirmingSubscription {
                try await scanner(photo, tagImage)
            }
            result.applySharpened(response)
            onApplied()
            Haptics.success()
            // A haptic is the whole of the feedback a sighted user gets, and
            // the estimate may not visibly move at all — so on success this
            // said nothing, and said nothing at all to VoiceOver. Both are
            // fixed here: a line that persists, and an announcement.
            tagSuccess = String(localized: "Re-read with the tag. Estimate updated.")
            UIAccessibility.post(notification: .announcement, argument: tagSuccess ?? "")
            Analytics.shared.track(.tagPhotoAdded(succeeded: true))
        } catch {
            Haptics.failure()
            Analytics.shared.track(.tagPhotoAdded(succeeded: false))
            // A subscriber the server would not recognise: the alert, with
            // Restore and support, rather than a line under a Pro card.
            if AppError.from(error) == .subscriptionUnconfirmed {
                showSubscriptionUnconfirmed = true
                return
            }
            tagError = AppError.from(error).errorDescription
                ?? String(localized: "That didn't work. Your estimate is unchanged.")
        }
    }

    /// Re-reads the stored photo so a subscriber gets the panel a free scan
    /// was never sent — see `ValuationDetail.lacksProDetail`.
    ///
    /// A fresh result only, like the tag re-read. `FullDetailOffer` decides,
    /// and it is checked here as well as at the button and the paywall's
    /// dismissal, so no path re-prices a find reopened from My Finds or My
    /// Flips.
    ///
    /// The same machinery as the tag re-read, and like it this replaces the
    /// estimate: the ladder has to explain the number beside it, so taking the
    /// new detail and keeping the old range would show a breakdown of a price
    /// the app no longer states.
    func rereadForFullDetail(offer: FullDetailOffer, photo: UIImage?, result: ScanResult,
                             purchaseService: any PurchaseService,
                             onApplied: () -> Void) async {
        guard !isRescanning, offer == .reread else { return }
        guard let photo else {
            fullDetailError = String(localized: "The original photo is no longer available for this find.")
            return
        }
        isRescanning = true
        defer { isRescanning = false }
        fullDetailError = nil
        let background = BackgroundScanActivity.begin("Full breakdown")
        defer { background.end() }
        // A server that still reads this device as free does not refuse the
        // scan — it answers it, off the free allowance, stripped of exactly
        // what this is for. Right after a purchase it usually does: the
        // purchase tells the server in a detached task. So wait for the server
        // to agree first, and do not scan if it will not.
        if !Config.mockScans {
            switch await purchaseService.resyncEntitlement() {
            case .confirmed:
                break
            case .notSubscribed:
                return                  // the panel is back to the teaser
            case .unreachable(let reason, let error):
                // Offline, timed out, rate-limited or down. Unlike a 402's
                // resync, nothing here has shown the network works, and
                // telling someone on a train that Apple and SnapWorth disagree
                // about their subscription is not what happened.
                Analytics.shared.track(.entitlementSyncFailed(reason: reason))
                Haptics.failure()
                fullDetailError = error.errorDescription
                    ?? String(localized: "We couldn't load the full breakdown. Your estimate is unchanged.")
                return
            case .failed(let reason):
                Analytics.shared.track(.entitlementSyncFailed(reason: reason))
                showSubscriptionUnconfirmed = true
                return
            }
        }
        do {
            let response = try await purchaseService.confirmingSubscription {
                try await scanner(photo, nil)
            }
            // Still stripped: applying it would move the estimate and leave
            // the panel as thin as before.
            guard let detail = ValuationDetail(response: response), !detail.lacksProDetail else {
                Haptics.failure()
                fullDetailError = String(localized: "We couldn't load the full breakdown. Your estimate is unchanged.")
                return
            }
            result.applySharpened(response)
            onApplied()
            Haptics.success()
            UIAccessibility.post(notification: .announcement,
                                 argument: String(localized: "Full breakdown loaded."))
        } catch {
            Haptics.failure()
            if AppError.from(error) == .subscriptionUnconfirmed {
                showSubscriptionUnconfirmed = true
                return
            }
            fullDetailError = AppError.from(error).errorDescription
                ?? String(localized: "We couldn't load the full breakdown. Your estimate is unchanged.")
        }
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
    func generateListing(result: ScanResult, purchaseService: any PurchaseService) async {
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
            // /listing is Pro-only, and a subscriber the server has not heard
            // about gets its 402 — which read, in red under the Pro badge,
            // "This is a Pro feature" to someone paying for Pro.
            let listing = try await purchaseService.confirmingSubscription {
                try await listingGenerator(input, requested)
            }
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
            if AppError.from(error) == .subscriptionUnconfirmed {
                // Its own alert, with Restore and support; the Generate
                // button stays for a retry.
                showSubscriptionUnconfirmed = true
                return
            }
            listingError = AppError.from(error).errorDescription
        }
    }

    func copyGeneratedListing() {
        guard let listing = generatedListing else { return }
        UIPasteboard.general.string = listing.shareText
        Analytics.shared.track(.listingCopied(marketplace: listing.marketplace.rawValue))
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
        Analytics.shared.track(.marketplaceOpened(marketplace: marketplace.rawValue))
        marketplace.openSellPage()
    }

    func copyListing(result: ScanResult) {
        let text = """
        \(result.listingTitle)

        \(result.listingDescription)

        Asking: \(result.formattedRange)
        Condition: \(result.conditionNotes)
        """
        UIPasteboard.general.string = text
        Analytics.shared.track(.listingCopied(marketplace: "draft"))

        withAnimation { didCopyListing = true }

        resetTask?.cancel()
        resetTask = Task {
            try? await Task.sleep(for: .seconds(2))
            guard !Task.isCancelled else { return }
            withAnimation { didCopyListing = false }
        }
    }
}

extension Marketplace {
    /// Opens the marketplace so the user can paste a copied listing. Prefers a
    /// real app URL scheme (foregrounds the installed app), else the public
    /// "create listing" web page. Never auto-posts — see `webSellURL`.
    ///
    /// Here rather than on `ResultViewModel` because the haul summary (#93)
    /// opens the same page for a batch of drafts, with no result to hang it on.
    @MainActor
    func openSellPage() {
        if let scheme = appURLScheme, UIApplication.shared.canOpenURL(scheme) {
            UIApplication.shared.open(scheme)
        } else {
            UIApplication.shared.open(webSellURL)
        }
    }
}
