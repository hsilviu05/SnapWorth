import Foundation

// ═══════════════════════════════════════════════════════════════════
// MARK: - Ask Tag (1.8.0)
// ═══════════════════════════════════════════════════════════════════
//
// A Pro user's questions about one find, answered by the backend's `/assist`.
// The conversation is kept on the find (`ScanResult.askTagData`), on the
// device only, and goes when the find does.

/// One message in an Ask Tag conversation.
struct AskTagTurn: Codable, Equatable, Identifiable, Sendable {
    enum Role: String, Codable, Sendable {
        case user
        case tag
    }

    let id: UUID
    let role: Role
    let text: String
    let date: Date

    init(id: UUID = UUID(), role: Role, text: String, date: Date = Date()) {
        self.id = id
        self.role = role
        self.text = text
        self.date = date
    }
}

enum AskTagThread {
    /// The server's limits on what one request may carry
    /// (`backend/scan/assist.py`). Longer is refused with a 422.
    static let maxQuestionLength = 300
    static let maxHistory = 6
    static let maxTurnLength = 700
    /// Kept on the find. Older turns are dropped first.
    static let maxStored = 40

    static func decode(_ data: Data?) -> [AskTagTurn] {
        guard let data, let turns = try? JSONDecoder().decode([AskTagTurn].self, from: data)
        else { return [] }
        return turns
    }

    static func encode(_ turns: [AskTagTurn]) -> Data? {
        guard !turns.isEmpty else { return nil }
        return try? JSONEncoder().encode(Array(turns.suffix(maxStored)))
    }

    /// The earlier turns sent with a question: the last few, each cut to what
    /// the server accepts.
    static func history(_ turns: [AskTagTurn]) -> [AskTagTurn] {
        turns.suffix(maxHistory).map {
            AskTagTurn(id: $0.id, role: $0.role, text: String($0.text.prefix(maxTurnLength)),
                       date: $0.date)
        }
    }

    /// The question as sent: trimmed, and cut to the server's limit.
    static func cleanedQuestion(_ text: String) -> String {
        String(text.trimmingCharacters(in: .whitespacesAndNewlines).prefix(maxQuestionLength))
    }

    /// The app's language as `/assist` names it, or English.
    static func languageCode(_ preferred: [String] = Bundle.main.preferredLocalizations) -> String {
        let supported = ["en", "ro", "es", "de", "zh-Hans", "fr", "it", "pt-BR", "pl", "ja", "nl"]
        guard let first = preferred.first else { return "en" }
        if supported.contains(first) { return first }
        if first.hasPrefix("zh") { return "zh-Hans" }
        if first.hasPrefix("pt") { return "pt-BR" }
        let base = String(first.prefix { $0 != "-" && $0 != "_" })
        return supported.contains(base) ? base : "en"
    }
}

extension ScanResult {
    var askTagTurns: [AskTagTurn] {
        get { AskTagThread.decode(askTagData) }
        set { askTagData = AskTagThread.encode(newValue) }
    }
}
