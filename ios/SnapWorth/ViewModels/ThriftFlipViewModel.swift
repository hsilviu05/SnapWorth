import SwiftUI
import SwiftData

/// Drives Thrift Flip: scan an item (reusing the valuation core), read its shelf
/// price (OCR or manual), and get an instant buy/skip profit verdict. Money math
/// is done in `Decimal` via `FlipMath`.
@MainActor
@Observable
final class ThriftFlipViewModel {
    // ── Item scan ──────────────────────────────────────────────────────────────
    var itemImage: UIImage?
    var scanResult: ScanResult?
    var isScanningItem = false
    var scanError: String?
    var showPaywall = false

    // ── Inputs ───────────────────────────────────────────────────────────────
    // Each of the four inputs re-syncs an already-saved ledger row — see
    // `syncSavedLedgerRow`. All four, not just the shop price: the fees stored
    // with the row are computed from the resale price, the shipping and the
    // marketplace as well.
    var selectedMarketplace: Marketplace = .ebay {
        didSet { syncSavedLedgerRow() }
    }
    /// What the item costs in the shop — the purchase price. From OCR or manual.
    var shelfPriceText = "" {
        didSet { syncSavedLedgerRow() }
    }
    /// Expected resale price; defaults to the scan's condition-adjusted "likely".
    var resalePriceText = "" {
        didSet { syncSavedLedgerRow() }
    }
    /// Expected shipping cost the seller eats; blank == 0.
    var shippingText = "" {
        didSet { syncSavedLedgerRow() }
    }

    // ── OCR ────────────────────────────────────────────────────────────────────
    var isReadingTag = false
    var ocrNote: String?
    var didSaveToLedger = false
    /// Set when the ledger write itself failed — see `saveToLedger`.
    var saveError: String?

    /// Set when the scan itself succeeded but the find could not be added to
    /// My Finds — see `persistToLibrary`. Distinct from `scanError`, which
    /// means there is no verdict to show.
    var libraryWarning: String?

