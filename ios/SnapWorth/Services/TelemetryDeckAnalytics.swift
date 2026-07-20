import Foundation
import TelemetryDeck

/// TelemetryDeck-backed analytics. This is the **only** file that imports the
/// vendor SDK — the rest of the app talks to the `Analytics` facade.
///
/// TelemetryDeck sends: the signal name + our PII-free parameters, plus its own
/// default context (app/OS version, device model, locale) and a one-way salted
/// hash as the anonymous user identifier. No IDFA, no cross-app tracking.
final class TelemetryDeckAnalytics: AnalyticsService {
    init(appID: String) {
        TelemetryDeck.initialize(config: .init(appID: appID))
    }

    func track(_ event: AnalyticsEvent) {
        TelemetryDeck.signal(event.name, parameters: event.parameters)
    }
}

/// Wires the analytics backend at launch. Stays a no-op until a TelemetryDeck
/// app ID is set in `Config`, so debug builds and forks send nothing.
enum AnalyticsBootstrap {
    static func start() {
        let appID = Config.telemetryDeckAppID.trimmingCharacters(in: .whitespaces)
        guard !appID.isEmpty else { return }
        Analytics.shared.configure(TelemetryDeckAnalytics(appID: appID))
        Analytics.shared.track(.appOpened)
    }
}
