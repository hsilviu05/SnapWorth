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

    /// After this the Activity's `context.isStale` flips true, and the views
    /// render a last-known state instead of a live one.
    ///
    /// The system does *not* dim it for you — an earlier comment here said it
    /// did, which is why nothing read `isStale` for a while and an abandoned
    /// run kept looking live with its timer climbing past the 8-hour cap.
    /// `staleDate` sets a flag; presenting it is the widget's job.
    static let staleAfter: TimeInterval = 90 * 60

    /// When the Activity should declare itself out of date, never later than
    /// the moment `update` would end the run — otherwise a scan at 7h55m would
    /// push the stale date to 9h25m, past a run the app already considers over.
    static func staleDate(now: Date, startedAt: Date) -> Date {
        min(now.addingTimeInterval(staleAfter),
            startedAt.addingTimeInterval(maximumRunDuration))
    }

    /// The live run, if there is one.
    ///
    /// `activities` keeps an Activity after it finishes — `.ended` when
    /// something ended it, `.dismissed` when the user swiped it away — and the
    /// system removes those asynchronously. Taking `.first` unconditionally
    /// counted a finished Activity as a live run, so `start()` refused and the
    /// user could not begin a new one until the system got round to reaping
    /// the old one.
    ///
    /// Matched by exclusion rather than `== .active`, so `.stale` — a run that
    /// is still on screen and still the user's — keeps counting, and so does
    /// any state a later iOS adds. `.stale` itself is 17.2 and the deployment
    /// target is 17.0, which is the other reason not to name it.
    static var current: Activity<ThriftRunAttributes>? {
        Activity<ThriftRunAttributes>.activities.first { activity in
            switch activity.activityState {
            case .ended, .dismissed: return false
            default:                 return true
            }
        }
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
                                         staleDate: staleDate(now: now, startedAt: now)),
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

    /// Ends a run past the cap without waiting for a scan to notice.
    ///
    /// The cap lived only inside `update`, and `update` is reached only from
    /// the scan-mutation paths in `ScanRepository` — so it could not fire for
    /// the one case it exists for: a run somebody started and then stopped
    /// scanning on. `ScanView` re-reads `isRunning` on foreground and its
    /// comment there claimed a run could "hit the eight-hour cap"; nothing
    /// implemented that, which is what this closes.
    ///
    /// What it buys is dismissal, not the ending. ActivityKit ends an Activity
    /// of its own accord at eight hours, and `current` already excludes
    /// `.ended`, so the app's own state was never wrong. But the system's end
    /// leaves the Activity sitting on the Lock Screen for up to four hours
    /// more, and `end()` here dismisses it immediately — which is what the
    /// comment on `maximumRunDuration` actually asks for: "the Activity should
    /// not still be there the next morning claiming to be live."
    @discardableResult
    static func endIfExpired(now: Date = Date()) async -> Bool {
        guard let activity = current,
              hasExpired(startedAt: activity.attributes.startedAt, now: now)
        else { return false }
        await end()
        return true
    }

    /// Whether a run started at `startedAt` has outlived the cap.
    ///
    /// Split out for the same reason `staleDate` is: `endIfExpired` has to ask
    /// ActivityKit for a live Activity, and no test can hand it one — so the
    /// decision is tested apart from the thing it decides about.
    ///
    /// `>=`, not `>`: `staleDate` already pins the stale moment to exactly
    /// `startedAt + maximumRunDuration`, so the instant the run declares
    /// itself finally stale is the instant it is over.
    static func hasExpired(startedAt: Date, now: Date) -> Bool {
        now.timeIntervalSince(startedAt) >= maximumRunDuration
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
                            staleDate: staleDate(now: now, startedAt: startedAt)))
    }
}
