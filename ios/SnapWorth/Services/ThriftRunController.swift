import ActivityKit
import Foundation

/// Starts, updates and ends the thrift-run Live Activity.
///
/// The running state is read from `Activity.activities` rather than held in a
/// stored property. That matters: a stored reference dies with the process, so
/// a run started before a force-quit — or before the app was simply evicted
/// while the user shopped — would leave an Activity on the Lock Screen that
/// nothing in the app could see, and therefore nothing could end. The user's
/// only recourse would be swiping it away, and the app would happily start a
/// second one on top.
@MainActor
enum ThriftRunController {

    /// A run nobody ended. People put the phone in a pocket and go home; the
    /// Activity should not still be there the next morning claiming to be live.
    static let maximumRunDuration: TimeInterval = 8 * 60 * 60

    /// After this the system dims the Activity, signalling "this may be out of
    /// date" without the app having to be woken to say so.
    static let staleAfter: TimeInterval = 90 * 60

    static var current: Activity<ThriftRunAttributes>? {
        Activity<ThriftRunAttributes>.activities.first
    }

    static var isRunning: Bool { current != nil }

    /// False when the user has switched Live Activities off for the app, or the
    /// device does not support them. Callers use this to hide the control
    /// rather than offer a button that silently does nothing.
    static var isAvailable: Bool {
        ActivityAuthorizationInfo().areActivitiesEnabled
    }

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    @discardableResult
    static func start(now: Date = Date()) -> Bool {
        guard isAvailable, !isRunning else { return false }
        let attributes = ThriftRunAttributes(startedAt: now)
        do {
            _ = try Activity.request(
                attributes: attributes,
                content: ActivityContent(state: .empty,
                                         staleDate: now.addingTimeInterval(staleAfter)),
                pushType: nil)
            return true
        } catch {
            // Throws when the user revoked permission between the check above
            // and here, or too many Activities are live. Never fatal: the run
            // is a convenience layered on scanning, not a precondition for it.
            return false
        }
    }

    static func end() async {
        for activity in Activity<ThriftRunAttributes>.activities {
            await activity.end(nil, dismissalPolicy: .immediate)
        }
    }

    /// Recompute from the library. Ends a run that has outlived its welcome.
    ///
    /// Counts only scans at or after `startedAt`, which is what makes this a
    /// *run* rather than a second copy of the haul widget: the number answers
    /// "what have I found since I walked in", the one worth knowing while
    /// deciding whether to pick the next thing up.
    static func update(results: [ScanResult], now: Date = Date()) async {
        guard let activity = current else { return }
        let startedAt = activity.attributes.startedAt

        guard now.timeIntervalSince(startedAt) < maximumRunDuration else {
            await end()
            return
        }

        let thisRun = results.filter { $0.timestamp >= startedAt }
        let low = NSDecimalNumber(decimal: thisRun.reduce(Decimal.zero) {
            $0 + $1.priceRange(for: $1.condition).low
        }).doubleValue
        let high = NSDecimalNumber(decimal: thisRun.reduce(Decimal.zero) {
            $0 + $1.priceRange(for: $1.condition).high
        }).doubleValue

        let state = ThriftRunAttributes.ContentState(
            itemCount: thisRun.count,
            totalLow: low,
            totalHigh: high,
            lastItemName: thisRun.max(by: { $0.timestamp < $1.timestamp })?.itemName ?? "")

        await activity.update(
            ActivityContent(state: state,
                            staleDate: now.addingTimeInterval(staleAfter)))
    }
}
