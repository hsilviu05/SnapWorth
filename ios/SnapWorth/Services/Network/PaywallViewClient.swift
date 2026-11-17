import Foundation

/// `POST /events/paywall`: tells the server a paywall came on screen, so the
/// operator's weekly funnel can compare looks with trial starts without a
/// paid analytics plan. The server counts each device once a day per trigger.
///
/// Fire-and-forget: a failure costs one count and is never shown. It follows
/// the "Share anonymous analytics" switch, like every other analytics event.
enum PaywallViewClient {
    static func report(_ trigger: PaywallTrigger) {
        guard Analytics.shared.isEnabled, !Config.mockScans else { return }
        Task(priority: .utility) {
            try? await send(trigger)
        }
    }

    private static func send(_ trigger: PaywallTrigger) async throws {
        var req = URLRequest(url: Config.baseURL.appendingPathComponent("events/paywall"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(["trigger": trigger.rawValue])
        try await req.requireBearerToken()
        _ = try await req.sendRetryingAuth(on: .snapWorthAPI)
    }
}

/// `POST /events/vote`: one answer to My Finds' "What should SnapWorth add
/// next?" (`FeatureVote`). The server counts each device once per round, so a
/// retry cannot vote twice; the totals are the bot's `/votes`.
///
/// Fire-and-forget, and behind the same analytics switch as the paywall
/// report above.
enum FeatureVoteClient {
    static func report(choice: String, round: String) {
        guard Analytics.shared.isEnabled, !Config.mockScans else { return }
        Task(priority: .utility) {
            try? await send(choice: choice, round: round)
        }
    }

    private static func send(choice: String, round: String) async throws {
        var req = URLRequest(url: Config.baseURL.appendingPathComponent("events/vote"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(["choice": choice, "round": round])
        try await req.requireBearerToken()
        _ = try await req.sendRetryingAuth(on: .snapWorthAPI)
    }
}
