import Foundation
import TelemetryDeck

/// TelemetryDeck-backed analytics. This is the **only** file that imports the
/// vendor SDK — the rest of the app talks to the `Analytics` facade.
///
/// TelemetryDeck sends the signal name + our PII-free parameters, and a one-way
/// salted hash as the anonymous user identifier. No IDFA, no cross-app tracking.
///
/// It also attaches a **default payload we do not choose**, and this comment
/// used to describe it as "app/OS version, device model, locale" and stop
/// there. At the pinned 2.14.1 (`Package.resolved`, revision `ad4a03e`) it is
/// considerably more:
///
/// * `Signal+Helpers.swift:55-72` — seven accessibility settings:
///   `isReduceMotionEnabled`, `isBoldTextEnabled`, `isInvertColorsEnabled`,
///   `isDarkerSystemColorsEnabled`, `isReduceTransparencyEnabled`,
///   `shouldDifferentiateWithoutColor`, `preferredContentSizeCategory`.
/// * `Signal.swift:116-130` — `Acquisition.firstSessionDate` and five
///   `Retention.*` counters (average session seconds, distinct days used,
///   distinct days last month, total sessions, previous session seconds).
/// * `Signal.swift:73-84` — `Device.timeZone`, `screenResolutionWidth`/
///   `Height`, `screenScaleFactor`, `architecture`, `orientation`.
///
/// None of it is gated by anything the app sets: `config.sessionStatsEnabled`
/// gates only `SessionManager.startNewSession()`, not the payload block, and
/// there is no flag at all for the accessibility one. The only switch is
/// `analyticsDisabled` below, which stops everything.
///
/// So the inventory is stated here in full, the in-app privacy policy names the
/// same categories, and `PrivacyInfo.xcprivacy` declares
/// `NSPrivacyCollectedDataTypeOtherDataTypes` for the parts no named type
/// covers. An SDK bump that adds another field makes all three wrong together —
/// check this list when the pin moves.
final class TelemetryDeckAnalytics: AnalyticsService {
    /// The SDK holds this exact instance and re-reads it on every signal, so
    /// mutating `analyticsDisabled` here takes effect immediately — no
    /// re-initialisation, no restart.
    private let config: TelemetryDeck.Config

    init(appID: String, enabled: Bool) {
        // `salt:` was omitted, and it defaults to the empty string — so the
        // "salted hash" this file's own header and the in-app privacy policy
        // both describe was a plain `sha256(IDFV)`. See `Config.telemetryDeckSalt`.
        let config = TelemetryDeck.Config(appID: appID, salt: Config.telemetryDeckSalt)
        // Without this the opt-out only silenced *our* signals: the SDK sends
        // `TelemetryDeck.Session.started` and `Acquisition.newInstallDetected`
        // itself at launch and on every foreground, each carrying the salted
        // per-device identifier. `Analytics.track`'s guard never sees those —
        // they are emitted inside the SDK, and `analyticsDisabled` is the only
        // thing that stops them.
        config.analyticsDisabled = !enabled
        self.config = config
        TelemetryDeck.initialize(config: config)
    }

    func track(_ event: AnalyticsEvent) {
        TelemetryDeck.signal(event.name, parameters: event.parameters)
    }

    func setEnabled(_ enabled: Bool) {
        config.analyticsDisabled = !enabled
    }
}

/// Wires the analytics backend at launch. Stays a no-op until a TelemetryDeck
/// app ID is set in `Config`, so debug builds and forks send nothing.
enum AnalyticsBootstrap {
    static func start() {
        let appID = Config.telemetryDeckAppID.trimmingCharacters(in: .whitespaces)
        guard !appID.isEmpty else { return }
        Analytics.shared.configure(
            TelemetryDeckAnalytics(appID: appID, enabled: Analytics.shared.isEnabled))
        Analytics.shared.track(.appOpened)
    }
}
