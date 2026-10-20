import SwiftUI

// The two cards that offer a re-read, out of `ResultView` (#229, stage 6):
// "Sharpen this estimate" (the tag) and "Why this price" (the full
// breakdown). The re-reads themselves are `ResultViewModel`'s (stage 2), and
// whether each is offered is the sheet's (`offersTagReread`,
// `fullDetailOffer`). These cards take what they show in their initializers,
// read none of the sheet's @State, and ask for the tag camera, the re-read
// and the paywall through closures.

/// The second photo. Identification is what the estimate rests on, and the
/// tag is the biggest single lever on it — which is why "Sharpen this
/// estimate" above so often says "photograph the tag" and, until now, gave
/// the user nowhere to put it.
struct AddTagCard: View {
    let vm: ResultViewModel
    let isPro: Bool
    /// The sheet presents `TagCameraSheet`.
    let onAddTag: () -> Void
    let onPaywall: (PaywallTrigger) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Sharpen this estimate")
                    .snapSectionHeader()
                Spacer()
                if !isPro { ProBadge() }
            }
            Text("Photograph the care tag, size label or sole stamp and we'll re-read the item with both photos.")
                .font(.snapBody)
                .foregroundStyle(Color.snapWarmGray)
                .fixedSize(horizontal: false, vertical: true)

            if let tagError = vm.tagError {
                Text(tagError)
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapTerracottaText)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let tagSuccess = vm.tagSuccess {
                Label(tagSuccess, systemImage: "checkmark.circle.fill")
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapSageText)
                    .fixedSize(horizontal: false, vertical: true)
            }

            PrimaryButton(title: vm.isRescanning ? "Re-reading…" : "Add the tag") {
                guard !vm.isRescanning else { return }
                if isPro {
                    vm.tagError = nil
                    onAddTag()
                } else {
                    onPaywall(.addTag)
                }
            }
            .disabled(vm.isRescanning)
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .shadow(color: Color.snapCardShadow.opacity(0.08), radius: 24, x: 0, y: 8)
    }
}

/// The panel the backend has been paying for since July. Pro sees it all;
/// free sees the header, a blurred first line, and the way in.
struct WhyThisPriceCard: View {
    let result: ScanResult
    /// `result.valuationDetail`, unwrapped by the sheet.
    let detail: ValuationDetail
    let isPro: Bool
    /// The sheet's `fullDetailOffer`.
    let offer: FullDetailOffer
    let vm: ResultViewModel
    /// The sheet's `rereadForFullDetail`.
    let onReread: () -> Void
    let onPaywall: (PaywallTrigger) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("Why this price")
                    .snapSectionHeader()
                Spacer()
                if !isPro { ProBadge() }
            }
            if isPro {
                ValuationDetailView(detail: detail,
                                    priceFactor: result.conditionPriceFactor,
                                    gradeWasOverridden: result.conditionWasOverridden)
                switch offer {
                case .reread:           fullDetailPrompt
                case .scannedBeforePro: scannedBeforeProNote
                // `.teaserNewScansOnly` is a free user's; it never gets here.
                case .none, .teaserNewScansOnly:
                    EmptyView()
                }
            } else {
                lockedDetailTeaser(detail,
                                   newScansOnly: offer == .teaserNewScansOnly)
            }
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .shadow(color: Color.snapCardShadow.opacity(0.08), radius: 24, x: 0, y: 8)
    }

    /// For a subscriber looking at a fresh result that was saved with only
    /// the free part of the panel. Worded without claiming *why* it is thin,
    /// because the blob cannot say; in practice it is a scan made before Pro.
    ///
    /// It names everything a re-read replaces (`applySharpened`). It said only
    /// that "the estimate may change", and a tap also renames the item and
    /// rewrites its details and listing draft.
    private var fullDetailPrompt: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(FullDetailOffer.rereadPrompt)
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)
                .fixedSize(horizontal: false, vertical: true)
            if let fullDetailError = vm.fullDetailError {
                Text(fullDetailError)
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapTerracottaText)
                    .fixedSize(horizontal: false, vertical: true)
            }
            PrimaryButton(title: vm.isRescanning ? "Re-reading…" : "Show the full breakdown") {
                onReread()
            }
            .disabled(vm.isRescanning)
        }
    }

    /// For a subscriber reopening a thin find from My Finds or My Flips,
    /// which is not re-read (`FullDetailOffer`). Without it the panel is a
    /// score, a sentence and a grade, and nothing says that is not all Pro
    /// has to show.
    ///
    /// "Scanned before Pro" names the usual cause. The blob cannot prove it:
    /// a scan the server answered as free while the device was already Pro is
    /// thin too. That is a scan in the moments after a purchase, before the
    /// server has been told, and "before Pro" is how the server saw it.
    private var scannedBeforeProNote: some View {
        VStack(alignment: .leading, spacing: 4) {
            Label("Scanned before Pro", systemImage: "clock")
                .font(.dmSans(13, weight: .semibold))
                .foregroundStyle(Color.snapEspresso)
            Text("The full breakdown is only available for new scans. This find keeps the summary it was saved with.")
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }

    /// What a free user sees in place of the panel: a blurred first line, and
    /// the way in.
    ///
    /// `newScansOnly` is a thin find reopened from My Finds or My Flips
    /// (`FullDetailOffer.teaserNewScansOnly`). Buying does not bring that
    /// find's breakdown: it is not re-read, and the panel then says "Scanned
    /// before Pro". This teaser used to offer to "Unlock why this price" there
    /// too, above four price points and what drives the value, and a purchase
    /// from it delivered the label instead. So the button there sells Pro
    /// rather than this find's panel, and the caption says the breakdown comes
    /// with new scans and this find keeps its summary, as the label will say
    /// once they have bought. The paywall it opens keeps to that too
    /// (`paywallPitch(for:)`).
    private func lockedDetailTeaser(_ detail: ValuationDetail, newScansOnly: Bool) -> some View {
        ZStack {
            VStack(alignment: .leading, spacing: 6) {
                Text(detail.confidenceSummary
                     ?? String(localized: "Confidence \(detail.confidenceScore ?? 0) out of 100"))
                    .font(.dmSans(15, weight: .semibold))
                    .foregroundStyle(Color.snapEspresso)
                    .lineLimit(1)
                Text(detail.valueDrivers.first
                     ?? detail.improveEstimate.first
                     ?? String(localized: "Quick-sale, expected and best-case prices, and what moves them…"))
                    .font(.snapBody)
                    .foregroundStyle(Color.snapWarmGray)
                    .lineLimit(2)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .blur(radius: 5)
            .accessibilityHidden(true)

            VStack(spacing: 10) {
                Image(systemName: "lock.fill")
                    .snapSymbol(18)
                    .foregroundStyle(Color.snapTerracottaText)
                PrimaryButton(title: newScansOnly ? "Upgrade to Pro" : "Unlock why this price") {
                    onPaywall(.valuationDetail)
                }
                Text(FullDetailOffer.teaserCaption(newScansOnly: newScansOnly))
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
