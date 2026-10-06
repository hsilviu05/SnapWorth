import Foundation

// MARK: - A thin panel, fresh or reopened

/// What "Why this price" says about a find that was saved with only the free
/// part of the panel (`ValuationDetail.lacksProDetail`), to a subscriber and
/// to a free user deciding whether to become one.
///
/// A re-read of the stored photo returns the full panel, and replaces the
/// estimate, the name, the details and the listing draft with it
/// (`applySharpened`). So it keeps the tag re-read's rule: a fresh result
/// only. A find reopened from My Finds or My Flips may have been priced,
/// listed or sold on the number it has, and re-pricing it weeks later would
/// rewrite a number the user has already acted on. It is told why its panel
/// is thin instead, and nothing re-reads it, including a purchase made from
/// its own teaser. That is the owner's decision.
///
/// Which is why the free teaser is decided here too. On such a find a
/// purchase delivers `scannedBeforePro`, not the breakdown, and the teaser
/// that sold it — "Unlock why this price", four price points and what drives
/// the value — described a panel this find will never show. It says the
/// breakdown comes with new scans instead (`teaserNewScansOnly`).
///
/// A value rather than conditions in the view, so the one rule that matters,
/// that only a fresh result is ever re-read, is tested directly, and so is
/// what the teaser promises either side of it.
enum FullDetailOffer: Equatable {
    /// Nothing to add: the panel is full or there is none, or the free teaser
    /// is showing on a find that buying would deliver — a fresh result, which
    /// is re-read after the purchase.
    case none
    /// A fresh result: "Show the full breakdown", and the automatic re-read
    /// when the paywall opened from this panel closes on a purchase.
    case reread
    /// Reopened from My Finds or My Flips: a label saying why, never a re-read.
    case scannedBeforePro
    /// The free teaser on a find reopened from My Finds or My Flips. Buying
    /// turns it into `scannedBeforePro`, so the teaser offers Pro for new
    /// scans rather than this find's breakdown.
    case teaserNewScansOnly

    /// Takes `isFreshScan`, not `coverPrice`: the cover is a presentation
    /// choice, and this is about what happened — see `ResultView.isFreshScan`.
    ///
    /// A full panel needs nothing either way. That includes a lapsed
    /// subscriber's find from their Pro months: the free teaser is shown, and
    /// re-subscribing shows the panel the find was saved with.
    init(isPro: Bool, isFreshScan: Bool, detail: ValuationDetail?) {
        guard detail?.lacksProDetail == true else {
            self = .none
            return
        }
        switch (isPro, isFreshScan) {
        case (true, true):   self = .reread
        case (true, false):  self = .scannedBeforePro
        case (false, true):  self = .none
        case (false, false): self = .teaserNewScansOnly
        }
    }
}
