import SwiftUI
import UIKit

// MARK: - Invite a friend (referrer)

/// The referrer's side of #97: their invite code, a share button, and any
/// weeks they have earned. Shown from Settings only when the server reports
/// referrals enabled.
struct InviteFriendView: View {
    let status: ReferralStatus

    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @State private var didCopy = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    Image(systemName: "gift")
                        .snapSymbol(40, weight: .light)
                        .foregroundStyle(Color.snapTerracottaText)
                        .padding(.top, 24)
                        .accessibilityHidden(true)

                    Text("Give a friend a week of Pro, get a week of Pro")
                        .font(.fraunces(22, weight: .bold))
                        .foregroundStyle(Color.snapEspresso)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)

                    Text("When a friend enters your code and starts their free week, you get a week of Pro too.")
                        .font(.snapBody)
                        .foregroundStyle(Color.snapWarmGray)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)

                    if let code = status.code {
                        codeCard(code)
                    }

                    if let message = status.shareMessage {
                        ShareLink(item: message) {
                            Label("Share invite", systemImage: "square.and.arrow.up")
                                .font(.dmSans(16, weight: .semibold))
                                .foregroundStyle(Color.snapOnAccent)
                                .frame(maxWidth: .infinity, minHeight: 52)
                                .background(Color.snapTerracottaFill)
                                .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                        }
                        .simultaneousGesture(TapGesture().onEnded {
                            Analytics.shared.track(.referralShared)
                        })
                    }

                    if !status.rewards.isEmpty {
                        rewardsSection
                    }

                    if let left = status.rewardsLeftThisYear {
                        Text("Weeks left to earn this year: \(left)")
                            .font(.snapCaption)
                            .foregroundStyle(Color.snapWarmGray)
                    }
                }
                .padding(20)
            }
            .background(Color.snapBackground)
            .navigationTitle("Invite a friend")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("Close") { dismiss() }
                        .foregroundStyle(Color.snapTerracottaText)
                }
            }
        }
    }

    private func codeCard(_ code: String) -> some View {
        VStack(spacing: 8) {
            Text("Your invite code")
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)
            Text(code)
                .font(.dmSans(30, weight: .bold))
                .tracking(4)
                .foregroundStyle(Color.snapEspresso)
                .textSelection(.enabled)
                // Read letter by letter, the way it has to be typed.
                .accessibilityLabel(code.map(String.init).joined(separator: " "))
            Button(didCopy ? "Copied!" : "Copy") {
                UIPasteboard.general.string = code
                didCopy = true
            }
            .font(.dmSans(14, weight: .semibold))
            .foregroundStyle(Color.snapTerracottaText)
            .snapHitTarget()
        }
        .frame(maxWidth: .infinity)
        .padding(18)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
    }

    private var rewardsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Weeks you've earned")
                .snapSectionHeader()
            ForEach(status.rewards) { reward in
                HStack {
                    Image(systemName: "checkmark.seal.fill")
                        .foregroundStyle(Color.snapSageText)
                        .accessibilityHidden(true)
                    Text(reward.code)
                        .font(.dmSans(15, weight: .semibold))
                        .foregroundStyle(Color.snapEspresso)
                    Spacer()
                    Button("Redeem") {
                        Analytics.shared.track(.referralRewarded)
                        openURL(reward.redeemURL)
                    }
                    .font(.dmSans(14, weight: .semibold))
                    .foregroundStyle(Color.snapTerracottaText)
                    .snapHitTarget()
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
    }
}

// MARK: - Have an invite code? (friend)

/// The friend's side of #97. Entering a code records who referred them and
/// hands back an Apple one-time offer code, which Apple's own redemption page
/// then shows and confirms. The app never grants the week itself.
struct RedeemInviteView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @State private var code = ""
    @State private var isChecking = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            VStack(spacing: 18) {
                Text("Enter the invite code a friend sent you.")
                    .font(.snapBody)
                    .foregroundStyle(Color.snapWarmGray)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 24)

                TextField("Invite code", text: $code)
                    .font(.dmSans(24, weight: .bold))
                    .multilineTextAlignment(.center)
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
                    .padding(.vertical, 14)
                    .background(Color.snapCard)
                    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                    .accessibilityLabel("Invite code")

                if let error {
                    Text(error)
                        .font(.snapCaption)
                        .foregroundStyle(Color.snapTerracottaText)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                }

                PrimaryButton(title: isChecking ? "Checking…" : "Get my free week", isLoading: isChecking) {
                    Task { await redeem() }
                }
                .disabled(isChecking || code.trimmingCharacters(in: .whitespaces).isEmpty)

                // Honest about what Apple's sheet will say: an offer code on a
                // subscription starts the subscription.
                Text("Apple shows the offer before you confirm. After the free week, the subscription renews unless you cancel.")
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)

                Spacer()
            }
            .padding(20)
            .background(Color.snapBackground)
            .navigationTitle("Have an invite code?")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("Close") { dismiss() }
                        .foregroundStyle(Color.snapTerracottaText)
                }
            }
        }
    }

    private func redeem() async {
        isChecking = true
        defer { isChecking = false }
        error = nil
        do {
            let claim = try await ReferralAPIClient.shared.claim(code: code)
            Analytics.shared.track(.referralRedeemed)
            openURL(claim.redeemURL)
            dismiss()
        } catch let failure as ReferralClaimError {
            error = failure.errorDescription
        } catch {
            self.error = ReferralClaimError.other.errorDescription
        }
    }
}
