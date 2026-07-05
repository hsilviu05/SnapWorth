import SwiftUI

// MARK: - Privacy Policy

struct PrivacyPolicyView: View {
    var body: some View {
        LegalDocumentView(title: "Privacy Policy", updated: "July 5, 2026") {
            LegalSection(heading: nil, text: """
                SnapWorth ("we", "our", or "us") operates the SnapWorth mobile application. This page informs you of our policies regarding the collection, use, and disclosure of personal data when you use our Service.
                """)

            LegalSection(heading: "Information We Collect", text: """
                We collect photos you submit for valuation. Photos are sent to our server, processed by an AI model to identify the item and estimate resale value, and are not stored after the response is returned.

                We collect an anonymous device identifier (UUID) solely for rate-limiting purposes. This ID is not linked to your identity.
                """)

            LegalSection(heading: "How We Use Your Information", text: """
                Photos are used only to generate the valuation response you requested. We do not sell, rent, or share your photos or device identifier with third parties, except as required by law.
                """)

            LegalSection(heading: "Data Retention", text: """
                Photos and scan results are processed in real time and are not retained on our servers. Scan history is stored locally on your device and can be deleted at any time from Settings.
                """)

            LegalSection(heading: "Children's Privacy", text: """
                SnapWorth is not directed to children under 13. We do not knowingly collect personal information from children under 13.
                """)

            LegalSection(heading: "Changes to This Policy", text: """
                We may update this Privacy Policy from time to time. Changes are effective when posted in the app.
                """)

            LegalSection(heading: "Contact", text: "Questions? Email us at silh6767@gmail.com")
        }
    }
}

// MARK: - Terms of Service

struct TermsOfServiceView: View {
    var body: some View {
        LegalDocumentView(title: "Terms of Service", updated: "July 5, 2026") {
            LegalSection(heading: nil, text: """
                By downloading or using SnapWorth you agree to these Terms. If you disagree, please do not use the app.
                """)

            LegalSection(heading: "Use of Service", text: """
                SnapWorth provides AI-generated resale value estimates for informational purposes only. Estimates are not guarantees of actual sale prices. We are not responsible for any financial decisions made based on our estimates.
                """)

            LegalSection(heading: "Subscriptions", text: """
                SnapWorth offers auto-renewing subscriptions (weekly and yearly). Subscriptions are charged to your Apple ID account. You can cancel at any time in your device's subscription settings. Cancellation takes effect at the end of the current billing period. A 3-day free trial is available for new subscribers.
                """)

            LegalSection(heading: "Prohibited Use", text: """
                You may not use SnapWorth to submit illegal content, attempt to reverse-engineer the service, or abuse the rate limits.
                """)

            LegalSection(heading: "Disclaimer", text: """
                THE SERVICE IS PROVIDED "AS IS" WITHOUT WARRANTIES OF ANY KIND. TO THE MAXIMUM EXTENT PERMITTED BY LAW, WE DISCLAIM ALL WARRANTIES, EXPRESS OR IMPLIED.
                """)

            LegalSection(heading: "Contact", text: "Questions? Email us at silh6767@gmail.com")
        }
    }
}

// MARK: - Shared layout

private struct LegalDocumentView<Content: View>: View {
    let title: String
    let updated: String
    @ViewBuilder let content: Content

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("Last updated: \(updated)")
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)

                content
            }
            .padding(20)
            .padding(.bottom, 32)
        }
        .background(Color.snapBackground)
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.large)
    }
}

private struct LegalSection: View {
    let heading: String?
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let heading {
                Text(heading)
                    .font(.dmSans(15, weight: .semibold))
                    .foregroundStyle(Color.snapEspresso)
            }
            Text(text)
                .font(.snapBody)
                .foregroundStyle(Color.snapWarmGray)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
