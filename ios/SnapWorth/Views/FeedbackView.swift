import SwiftUI
import UIKit

struct FeedbackView: View {
    var initialType: FeedbackType = .featureRequest

    @State private var feedbackType: FeedbackType = .featureRequest
    @State private var message: String = ""
    @State private var didSend: Bool = false
    @State private var didCopy: Bool = false
    /// Set when `mailto:` could not be opened at all — an iPhone with no mail
    /// account, or a managed device with Mail restricted. Without this the
    /// Send button was simply inert and the user had no way to know why.
    @State private var mailUnavailable: Bool = false
    @State private var sendResetTask: Task<Void, Never>?
    @State private var copyResetTask: Task<Void, Never>?

    private let maxChars = 500

    /// What VoiceOver is told about the message field.
    ///
    /// Mirrors exactly what the two visual-only lines below the editor say —
    /// the character counter and the "N more characters needed" message that
    /// explains why Send is disabled. Both are `Text` views a VoiceOver user
    /// would have to go looking for, on a support screen, which is the one
    /// place a user who cannot see the form most needs to reach someone.
    private var messageFieldHint: String {
        let trimmed = message.trimmingCharacters(in: .whitespacesAndNewlines).count
        guard trimmed >= 10 else {
            let needed = 10 - trimmed
            return "\(needed) more character\(needed == 1 ? "" : "s") needed "
                + "before you can send. Up to \(maxChars) characters."
        }
        let left = maxChars - message.count
        return "\(left) character\(left == 1 ? "" : "s") remaining."
    }

    enum FeedbackType: String, CaseIterable {
        case featureRequest  = "Feature Request"
        case bugReport       = "Bug Report"
        case general         = "General Feedback"

        var icon: String {
            switch self {
            case .featureRequest: return "lightbulb"
            case .bugReport:      return "ant"
            case .general:        return "bubble.left"
            }
        }

        var subject: String { "SnapWorth \(rawValue)" }
    }

    private var canSend: Bool {
        message.trimmingCharacters(in: .whitespacesAndNewlines).count >= 10
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {

                // ── Type chips ────────────────────────────────────────────
                VStack(alignment: .leading, spacing: 12) {
                    Text("I want to…")
                        .snapSectionHeader()
                        .padding(.horizontal, 20)

                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 10) {
                            ForEach(FeedbackType.allCases, id: \.self) { type in
                                Button {
                                    withAnimation(.spring(duration: 0.2)) {
                                        feedbackType = type
                                    }
                                } label: {
                                    HStack(spacing: 6) {
                                        Image(systemName: type.icon)
                                            .snapSymbol(13, weight: .medium)
                                        Text(type.rawValue)
                                            .font(.dmSans(13, weight: .medium))
                                    }
                                    .padding(.horizontal, 14)
                                    .padding(.vertical, 9)
                                    .background(feedbackType == type ? Color.snapTerracottaFill : Color.snapCard)
                                    .foregroundStyle(feedbackType == type ? Color.snapBackground : Color.snapEspresso)
                                    .clipShape(Capsule())
                                    .overlay(
                                        Capsule()
                                            .strokeBorder(
                                                feedbackType == type ? Color.clear : Color.snapBorder,
                                                lineWidth: 1
                                            )
                                    )
                                }
                                .buttonStyle(.plain)
                                .snapAnimation(.spring(duration: 0.2), value: feedbackType)
                                // 9pt of vertical padding left a ~31pt target,
                                // and selection was carried by colour alone.
                                .snapHitTarget()
                                .accessibilityLabel(type.rawValue)
                                .accessibilityAddTraits(
                                    feedbackType == type ? [.isButton, .isSelected] : .isButton)
                            }
                        }
                        .padding(.horizontal, 20)
                        .padding(.vertical, 2)
                    }
                }

