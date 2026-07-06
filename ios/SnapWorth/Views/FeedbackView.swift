import SwiftUI

struct FeedbackView: View {
    var initialType: FeedbackType = .featureRequest

    @Environment(\.dismiss) private var dismiss
    @State private var feedbackType: FeedbackType = .featureRequest
    @State private var message: String = ""
    @State private var didSend: Bool = false

    private let maxChars = 500

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
                                            .font(.system(size: 13, weight: .medium))
                                        Text(type.rawValue)
                                            .font(.dmSans(13, weight: .medium))
                                    }
                                    .padding(.horizontal, 14)
                                    .padding(.vertical, 9)
                                    .background(feedbackType == type ? Color.snapTerracotta : Color.snapCard)
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
                                .animation(.spring(duration: 0.2), value: feedbackType)
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

                        Text("\(message.count)/\(maxChars)")
                            .font(.dmSans(11))
                            .foregroundStyle(Color.snapWarmGray.opacity(0.55))
                            .padding(10)
                    }

                    Text("Minimum 10 characters.")
                        .font(.dmSans(11))
                        .foregroundStyle(Color.snapWarmGray.opacity(0.6))
                }
                .padding(.horizontal, 20)

                // ── Send ──────────────────────────────────────────────────
                VStack(spacing: 14) {
                    PrimaryButton(title: "Send Feedback") {
                        sendFeedback()
                    }
                    .disabled(!canSend)
                    .opacity(canSend ? 1 : 0.45)
                    .animation(.easeInOut(duration: 0.15), value: canSend)

                    if didSend {
                        HStack(spacing: 8) {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundStyle(Color.snapSage)
                            Text("Thanks! We read every message.")
                                .font(.snapCaption)
                                .foregroundStyle(Color.snapWarmGray)
                        }
                        .transition(.opacity.combined(with: .scale(scale: 0.9, anchor: .top)))
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
        .animation(.spring(duration: 0.3), value: didSend)
        .onAppear { feedbackType = initialType }
    }

    private func sendFeedback() {
        let subject = feedbackType.subject
            .addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? ""
        let body = message
            .addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? ""
        let raw = "mailto:silh6767@gmail.com?subject=\(subject)&body=\(body)"

        guard let url = URL(string: raw) else { return }
        UIApplication.shared.open(url)

        withAnimation(.spring(duration: 0.3)) { didSend = true }
        message = ""
        Task {
            try? await Task.sleep(for: .seconds(3))
            withAnimation { didSend = false }
        }
    }
}
