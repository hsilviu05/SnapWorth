import Foundation

/// `DELETE /me/data`: Delete my server data in Settings.
///
/// The server deletes what it keeps for this device and can only find it by
/// this device's own sign-in key, which is why this is a button and not an
/// email to support: a support id is a one-way hash of that key.
actor ServerDataClient {
    static let shared = ServerDataClient()
    private let session: URLSession = .snapWorthAPI

    struct Failure: Error { let status: Int }

    struct Outcome: Decodable, Equatable {
        let deleted: Bool
        /// An active subscription's record is kept, since Pro depends on it.
        let subscriptionKept: Bool

        enum CodingKeys: String, CodingKey {
            case deleted
            case subscriptionKept = "subscription_kept"
        }
    }

    func deleteAll() async throws -> Outcome {
        if Config.mockScans { return Outcome(deleted: true, subscriptionKept: false) }
        var req = URLRequest(url: Config.baseURL.appendingPathComponent("me/data"))
        req.httpMethod = "DELETE"
        try await req.requireBearerToken()
        let (data, http) = try await req.sendRetryingAuth(on: session)
        guard (200..<300).contains(http.statusCode) else { throw Failure(status: http.statusCode) }
        return try JSONDecoder().decode(Outcome.self, from: data)
    }
}
