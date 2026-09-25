import Foundation

/// Referrals (#97): give a friend a week of Pro, get a week of Pro.
///
/// Apple grants both weeks, as one-time offer codes; the server
/// (`backend/referral.py`) only decides who gets which code, and the app only
/// shows codes and opens Apple's redemption page. Nothing here unlocks Pro.
///
/// The server is the switch: `status().enabled == false` hides every
/// referral surface, so the feature can ship dark in a build and be turned on
/// without another release.

struct ReferralStatus: Decodable, Equatable {
    let enabled: Bool
    let code: String?
    let shareURL: URL?
    let rewards: [Reward]
    let rewardsLeftThisYear: Int?

    struct Reward: Decodable, Equatable, Identifiable {
        let code: String
        let redeemURL: URL
        let earnedAt: Int
        var id: String { code }

        enum CodingKeys: String, CodingKey {
            case code
            case redeemURL = "redeem_url"
            case earnedAt = "earned_at"
        }
    }

    enum CodingKeys: String, CodingKey {
        case enabled, code, rewards
        case shareURL = "share_url"
        case rewardsLeftThisYear = "rewards_left_this_year"
    }

    static let disabled = ReferralStatus(enabled: false, code: nil, shareURL: nil,
                                         rewards: [], rewardsLeftThisYear: nil)

    /// What a friend receives. The code and link are the two things they need;
    /// the link's page repeats the code next to an App Store button.
    var shareMessage: String? {
        guard let code, let shareURL else { return nil }
        return String(localized: "Get a free week of SnapWorth Pro: enter my invite code \(code) in the app. \(shareURL.absoluteString)")
    }
}

struct ReferralClaim: Decodable, Equatable {
    let friendCode: String
    let redeemURL: URL

    enum CodingKeys: String, CodingKey {
        case friendCode = "friend_code"
        case redeemURL = "redeem_url"
    }
}

/// Why a claim failed, worded here rather than taken from the server: the
/// backend's `detail` is English, and this screen is in five languages. The
/// status codes are the contract (`backend/referral.py`, `claim`).
enum ReferralClaimError: LocalizedError, Equatable {
    case unknownCode, ownCode, alreadyUsed, tooManyTries, paused, unavailable, other

    static func from(status: Int) -> ReferralClaimError {
        switch status {
        case 404: .unknownCode
        case 400: .ownCode
        case 409: .alreadyUsed
        case 429: .tooManyTries
        case 503: .paused
        default:  .other
        }
    }

    var errorDescription: String? {
        switch self {
        case .unknownCode:  String(localized: "That code doesn't match an invite. Check it and try again.")
        case .ownCode:      String(localized: "That's your own invite code. Share it with a friend instead.")
        case .alreadyUsed:  String(localized: "This phone has already used an invite.")
        case .tooManyTries: String(localized: "Too many tries today. Try again tomorrow.")
        case .paused:       String(localized: "Invites are paused for a moment. Try again later.")
        case .unavailable, .other:
            String(localized: "Couldn't check that code. Try again.")
        }
    }
}

actor ReferralAPIClient {
    static let shared = ReferralAPIClient()
    private init() {}

    /// Shared pinned session — see `ScanAPIClient`.
    private let session: URLSession = .snapWorthAPI

    /// The Keychain id: it survives a reinstall where the attestation subject
    /// does not, so a referrer keeps their code and their earned weeks.
    private var deviceID: String { DeviceIdentity.shared.id }

    /// The caller's invite code and earned weeks, or `.disabled`. Never throws:
    /// every referral surface hides on failure rather than showing an error for
    /// a feature the user did not ask about.
    func status() async -> ReferralStatus {
        if Config.mockScans { return Self.mockStatus }
        do {
            let (data, http) = try await post("referral/status", body: ["device_id": deviceID])
            guard (200..<300).contains(http.statusCode) else { return .disabled }
            return try JSONDecoder().decode(ReferralStatus.self, from: data)
        } catch {
            return .disabled
        }
    }

    func claim(code: String) async throws -> ReferralClaim {
        if Config.mockScans {
            return ReferralClaim(friendCode: "MOCKCODE",
                                 redeemURL: URL(string: "https://apps.apple.com/redeem?ctx=offercodes&id=6788521307&code=MOCKCODE")!)
        }
        let (data, http): (Data, HTTPURLResponse)
        do {
            (data, http) = try await post("referral/claim", body: ["device_id": deviceID, "code": code])
        } catch {
            throw ReferralClaimError.unavailable
        }
        guard (200..<300).contains(http.statusCode) else {
            throw ReferralClaimError.from(status: http.statusCode)
        }
        return try JSONDecoder().decode(ReferralClaim.self, from: data)
    }

    private func post(_ path: String, body: [String: String]) async throws -> (Data, HTTPURLResponse) {
        var request = URLRequest(url: Config.baseURL.appendingPathComponent(path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(deviceID, forHTTPHeaderField: "x-device-id")
        await request.attachBearerToken()
        request.httpBody = try JSONEncoder().encode(body)
        return try await request.sendRetryingAuth(on: session)
    }

    static let mockStatus = ReferralStatus(
        enabled: true, code: "K7Q2MX", shareURL: URL(string: "https://www.snapworth.eu/i/K7Q2MX"),
        rewards: [], rewardsLeftThisYear: 5)
}

/// Which earned weeks the user has already been told about, so the "you
/// earned a week" notice shows once per code. A per-device convenience only:
/// the codes themselves stay listed under Invite a friend.
enum ReferralRewardNotice {
    static let key = "referral.announcedRewardCodes"

    static func unannounced(_ rewards: [ReferralStatus.Reward],
                            defaults: UserDefaults = .standard) -> [ReferralStatus.Reward] {
        let seen = Set(defaults.stringArray(forKey: key) ?? [])
        return rewards.filter { !seen.contains($0.code) }
    }

    static func markAnnounced(_ rewards: [ReferralStatus.Reward], defaults: UserDefaults = .standard) {
        let seen = Set(defaults.stringArray(forKey: key) ?? []).union(rewards.map(\.code))
        defaults.set(Array(seen), forKey: key)
    }
}
