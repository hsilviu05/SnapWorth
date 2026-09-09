import Foundation
import TelemetryDeck

/// TelemetryDeck-backed analytics. This is the **only** file that imports the
/// vendor SDK — the rest of the app talks to the `Analytics` facade.
///
/// TelemetryDeck sends: the signal name + our PII-free parameters, plus its own
/// default context (app/OS version, device model, locale) and a one-way salted
/// hash as the anonymous user identifier. No IDFA, no cross-app tracking.
final class TelemetryDeckAnalytics: AnalyticsService {
    /// The SDK holds this exact instance and re-reads it on every signal, so
    /// mutating `analyticsDisabled` here takes effect immediately — no
    /// re-initialisation, no restart.
    private let config: TelemetryDeck.Config

    init(appID: String, enabled: Bool) {
        let config = TelemetryDeck.Config(appID: appID)
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
