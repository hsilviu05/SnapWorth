import SwiftData
import SwiftUI

// ═══════════════════════════════════════════════════════════════════
// MARK: - Ask Tag (1.8.0)
// ═══════════════════════════════════════════════════════════════════

/// A Pro user's conversation with Tag about one find, or a free user's one
/// preview question. Opened from the Tag's tips card. The conversation is
/// kept on the find (`ScanResult.askTagData`).
struct AskTagView: View {
    let result: ScanResult
    /// A free user's weekly question (`AskTagPreview`): one question, no
    /// follow-ups, then the way into Pro.
    var isPreview: Bool = false
    let onClose: () -> Void
    /// The preview's "Keep asking with Pro". Unused for Pro.
    var onUpgrade: () -> Void = {}

    @Environment(\.modelContext) private var modelContext
    @Environment(\.openURL) private var openURL
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var turns: [AskTagTurn] = []
    @State private var draft = ""
    @State private var isAsking = false
    @State private var problem: String?
    @FocusState private var fieldFocused: Bool

    static let suggestions: [String] = [
        String(localized: "What should I ask for it?"),
        String(localized: "How should I photograph it?"),
        String(localized: "Where would it sell best?"),
    ]

    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 12) {
                        header
                        ForEach(turns) { turn in
                            bubble(turn).id(turn.id)
                        }
                        if isAsking {
                            thinking.id("thinking")
                        }
                        if let problem {
                            Text(problem)
                                .font(.snapCaption)
                                .foregroundStyle(Color.snapTerracottaText)
                                .fixedSize(horizontal: false, vertical: true)
                                .id("problem")
                        }
                        if turns.isEmpty && !isAsking {
                            suggestionChips
                        }
                        if previewUsed {
                            upgradePrompt
                        }
                    }
                    .padding(20)
                }
                .scrollDismissesKeyboard(.interactively)
                .onChange(of: turns.count) { _, _ in scrollToEnd(proxy) }
                .onChange(of: isAsking) { _, _ in scrollToEnd(proxy) }
            }
            .background(Color.snapBackground)
            .safeAreaInset(edge: .bottom) {
                if !previewUsed { composer }
            }
            .navigationTitle("Ask Tag")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done", action: onClose)
                }
            }
        }
        .onAppear { turns = result.askTagTurns }
    }

    // ── Pieces ────────────────────────────────────────────────────────────

    private var header: some View {
        HStack(alignment: .center, spacing: 12) {
            TagMascot(mood: isAsking ? .wow : .happy, height: 56)
            VStack(alignment: .leading, spacing: 4) {
                Text(verbatim: result.itemName)
                    .font(.dmSans(16, weight: .semibold))
                    .foregroundStyle(Color.snapEspresso)
                    .lineLimit(2)
                Text("Ask me anything about reselling this find.")
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.bottom, 4)
    }

    private func bubble(_ turn: AskTagTurn) -> some View {
        let mine = turn.role == .user
        return VStack(alignment: mine ? .trailing : .leading, spacing: 4) {
            Text(verbatim: turn.text)
                .font(.snapBody)
                .foregroundStyle(mine ? Color.snapOnAccent : Color.snapEspresso)
                .fixedSize(horizontal: false, vertical: true)
                .padding(12)
                .background(mine ? Color.snapTerracottaFill : Color.snapCard,
                            in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                .contextMenu {
                    if !mine {
                        Button {
                            report(turn)
                        } label: {
                            Label("Report this answer", systemImage: "flag")
                        }
                    }
                }
            if !mine {
                Text("AI answer, may be wrong")
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)
            }
        }
        .frame(maxWidth: .infinity, alignment: mine ? .trailing : .leading)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(mine
            ? Text("You: \(turn.text)")
            : Text("Tag: \(turn.text). AI answer, may be wrong."))
        .accessibilityAction(named: Text("Report this answer")) {
            if !mine { report(turn) }
        }
    }

    private var thinking: some View {
        HStack(spacing: 8) {
            ProgressView()
            Text("Tag is thinking…")
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)
        }
        .accessibilityElement(children: .combine)
    }

    private var suggestionChips: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Self.suggestions, id: \.self) { suggestion in
                Button {
                    ask(suggestion)
                } label: {
                    Text(verbatim: suggestion)
                        .font(.dmSans(14, weight: .medium))
                        .foregroundStyle(Color.snapTerracottaText)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 8)
                        .overlay(Capsule().strokeBorder(Color.snapTerracotta, lineWidth: 1))
                }
                .buttonStyle(.plain)
                .snapHitTarget()
            }
        }
    }

    private var composer: some View {
        HStack(spacing: 8) {
            TextField("Ask Tag a question", text: $draft, axis: .vertical)
                .lineLimit(1...4)
                .font(.snapBody)
                .focused($fieldFocused)
                .submitLabel(.send)
                .onSubmit { ask(draft) }
                .onChange(of: draft) { _, text in
                    if text.count > AskTagThread.maxQuestionLength {
                        draft = String(text.prefix(AskTagThread.maxQuestionLength))
                    }
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(Color.snapCard, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
            Button {
                ask(draft)
            } label: {
                Image(systemName: "arrow.up.circle.fill")
                    .snapSymbol(30, weight: .semibold)
                    .foregroundStyle(canSend ? Color.snapTerracotta : Color.snapBorder)
            }
            .disabled(!canSend)
            .snapHitTarget()
            .accessibilityLabel("Send")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(Color.snapBackground)
    }

    /// The preview's question has been asked: on this find (an answer is
    /// here), or this week on this phone. Either way, no more without Pro.
    private var previewUsed: Bool {
        isPreview && !isAsking
            && (turns.contains { $0.role == .tag } || previewRefused)
    }
    @State private var previewRefused = false

    private var upgradePrompt: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Keep the conversation going with Pro: follow-up questions and Tag's advice on every find.")
                .font(.snapBody)
                .foregroundStyle(Color.snapEspresso)
                .fixedSize(horizontal: false, vertical: true)
            PrimaryButton(title: "Keep asking with Pro", action: onUpgrade)
        }
        .padding(16)
        .background(Color.snapCard, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        .padding(.top, 8)
    }

    private var canSend: Bool {
        !isAsking && !AskTagThread.cleanedQuestion(draft).isEmpty
    }

    // ── Actions ───────────────────────────────────────────────────────────

    private func ask(_ text: String) {
        let question = AskTagThread.cleanedQuestion(text)
        guard !question.isEmpty, !isAsking else { return }
        let history = turns
        let input = AskTagInput(result: result)
        draft = ""
        problem = nil
        isAsking = true
        append(AskTagTurn(role: .user, text: question))
        Task { @MainActor in
            defer { isAsking = false }
            do {
                let answer = try await AskTagAPIClient.shared.ask(
                    input, question: question, history: history)
                guard let answer else {
                    problem = String(localized: "I couldn't give a good answer to that one. Try asking another way.")
                    return
                }
                Haptics.light()
                append(AskTagTurn(role: .tag, text: answer))
                if isPreview { AskTagPreview.markUsed() }
            } catch {
                if isPreview, (error as? ScanAPIError)?.statusCode == 402 {
                    // This week's free question went on another find.
                    AskTagPreview.markUsed()
                    previewRefused = true
                    problem = String(localized: "You've used this week's free question.")
                } else {
                    problem = Self.message(for: error)
                }
            }
        }
    }

    private func append(_ turn: AskTagTurn) {
        if reduceMotion {
            turns.append(turn)
        } else {
            withAnimation(.snappy) { turns.append(turn) }
        }
        guard !AskTagView.findIsGone(result) else { return }
        result.askTagTurns = turns
        try? modelContext.save()
    }

    /// A find deleted while the sheet was open must not be written to: see
    /// `ResultView.findWasDeleted`.
    static func findIsGone(_ result: ScanResult) -> Bool {
        result.isDeleted || result.modelContext == nil
    }

    static func message(for error: Error) -> String {
        switch (error as? ScanAPIError)?.statusCode {
        case 429:
            // The day's 20, or the per-address hourly cap: either way, later.
            return String(localized: "You've reached Ask Tag's limit for now. Try again later.")
        case 402:
            return String(localized: "Ask Tag is part of SnapWorth Pro.")
        default:
            return String(localized: "Tag couldn't answer right now. Check your connection and try again.")
        }
    }

    private func report(_ turn: AskTagTurn) {
        let question = turns.last { $0.role == .user && $0.date <= turn.date }?.text ?? ""
        let body = "Ask Tag answer report\n\nItem: \(result.itemName)\nQuestion: \(question)\nAnswer: \(turn.text)\n\nWhat was wrong:\n"
        if let url = SupportMail.composeURL(subject: "SnapWorth Ask Tag report", body: body) {
            openURL(url)
        }
    }

    private func scrollToEnd(_ proxy: ScrollViewProxy) {
        let target: AnyHashable? = isAsking ? AnyHashable("thinking") : turns.last.map { AnyHashable($0.id) }
        guard let target else { return }
        if reduceMotion {
            proxy.scrollTo(target, anchor: .bottom)
        } else {
            withAnimation { proxy.scrollTo(target, anchor: .bottom) }
        }
    }
}

/// A free user's one Ask Tag question a week. The server holds the real
/// count (`ASSIST_FREE_WEEKLY_MAX`); this only hides the offer once it has
/// been used, so the card does not promise a question the server will refuse.
enum AskTagPreview {
    static let lastUsedKey = "snapworth_ask_tag_preview_used"
    static let window: TimeInterval = 7 * 24 * 3600

    static func isAvailable(now: Date = Date(), defaults: UserDefaults = .standard) -> Bool {
        guard let last = defaults.object(forKey: lastUsedKey) as? Date else { return true }
        return now.timeIntervalSince(last) >= window || last > now
    }

    static func markUsed(now: Date = Date(), defaults: UserDefaults = .standard) {
        defaults.set(now, forKey: lastUsedKey)
    }
}