                // ── Message ───────────────────────────────────────────────
                VStack(alignment: .leading, spacing: 8) {
                    Text("Your message")
                        .snapSectionHeader()

                    ZStack(alignment: .bottomTrailing) {
                        TextEditor(text: $message)
                            .font(.snapBody)
                            .foregroundStyle(Color.snapEspresso)
                            .frame(minHeight: 150)
                            .padding(12)
                            .scrollContentBackground(.hidden)
                            .background(Color.snapCard)
                            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                            .overlay(
                                RoundedRectangle(cornerRadius: 16, style: .continuous)
                                    .strokeBorder(Color.snapBorder, lineWidth: 1)
                            )
                            .onChange(of: message) { _, new in
                                if new.count > maxChars {
                                    message = String(new.prefix(maxChars))
                                }
                            }
                            // A `TextEditor` has no accessible name of its own,
                            // and the "Your message" header above it is a
                            // separate `Text` — so VoiceOver announced this as
                            // an unlabelled text field. The 500-character cap
                            // and the reason Send is disabled were both visual
                            // only: the counter below and the "N more
                            // characters needed" line, neither of which a
                            // VoiceOver user is given any reason to go and find.
                            .accessibilityLabel("Your message")
                            .accessibilityHint(messageFieldHint)

                        Text("\(message.count)/\(maxChars)")
                            .font(.dmSans(11))
                            .foregroundStyle(Color.snapWarmGray)
                            .padding(10)
                            // The hint above says the same thing in words. A
                            // second stop reading "12 slash 500" is noise.
                            .accessibilityHidden(true)
                    }

                    if !canSend {
                        Text("\(max(0, 10 - message.trimmingCharacters(in: .whitespacesAndNewlines).count)) more character\(10 - message.trimmingCharacters(in: .whitespacesAndNewlines).count == 1 ? "" : "s") needed.")
                            .font(.dmSans(11))
                            .foregroundStyle(Color.snapWarmGray)
                            .transition(.opacity)
                    }
                }
                .padding(.horizontal, 20)