    // ── Scan the item (respects the shared daily free-scan cap) ─────────────────
    func scanItem(image: UIImage, purchaseService: any PurchaseService,
                  repository: ScanRepository) async {
        guard !isScanningItem else { return }
        // Base valuation stays open, but a scan is a scan — enforce the same daily
        // cap here so Thrift Flip can't be used to bypass it.
        guard purchaseService.isSubscribed || FreeScanCounter.hasRemaining else {
            Analytics.shared.track(.freeScanLimitHit)
            showPaywall = true
            return
        }

        isScanningItem = true
        scanError = nil
        itemImage = image
        defer { isScanningItem = false }

        do {
            let response = try await ScanAPIClient.shared.scan(image: image)
            // Encoded off the main actor — see ScanAPIClient.encodeForStorage.
            let storedImage = await ScanAPIClient.encodeForStorage(image)
            // Drop the original now the encodes are done: the only surface that
            // shows it is a 64pt header thumbnail, and holding the picker's
            // untouched image for the rest of the session was the largest
            // allocation in this flow.
            itemImage = await ScanAPIClient.thumbnail(image, side: 64)
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
                imageData: storedImage,
                valuationDetailData: ValuationDetail(response: response)?.encoded()
            )
            scanResult = result
            // Seed the resale field with the condition-adjusted likely value.
            resalePriceText = Self.moneyField(result.priceRange(for: result.condition).likely)

            if !purchaseService.isSubscribed {
                FreeScanCounter.increment()
                // The server's count, not just the local one — `remaining`
                // prefers the server value, so incrementing alone left the Scan
                // tab showing the pre-scan number. On a FREE_SCANS_FIRST_DAY=3
                // welcome day that meant three Thrift Flip scans still read
                // "3 left", and the fourth 402'd. This is the daily trigger for
                // the dead-end alert I-23 fixes; both were needed.
                if let remaining = response.freeScansRemaining {
                    FreeScanCounter.serverRemaining = remaining
                }
            }
            Analytics.shared.track(
                .scanCompleted(success: true, category: ItemCategory(normalizing: response.category))
            )
            ScanViewModel.noteScanForStreakAndReminder(isPro: purchaseService.isSubscribed)
            persistToLibrary(result, repository: repository)
        } catch {
            let appError = AppError.from(error)

            // Same 402-is-the-paywall rule as ScanViewModel.startScan: this
            // path shares the daily cap, so it shares the local-vs-UTC day skew
            // that lets a spent allowance past the pre-flight gate.
            if appError.isPaywall {
                Analytics.shared.track(.freeScanLimitHit)
                showPaywall = true
                return
            }

            scanError = appError.errorDescription
            Analytics.shared.track(.scanFailed(reason: ScanFailureReason(appError)))
        }
    }

    // ── Read the price tag (on-device OCR, manual fallback) ─────────────────────

    /// Which item the in-flight read belongs to. Bumped when a read begins and
    /// when the form is cleared.
    ///
    /// `private(set)` and the decision split into `applyOCR` because
    /// `readPriceTag` needs a `UIImage` and a Vision request, neither of which
    /// a unit test can supply — so the rule is tested apart from the thing it
    /// governs, the same split `hasExpired` and `isFresh` use.
    private(set) var ocrGeneration = 0

    func readPriceTag(image: UIImage) async {
        ocrGeneration += 1
        let generation = ocrGeneration
        isReadingTag = true
        ocrNote = nil
        do {
            let price = try await PriceTagOCR.detectPrice(in: image)
            applyOCR(.success(price), generation: generation)
        } catch {
            applyOCR(.failure(error), generation: generation)
        }
    }

    /// Applies a read's outcome, unless the form has moved on since it started.
    ///
    /// `readPriceTag` is launched as an unstructured `Task` from the view and
    /// nothing cancelled it, while "New item" stayed enabled for the whole time
    /// the spinner was up. So a read of item A's tag finishing after the form
    /// was cleared wrote A's shop price into B's shelf field — and
    /// `calculation` went non-nil the instant B's scan seeded the resale price,
    /// so a full green or red verdict appeared, computed from the wrong item's
    /// cost, under a note about a field the user never filled in. Save that and
    /// B's ledger row carries A's `paidPrice` for good.
    ///
    /// A generation token rather than cancelling the `Task`: the view owns the
    /// `Task`, this type does not, and `PriceTagOCR.detectPrice` is not
    /// cancellable mid-request anyway — so checking whether the answer is still
    /// wanted is both simpler and the thing that actually has to be true.
    @discardableResult
    func applyOCR(_ outcome: Result<Decimal, Error>, generation: Int) -> Bool {
        guard generation == ocrGeneration else { return false }
        isReadingTag = false
        switch outcome {
        case .success(let price):
            shelfPriceText = Self.moneyField(price)
            ocrNote = "Read \(Self.money(price)) — tap to correct if it's off."
        case .failure:
            ocrNote = "Couldn't read the tag — enter the price manually."
        }
        return true
    }

    // ── Verdict ─────────────────────────────────────────────────────────────────

    /// Live profit calc. Nil until both a resale price (>0) and a shelf price are
    /// present — we never show a verdict from a missing input.
    var calculation: FlipCalculation? {
        guard let resale = Self.decimal(resalePriceText), resale > 0,
              let purchase = Self.decimal(shelfPriceText) else { return nil }
        let shipping = Self.decimal(shippingText) ?? 0
        return FlipMath.calculate(
            resalePrice: resale,
            purchasePrice: purchase,
            shippingCost: shipping,
            fee: MarketplaceFees.fee(for: selectedMarketplace)
        )
    }

    /// What the verdict card says while it is still waiting on an input, or
    /// nil once there is a verdict to show.
    ///
    /// The card hardcoded "Add the shop price to see your profit." for both
    /// missing inputs. `calculation` is nil when *either* the resale price
    /// fails to parse or is not positive, *or* the shop price fails to parse —
    /// so clearing the resale field to retype it asked for a shop price that
    /// was sitting filled in two rows above, and following the instruction
    /// never produced a verdict.
    ///
    /// The two tests differ deliberately, and match `calculation`'s own guards:
    /// a resale price has to be *positive* (a zero resale is not a listing),
    /// while a shop price only has to parse, because a free find is a real
    /// thing and zero is the honest number for it.
    var missingInputPrompt: String? {
        guard calculation == nil else { return nil }
        let hasResale = (Self.decimal(resalePriceText) ?? 0) > 0
        let hasShelf = Self.decimal(shelfPriceText) != nil
        switch (hasResale, hasShelf) {
        case (true, false):
            return "Add the shop price to see your profit."
        case (false, true):
            return "Add the expected resale price to see your profit."
        default:
            return "Add both prices to see your profit."
        }
    }

    func trackVerdict() {
        guard let calculation else { return }
        Analytics.shared.track(.thriftFlipCalculated(verdict: calculation.isProfitable ? "profit" : "loss"))
    }

    /// Adds a completed scan to My Finds, the way `ScanViewModel.startScan`
    /// already does.
    ///
    /// This screen spends the shared daily allowance on every successful scan
    /// — `FreeScanCounter.increment()` plus the server's count — and the
    /// `ScanResult` it built was only ever written by `saveToLedger`, which the
    /// UI reaches when the user is Pro, the verdict is profitable, and they
    /// press the button. So a free user could scan their whole allowance in
    /// Thrift Flip, be told 0 left and 402'd on the next one, and find My Finds
    /// empty: no record of any item, valuation or photo they had paid for. Same
    /// for a Pro user on any "Skip it".
    ///
    /// Failure is not blocking — the verdict is the thing the user came for and
    /// it stays on screen. `libraryWarning` says what did not happen, and says
    /// it differently for the two failures for the same reason `saveToLedger`
    /// does: a write that failed can be retried, a fallback launch cannot.
    /// Internal rather than private only so the tests can reach it: `scanItem`
    /// itself goes through `ScanAPIClient`, and the behaviour worth pinning is
    /// what happens to the row and the warning afterwards.
    func persistToLibrary(_ result: ScanResult, repository: ScanRepository) {
        // Taken before the call: the rollback inside `save` cannot reach it,
        // and this screen keeps pricing off `scanResult` either way.
        let backup = result.detachedCopy()
        do {
            try repository.save(result)
            libraryWarning = nil
        } catch let failure as ScanPersistenceError {
            if case .saveFailed = failure {
                // The repository rolled the shared context back, so `result` is
                // no longer registered with it. Hold the copy taken above —
                // same values, safe to read and to price for as long as this
                // screen is up. `.storeUnavailable` throws before the insert,
                // so there is nothing to swap there.
                scanResult = backup
                libraryWarning = "Couldn't add this to My Finds. The verdict below still works."
            } else {
                libraryWarning = "SnapWorth couldn't open your library on this launch, so this find won't be kept."
            }
        } catch {
            libraryWarning = "Couldn't add this to My Finds. The verdict below still works."
        }
    }

    /// Saves the flip into the "My Flips" ledger as an owned item, carrying the
    /// paid price forward so realized profit can be tracked when it sells.
    /// - Returns: whether the flip actually reached the ledger. This used to
    ///   `try?` the save and set `didSaveToLedger` regardless, so a failed
    ///   write gave a success haptic, hid the button, and lost the flip.
    @discardableResult
    func saveToLedger(repository: ScanRepository) -> Bool {
        guard let result = scanResult, let purchase = Self.decimal(shelfPriceText) else { return false }
        result.paidPrice = NSDecimalNumber(decimal: purchase).doubleValue
        // The fees the verdict was computed from, carried into the row.
        //
        // Only the paid price went across, and `ScanResult.realizedProfit` is
        // `sold − paid − (feesEstimate ?? 0)` — so the screen that had just
        // told the user "net $28.38 after $6.63 of fees and $5 shipping"
        // handed My Flips a fee-blind row that would later report $40.00, with
        // an empty Fees column in the CSV and the same overstatement inherited
        // by the monthly recap and the share card.
        //
        // Which fees, exactly, is `ledgerFees` — and `syncSavedLedgerRow` uses
        // the same rule, so a correction after the save cannot write a
        // different kind of number than the save did.
        result.feesEstimate = Self.ledgerFees(from: calculation)
        result.status = .owned
        do {
            try repository.save(result)
        } catch let failure as ScanPersistenceError {
            // "Try again" is only true of a write that failed. When the store
            // could not be opened at launch the app is running on a throwaway
            // in-memory container, so a retry succeeds exactly as silently as
            // the first attempt and is discarded at quit just the same.
            if case .storeUnavailable = failure {
                saveError = "SnapWorth couldn't open your library on this launch, so this flip can't be saved to it."
            } else {
                saveError = "Couldn't save this flip. Try again."
            }
            return false
        } catch {
            saveError = "Couldn't save this flip. Try again."
            return false
        }
        saveError = nil
        didSaveToLedger = true
        return true
    }

    /// What goes into `ScanResult.feesEstimate` for a given verdict.
    ///
    /// Shipping is always included because the user typed it. The platform fee
    /// only when the fee table had an entry: `feesUnknown` means the verdict
    /// itself was fee-blind and said so on screen, and its `platformFees` is an
    /// assumed zero — recording that would turn a stated uncertainty into a
    /// number the ledger reports as measured.
    ///
    /// Nil when there is no verdict at all, which leaves the field untouched
    /// rather than zeroing a fee that was once known.
    private static func ledgerFees(from calculation: FlipCalculation?) -> Double? {
        guard let calculation else { return nil }
        let known = calculation.feesUnknown
            ? calculation.shippingCost
            : calculation.platformFees + calculation.shippingCost
        return NSDecimalNumber(decimal: known).doubleValue
    }

    /// Keeps an already-saved ledger row in step with the inputs it came from.
    ///
    /// `didSaveToLedger` hides the save button for the rest of the session
    /// while every field stays editable and `calculation` keeps recomputing —
    /// so correcting an OCR'd $8 to the $18 the tag actually said updated the
    /// verdict on screen and left the persisted row at $8, with no button to
    /// press and nothing saying the row had gone stale. Realized profit for
    /// that item was overstated by $10 for good.
    ///
    /// Re-assigning is enough rather than re-saving: after the insert
    /// `scanResult` is the same context-managed object and the main context
    /// autosaves. Clearing the field entirely leaves the last saved value in
    /// place — an empty field asserts nothing, and a row with no paid price
    /// loses its profit.
    private func syncSavedLedgerRow() {
        guard didSaveToLedger,
              let result = scanResult,
              let purchase = Self.decimal(shelfPriceText)
        else { return }
        result.paidPrice = NSDecimalNumber(decimal: purchase).doubleValue
        if let fees = Self.ledgerFees(from: calculation) {
            result.feesEstimate = fees
        }
    }

    func reset() {
        itemImage = nil
        // Cleared *before* the text fields, and the order is load-bearing:
        // each of those has a `didSet` that re-syncs an already-saved ledger
        // row, and clearing them with `scanResult` still set would write empty
        // inputs through to the row the user just finished.
        scanResult = nil
        scanError = nil
        libraryWarning = nil
        shelfPriceText = ""
        resalePriceText = ""
        shippingText = ""
        ocrNote = nil
        didSaveToLedger = false
        saveError = nil
        // A read still in flight belongs to the item being cleared, not to the
        // next one. Its result is dropped rather than written into the form.
        ocrGeneration += 1
        isReadingTag = false
    }

    // ── Formatting helpers ──────────────────────────────────────────────────────

    /// A stored amount formatted for an editable text field (no currency symbol).
    static func moneyField(_ value: Decimal) -> String {
        let d = NSDecimalNumber(decimal: value).doubleValue
        let fmt = d.truncatingRemainder(dividingBy: 1) == 0 ? "%.0f" : "%.2f"
        return String(format: fmt, d)
    }

    /// `snapCurrencyCents`, not `snapCurrency`.
    ///
    /// This feature prints an itemised subtraction — resale, minus fees, minus
    /// shipping, minus paid, equals net — and every row went through a
    /// formatter with `maximumFractionDigits = 0`. Each row rounded
    /// independently, so the rows did not add up to the total shown beneath
    /// them: $20.75 − $3.15 − $10.40 rendered as "$21 − $3 − $10" over a net
    /// of "$7", and 21 − 3 − 10 is 8.
    ///
    /// And any profit under a dollar printed as "$0" beneath a green "Worth
    /// flipping", because `isProfitable` is the exact `netProfit > 0`. A
    /// 1-cent margin is a *correct* verdict presented as a contradiction, at
    /// exactly the boundary where the number is the whole decision.
    ///
    /// `snapCurrency` stays where it belongs: a valuation range is an estimate
    /// and cents would imply precision it does not have.
    static func money(_ value: Decimal) -> String {
        NumberFormatter.snapCurrencyCents.string(from: NSDecimalNumber(decimal: value))
            ?? "$0.00"
    }

    static func signedMoney(_ value: Decimal) -> String {
        let base = money(abs(value))
        return value < 0 ? "−\(base)" : base
    }

    static func percent(_ fraction: Decimal) -> String {
        let value = Int((NSDecimalNumber(decimal: fraction).doubleValue * 100).rounded())
        return "\(value)%"
    }

    /// Shared with ResultView's money fields — see `MoneyInput`.
    private static func decimal(_ text: String) -> Decimal? {
        MoneyInput.decimal(text)
    }
}
