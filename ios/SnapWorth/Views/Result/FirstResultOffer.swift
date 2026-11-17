import SwiftUI

// MARK: - When the first result offers Pro

/// Whether a result carries `FirstResultOfferCard`: a free user's first fresh
/// result, once.
///
/// This replaced a full-screen paywall that opened by itself as the first
/// result's sheet closed (trigger `onboarding`). It led with the trial and a
/// list of ten features, none of them about the find just valued, and in the
/// 28 days to 17 Nov 2026 it had 100 views and no trial or purchase, while
/// the paywalls opened from something the user tapped all sold. So the offer
/// moved into the result, beside the find, and opens the paywall only when
/// tapped.
///
/// A value rather than conditions in the view, so who sees it is tested
/// directly.
enum FirstResultOffer {
    /// Set once the card has been on screen. The key the full-screen paywall
    /// used, on purpose: someone who already met that one is not offered Pro
    /// a second time as a "first" result.
    static let seenKey = "hasSeenFirstResultPaywall"

    /// A fresh scan only (`ResultView.isFreshScan`): a find reopened from My
    /// Finds or My Flips is not a first result. A typed item counts: the card
    /// promises nothing a photo is needed for.
    static func isOffered(isPro: Bool, isFreshScan: Bool, alreadySeen: Bool) -> Bool {
        !isPro && isFreshScan && !alreadySeen
    }

    /// What Pro does with this find, in the card's order. The first row is
    /// what the paywall it opens leads with (`PaywallCopy.pitch(for:
    /// .firstResult)`).
    static var rows: [PaywallCopy.Benefit] {
        [
            PaywallCopy.Benefit(icon: "cart.fill",
                                text: String(localized: "Turn it into a ready-to-post listing")),
            PaywallCopy.Benefit(icon: "bubble.left.and.bubble.right.fill",
                                text: String(localized: "Ask Tag how to price and sell it")),
            PaywallCopy.Benefit(icon: "infinity",
                                text: String(localized: "Scan the rest of your finds, with no daily limit")),
        ]
    }
}

// MARK: - The card

/// Pro, offered on the find the user has just valued: its name and range,
/// what Pro would do with it, and the way in. Shown under "Why this price",
/// after the guess cover has lifted, because it prints the range.
struct FirstResultOfferCard: View {
    let result: ScanResult
    let onPaywall: (PaywallTrigger) -> Void
    /// "Not now": the sheet hides the card for the rest of this result.
    let onDismiss: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("Get the most for this find")
                    .snapSectionHeader()
                Spacer()
                ProBadge()
            }

            VStack(alignment: .leading, spacing: 2) {
                Text(verbatim: result.itemName)
                    .font(.dmSans(17, weight: .semibold))
                    .foregroundStyle(Color.snapEspresso)
                    .lineLimit(2)
                Text(verbatim: result.formattedRange)
                    .font(.dmSans(15, weight: .semibold))
                    .foregroundStyle(Color.snapTerracottaText)
            }
            .accessibilityElement(children: .combine)

            VStack(alignment: .leading, spacing: 10) {
                ForEach(FirstResultOffer.rows, id: \.text) { row in
                    HStack(spacing: 12) {
                        Image(systemName: row.icon)
                            .snapSymbol(16, weight: .medium)
                            .foregroundStyle(Color.snapSageText)
                            // A fixed width, so the wider speech-bubble
                            // icon does not push its row's text right.
                            .frame(width: 28)
                        Text(row.text)
                            .font(.snapBody)
                            .foregroundStyle(Color.snapEspresso)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 0)
                    }
                    // The icon is decorative: the text names the benefit.
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(row.text)
                }
            }

            PrimaryButton(title: "See Pro plans") {
                onPaywall(.firstResult)
            }

            Button("Not now", action: onDismiss)
                .font(.dmSans(14, weight: .semibold))
                .foregroundStyle(Color.snapWarmGray)
                .frame(maxWidth: .infinity)
                .snapHitTarget()
                .accessibilityHint("Hides this offer")
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .shadow(color: Color.snapCardShadow.opacity(0.08), radius: 24, x: 0, y: 8)
    }
}