                // ── Send ──────────────────────────────────────────────────
                VStack(spacing: 14) {
                    PrimaryButton(title: "Send Feedback") {
                        sendFeedback()
                    }
                    .disabled(!canSend)
                    .opacity(canSend ? 1 : 0.45)
                    .snapAnimation(.easeInOut(duration: 0.15), value: canSend)

                    if didSend {
                        HStack(spacing: 8) {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundStyle(Color.snapSageText)
                            // Not "thanks, sent": opening a draft is not
                            // sending it. Saying so is also why the message
                            // below is still here to send.
                            Text("Your draft is open in Mail — send it there.")
                                .font(.snapCaption)
                                .foregroundStyle(Color.snapWarmGray)
                        }
                        .transition(.opacity.combined(with: .scale(scale: 0.9, anchor: .top)))
                    }

                    if mailUnavailable {
                        mailFallback
                            .transition(.opacity.combined(with: .scale(scale: 0.95, anchor: .top)))
                    }
                }
                .padding(.horizontal, 20)
            }
            .padding(.top, 8)
            .padding(.bottom, 48)
        }
        .background(Color.snapBackground)
        .navigationTitle("Send Feedback")
        .navigationBarTitleDisplayMode(.large)
        .scrollDismissesKeyboard(.interactively)
        .snapAnimation(.spring(duration: 0.3), value: didSend)
        .snapAnimation(.spring(duration: 0.3), value: mailUnavailable)
        .onAppear { feedbackType = initialType }
        .onDisappear {
            sendResetTask?.cancel()
            copyResetTask?.cancel()
        }
    }

    // ── Fallback when there is no mail client ─────────────────────────────────

    /// Shown instead of a dead button. The message is the user's work; if we
    /// cannot hand it to Mail we at least hand it back to them.
    private var mailFallback: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .snapSymbol(13, weight: .medium)
                    .foregroundStyle(Color.snapTerracottaText)
                Text("Couldn't open Mail")
                    .font(.dmSans(14, weight: .medium))
                    .foregroundStyle(Color.snapEspresso)
            }

            Text("This iPhone has no email account set up. Copy your message and send it to \(Config.supportEmail) from wherever you do have mail.")
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)
                .fixedSize(horizontal: false, vertical: true)

            Button {
                UIPasteboard.general.string = composedBody()
                Haptics.success()
                didCopy = true
                copyResetTask?.cancel()
                copyResetTask = Task {
                    try? await Task.sleep(for: .seconds(2))
                    guard !Task.isCancelled else { return }
                    withAnimation { didCopy = false }
                }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: didCopy ? "checkmark" : "doc.on.doc")
                        .snapSymbol(12, weight: .medium)
                    Text(didCopy ? "Copied" : "Copy message and address")
                        .font(.dmSans(13, weight: .medium))
                }
                .foregroundStyle(Color.snapTerracottaText)
                .padding(.vertical, 4)
            }
            .buttonStyle(.plain)
            .snapHitTarget()
            .snapAnimation(.easeInOut(duration: 0.2), value: didCopy)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .strokeBorder(Color.snapBorder, lineWidth: 1)
        )
    }

    // ── Sending ───────────────────────────────────────────────────────────────

    /// The trimmed message plus the diagnostics block. Built once so the
    /// clipboard fallback carries exactly what the email would have.
    private func composedBody() -> String {
        let text = message.trimmingCharacters(in: .whitespacesAndNewlines)
        return "\(text)\n\n\(SupportMail.diagnostics)"
    }

    private func sendFeedback() {
        guard let url = SupportMail.composeURL(
            subject: feedbackType.subject, body: composedBody())
        else {
            withAnimation { mailUnavailable = true }
            return
        }

        UIApplication.shared.open(url) { success in
            Task { @MainActor in
                guard success else {
                    // The old code returned here without a word, so on a
                    // device with no mail account the button was inert and
                    // the user had no way to tell that from "sent".
                    withAnimation(.spring(duration: 0.3)) { self.mailUnavailable = true }
                    self.didSend = false
                    return
                }
                self.mailUnavailable = false
                withAnimation(.spring(duration: 0.3)) { self.didSend = true }
                // The message is deliberately NOT cleared. `open` succeeding
                // means Mail opened a draft, not that anything was sent —
                // clearing here destroyed the message of anyone who backed
                // out of the compose sheet, with no way to get it back.
                self.sendResetTask?.cancel()
                self.sendResetTask = Task {
                    try? await Task.sleep(for: .seconds(4))
                    guard !Task.isCancelled else { return }
                    withAnimation { self.didSend = false }
                }
            }
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - Support mail
// ═══════════════════════════════════════════════════════════════════

/// Composing the one message this app ever sends.
///
/// Every path here used to live inline in `sendFeedback()`, and each had a
/// way of losing the user's message:
///
/// * `URLComponents.queryItems` does not escape `+`, and a mail client
///   reading a query component decodes a bare `+` as a space. "iOS 26 +
///   widgets" arrived as "iOS 26   widgets"; a phone number lost its country
///   code. Encoding the values here with `+` and the query delimiters removed
///   from the allowed set is what gets the body through byte-for-byte.
/// * A failed open was swallowed, so on an iPhone with no mail account the
///   Send button did nothing at all, forever, with no explanation.
/// * A bug report carried no version, OS or hardware, so the first reply was
///   always a round trip asking for them.
enum SupportMail {

    /// `urlQueryAllowed` permits the delimiters that separate a query's own
    /// fields, plus `+`. Inside a *value* all four have to be escaped.
    private static let valueAllowed: CharacterSet = {
        var set = CharacterSet.urlQueryAllowed
        set.remove(charactersIn: "+&=?#")
        return set
    }()

    /// A `mailto:` URL for `Config.supportEmail`, or nil if the subject or
    /// body cannot be encoded — the caller must surface that, not drop it.
    static func composeURL(subject: String, body: String) -> URL? {
        guard !Config.supportEmail.isEmpty,
              let subject = subject.addingPercentEncoding(withAllowedCharacters: valueAllowed),
              let body = body.addingPercentEncoding(withAllowedCharacters: valueAllowed)
        else { return nil }
        return URL(string: "mailto:\(Config.supportEmail)?subject=\(subject)&body=\(body)")
    }

    private static let supportIDKey = "supportID"

    /// The device's pseudonym in the backend's own indexes, as sent by the
    /// last token mint. `nil` until the app has authenticated once, and on
    /// any build talking to a backend that predates the field.
    ///
    /// Not a credential. It is a salted, truncated hash of the attestation
    /// subject: it authenticates nothing, and the salt lives on the server,
    /// which is why the client has to be told rather than deriving it.
    static var supportID: String? {
        get { UserDefaults.standard.string(forKey: supportIDKey) }
        set {
            let defaults = UserDefaults.standard
            if let newValue, !newValue.isEmpty {
                defaults.set(newValue, forKey: supportIDKey)
            } else {
                defaults.removeObject(forKey: supportIDKey)
            }
        }
    }

    /// What triaging a bug report needs and what a user should never be asked
    /// to go and look up.
    ///
    /// Version, OS, hardware, and the support id if there is one — which is
    /// what makes the operator's `/user <id>` command usable from an email
    /// for the first time. Nothing else: no vendor id, no address, no scan
    /// history. The id is pseudonymous by construction and resolves only
    /// against an index the operator already holds.
    static var diagnostics: String {
        let info = Bundle.main.infoDictionary
        let version = info?["CFBundleShortVersionString"] as? String ?? "?"
        let build = info?["CFBundleVersion"] as? String ?? "?"
        let device = UIDevice.current
        var lines = [
            "—",
            "SnapWorth \(version) (\(build))",
            "\(device.systemName) \(device.systemVersion) · \(hardwareModel)",
        ]
        if let id = supportID, !id.isEmpty {
            lines.append("Device \(id)")
        }
        return lines.joined(separator: "\n")
    }

    /// `UIDevice.model` is the string "iPhone" on every iPhone ever made. The
    /// machine identifier ("iPhone17,2") is what tells a 12 mini from a 17
    /// Pro Max, which is the difference between reproducing a layout bug and
    /// not.
    private static var hardwareModel: String {
        var info = utsname()
        guard uname(&info) == 0 else { return "unknown" }
        return withUnsafeBytes(of: info.machine) { raw in
            String(decoding: raw.prefix(while: { $0 != 0 }), as: UTF8.self)
        }
    }
}
