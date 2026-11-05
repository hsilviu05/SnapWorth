import SwiftUI

// ═══════════════════════════════════════════════════════════════════
// MARK: - AI consent (1.8.0)
// ═══════════════════════════════════════════════════════════════════
//
// App Store Review Guideline 5.1.2(i): an app that shares personal data with a
// third-party AI service has to say so and get permission first. Every scan,
// typed description, legit check, rack scan, listing draft and Ask Tag
// question goes to Google's Gemini API through our server, and until 1.8.0
// only the privacy policy said so.
//
// One screen, once, between onboarding and the app (`RootView`), so nothing
// reaches the AI before it has been agreed to, whichever way in: the camera,
// Photos, the share extension, visual intelligence or a widget.

enum AIConsent {
    /// Versioned, so a material change to what is sent can ask again.
    static let key = "snapworth_ai_consent_v1"

    static var isGiven: Bool { UserDefaults.standard.bool(forKey: key) }
}

struct AIConsentView: View {
    let onAgree: () -> Void

    @State private var showPolicy = false

    var body: some View {
        ZStack {
            Color.snapBackground.ignoresSafeArea()

            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    TagMascot(mood: .happy, height: 96)
                        .frame(maxWidth: .infinity)
                        .padding(.top, 32)

                    Text("Before your first scan")
                        .font(.snapTitle)
                        .foregroundStyle(Color.snapEspresso)
                        .accessibilityAddTraits(.isHeader)

                    Text("SnapWorth uses Google's Gemini AI to identify items and estimate what they're worth. To do that, it sends Google:")
                        .font(.snapBody)
                        .foregroundStyle(Color.snapEspresso)
                        .fixedSize(horizontal: false, vertical: true)

                    VStack(alignment: .leading, spacing: 12) {
                        row("camera", "The photos you scan, and any close-ups for a legit check")
                        row("text.cursor", "Descriptions you type instead of a photo")
                        row("bubble.left.and.bubble.right", "Questions you ask Tag (Pro), with the details of that find but never its photo")
                    }

                    Text("Google doesn't use them to train its models, and we don't keep them after you get your answer.")
                        .font(.snapBody)
                        .foregroundStyle(Color.snapWarmGray)
                        .fixedSize(horizontal: false, vertical: true)

                    Button {
                        showPolicy = true
                    } label: {
                        Text("Read the privacy policy")
                            .font(.dmSans(15, weight: .semibold))
                            .foregroundStyle(Color.snapTerracottaText)
                            .underline()
                    }
                    .snapHitTarget()
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 24)
            }
            .safeAreaInset(edge: .bottom) {
                VStack(spacing: 8) {
                    PrimaryButton(title: "Agree and continue") {
                        Haptics.selection()
                        onAgree()
                    }
                    Text("Scanning needs this. You can read what's sent at any time in Settings → Privacy Policy.")
                        .font(.snapCaption)
                        .foregroundStyle(Color.snapWarmGray)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.horizontal, 24)
                .padding(.vertical, 12)
                .background(Color.snapBackground)
            }
        }
        .sheet(isPresented: $showPolicy) {
            NavigationStack {
                PrivacyPolicyView()
                    .toolbar {
                        ToolbarItem(placement: .confirmationAction) {
                            Button("Done") { showPolicy = false }
                        }
                    }
            }
        }
    }

    private func row(_ icon: String, _ text: LocalizedStringKey) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Image(systemName: icon)
                .snapSymbol(16, weight: .medium)
                .foregroundStyle(Color.snapTerracottaText)
                .frame(minWidth: 24)
                .accessibilityHidden(true)
            Text(text)
                .font(.snapBody)
                .foregroundStyle(Color.snapEspresso)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
