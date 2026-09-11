import SwiftUI

// MARK: - Privacy Policy

struct PrivacyPolicyView: View {
    var body: some View {
        LegalDocumentView(title: "Privacy Policy", updated: PrivacyPolicy.updated) {
            ForEach(Array(PrivacyPolicy.sections.enumerated()), id: \.offset) { _, section in
                LegalSection(heading: section.heading, text: section.text)
            }
        }
    }
}

/// The shipped privacy policy, as data rather than as view code.
///
/// Hoisted out of the view body so it can be asserted on. It was inline when
/// the web copy at `/privacy` gained a Service Providers section on
/// 2026-09-03 and this one did not: for a week the app — including the link
/// on the paywall — told users their data was not shared with anyone, while
/// photos went to Google on every scan. Nothing could have caught that,
/// because nothing could read this text.
///
/// Change this and `backend/main.py`'s `/privacy` together.
/// `PrivacyPolicyDisclosureTests` fails if a processor goes missing.
enum PrivacyPolicy {
    static let updated = "September 9, 2026"

    /// Every third party that receives user data, by the name a reader would
    /// recognise. The test asserts each appears in `sections`.
    static let processors = ["Google", "Gemini", "Apple", "DeviceCheck",
                             "TelemetryDeck", "Telegram"]

    static let sections: [(heading: String?, text: String)] = [
        (heading: nil, text: """
            SnapWorth ("we", "our", or "us") operates the SnapWorth mobile application. This page informs you of our policies regarding the collection, use, and disclosure of personal data when you use our Service.
            """),
        (heading: "Information We Collect", text: """
            We collect photos you submit for valuation. Photos are sent to our server, processed by an AI model to identify the item and estimate resale value, and are not stored after the response is returned.

            We collect an anonymous device identifier (UUID) for rate limiting and to limit how many devices can use one subscription. This ID is not linked to your identity.

            To tell a reinstall from a genuinely new device — so a free allowance cannot be reset by deleting and reinstalling — we send Apple's DeviceCheck token when your device first verifies itself. Apple stores two bits against the hardware on our behalf; we store the token to read them. It contains no personal information and cannot identify you.

            We collect anonymous usage analytics to understand how the app is used and improve it. Using TelemetryDeck, we record in-app events — such as opening the app, starting a scan, viewing the paywall, and completing a purchase — along with your device model, operating system version, app version, and locale. A one-way salted hash is used as an anonymous identifier. This data contains no photos, item names, prices, or advertising identifiers (IDFA), is not linked to your identity, and is never used to track you across other apps or websites. You can turn analytics off at any time in Settings.
            """),
        (heading: "How We Use Your Information", text: """
            Photos are used only to generate the valuation response you requested. Analytics data is used only in aggregate to understand usage and improve the app. We do not sell, rent, or share your photos, device identifier, or analytics data with third parties, except for the service providers below and as required by law.
            """),
        (heading: "Service Providers", text: """
            Google (Gemini API). Photos you submit are transmitted to Google's Gemini API, which identifies the item and estimates its resale value. Google processes them under its API terms of service and does not use them to train its models. Photos are not retained by us after the response is returned.

            Apple (DeviceCheck). Receives the device token described above, and stores two bits against your hardware so a reinstall does not reset the free allowance.

            TelemetryDeck. Receives the anonymous usage events described above. It never receives photos, item names, prices, or identifiers.

            Telegram. To monitor the service, aggregate operational information may be relayed to the operator through Telegram: the name of an item the AI identified and its estimated price range. Never the photo, never a device identifier, never anything that links a scan to a device or a person.
            """),
        (heading: "Data Retention", text: """
            Photos and scan results are processed in real time and are not retained on our servers. Scan history is stored locally on your device and can be deleted at any time from Settings.
            """),
        (heading: "Children's Privacy", text: """
            SnapWorth is not directed to children under 13. We do not knowingly collect personal information from children under 13.
            """),
        (heading: "Changes to This Policy", text: """
            We may update this Privacy Policy from time to time. Changes are effective when posted in the app.
            """),
        (heading: "Contact", text: "Questions? Email us at her.silviu.i@gmail.com")
    ]
}

// MARK: - Terms of Service

/// The one paragraph of the Terms that can be made false by someone else.
///
/// Lifted out of the view so a test can read it. Everything else in this
/// document is a statement about how we behave; this is a statement about what
/// Apple grants, and only App Store Connect controls that.
enum TermsCopy {
    /// Deliberately the *rule*, not today's offer.
    ///
    /// This used to promise "a 3-day free trial". Change the introductory
    /// offer in App Store Connect — to a paid one, or to none — and these
    /// Terms become a promise the app does not keep, with no release in
    /// between to catch it. The paywall reads the live offer from StoreKit
    /// (`IntroOffer`), so that is where the specifics belong, and where the
    /// user sees them before Apple charges anything.
    static let subscriptions = """
        SnapWorth offers auto-renewing subscriptions (monthly and yearly). Subscriptions are charged to your Apple ID account. You can cancel at any time in your device's subscription settings. Cancellation takes effect at the end of the current billing period. Any introductory offer is available to new subscribers only; its length, its price, and whether it is free are shown on the subscription screen and confirmed by the App Store before you are charged.
        """
}

struct TermsOfServiceView: View {
    var body: some View {
        LegalDocumentView(title: "Terms of Service", updated: "September 11, 2026") {
            LegalSection(heading: nil, text: """
                By downloading or using SnapWorth you agree to these Terms. If you disagree, please do not use the app.
                """)

            LegalSection(heading: "Use of Service", text: """
                SnapWorth provides AI-generated resale value estimates for informational purposes only. Estimates are not guarantees of actual sale prices. We are not responsible for any financial decisions made based on our estimates.
                """)

            LegalSection(heading: "Subscriptions", text: TermsCopy.subscriptions)

            LegalSection(heading: "Prohibited Use", text: """
                You may not use SnapWorth to submit illegal content, attempt to reverse-engineer the service, or abuse the rate limits.
                """)

            LegalSection(heading: "Disclaimer", text: """
                THE SERVICE IS PROVIDED "AS IS" WITHOUT WARRANTIES OF ANY KIND. TO THE MAXIMUM EXTENT PERMITTED BY LAW, WE DISCLAIM ALL WARRANTIES, EXPRESS OR IMPLIED.
                """)

            LegalSection(heading: "Contact", text: "Questions? Email us at her.silviu.i@gmail.com")
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
