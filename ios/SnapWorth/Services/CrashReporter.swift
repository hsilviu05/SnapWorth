import Foundation
import MetricKit

/// Crash and hang reporting via MetricKit.
///
/// Why this exists
/// ---------------
/// Until now the app had no crash reporting of any kind. If a release crashed
/// for a subset of users — a failed store migration being the obvious risk —
/// the only signal was App Store Connect crash counts and one-star reviews,
/// both of which arrive days late and neither of which says *why*.
///
/// Why MetricKit rather than an SDK
/// --------------------------------
/// First-party, already on the device, no third-party code, no extra binary
/// weight, and no new permission prompt. The trade is that delivery is delayed:
/// iOS batches diagnostics and hands them over on a later launch, typically
/// within 24 hours. That is fine for "is this release healthy?", which is the
/// question being asked. It is not a live console.
///
/// What is deliberately NOT forwarded
/// ----------------------------------
/// **Call stacks.** `MXCallStackTree` carries symbol and binary names and is
/// large; it belongs in Xcode Organizer, which already receives it, not in an
/// analytics event.
///
/// **Raw `terminationReason`.** It is free-form text that can embed process and
/// path information. It is bucketed to a small vocabulary instead — the same
/// reasoning as `AppLaunchState.classify`.
///
/// The result is a handful of low-cardinality counters: enough to see a spike
/// and correlate it to a release, not enough to identify anyone.
final class CrashReporter: NSObject, MXMetricManagerSubscriber {

    static let shared = CrashReporter()

    private override init() { super.init() }

    /// Begins receiving payloads.
    ///
    /// Must be called *after* `AnalyticsBootstrap.start()`. MetricKit delivers
    /// asynchronously on a later runloop turn, so in practice analytics is
    /// always configured by the time a payload lands — but registering before
    /// bootstrap would leave a window where a delivered payload is dropped by
    /// the no-op analytics backend, which is the same trap Item 1 hit.
    func start() {
        MXMetricManager.shared.add(self)
    }

    func stop() {
        MXMetricManager.shared.remove(self)
    }

    // MARK: - MXMetricManagerSubscriber

    /// Diagnostics: crashes, hangs, CPU and disk-write exceptions.
    func didReceive(_ payloads: [MXDiagnosticPayload]) {
        for payload in payloads {
            for crash in payload.crashDiagnostics ?? [] {
                let summary = DiagnosticSummary.crash(
                    exceptionType: crash.exceptionType?.intValue,
                    signal: crash.signal?.intValue,
                    terminationReason: crash.terminationReason
                )
                Analytics.shared.track(
                    .crashReported(signal: summary.signal, termination: summary.termination))
            }

            for hang in payload.hangDiagnostics ?? [] {
                let seconds = hang.hangDuration.converted(to: .seconds).value
                Analytics.shared.track(
                    .hangReported(bucket: DiagnosticSummary.durationBucket(seconds)))
            }
        }
    }

    /// Aggregate metrics. Only launch responsiveness is forwarded — the rest
    /// (disk, network, cellular) answers questions nobody is asking yet, and
    /// every extra event is analytics cost for no decision.
    func didReceive(_ payloads: [MXMetricPayload]) {
        for payload in payloads {
            guard let launch = payload.applicationLaunchMetrics else { continue }
            let histogram = launch.histogrammedTimeToFirstDraw
            guard let median = DiagnosticSummary.medianSeconds(from: histogram) else { continue }
            Analytics.shared.track(
                .launchTimeReported(bucket: DiagnosticSummary.durationBucket(median)))
        }
    }
}

/// Pure mapping from raw MetricKit values to the compact, non-identifying
/// summary that is safe to send off-device.
///
/// Split out from `CrashReporter` deliberately: `MXDiagnosticPayload` and its
/// members have no public initialiser, so they cannot be constructed in a unit
/// test. Keeping the mapping as free functions over primitives means the part
/// that decides *what leaves the device* is fully testable, and only the thin
/// subscriber shell is not.
enum DiagnosticSummary {

    struct Crash: Equatable {
        let signal: String
        let termination: String
    }

    /// Signal numbers are stable POSIX constants and safe to name directly.
    /// Anything unrecognised is bucketed rather than passed through, so an
    /// unexpected value cannot widen the event's cardinality.
    static func crash(exceptionType: Int?,
                      signal: Int?,
                      terminationReason: String?) -> Crash {
        Crash(signal: signalName(signal, exceptionType: exceptionType),
              termination: terminationBucket(terminationReason))
    }

    static func signalName(_ signal: Int?, exceptionType: Int? = nil) -> String {
        switch signal {
        case 4:  return "SIGILL"
        case 5:  return "SIGTRAP"     // Swift runtime traps: force-unwrap, OOB
        case 6:  return "SIGABRT"     // uncaught exception, assertion
        case 8:  return "SIGFPE"
        case 10: return "SIGBUS"
        case 11: return "SIGSEGV"
        case 13: return "SIGPIPE"
        case 9:  return "SIGKILL"     // usually watchdog or OOM
        case .some:
            return "signal_other"
        case nil:
            // No signal: a Mach exception or a watchdog termination.
            return exceptionType == nil ? "unknown" : "mach_exception"
        }
    }

    /// Buckets the free-form termination reason.
    ///
    /// The raw string is never forwarded. It is written by the OS and can embed
    /// process names and paths, and its cardinality is effectively unbounded —
    /// either property alone would disqualify it from an analytics parameter.
    static func terminationBucket(_ reason: String?) -> String {
        guard let reason, !reason.isEmpty else { return "none" }
        let text = reason.lowercased()

        if text.contains("watchdog") || text.contains("0x8badf00d") {
            return "watchdog"
        }
        if text.contains("memory") || text.contains("jetsam") || text.contains("0x8badf00d") {
            return "memory_pressure"
        }
        if text.contains("background") {
            return "background_task_timeout"
        }
        if text.contains("namespace signal") || text.contains("signal") {
            return "signal"
        }
        return "other"
    }

    /// Coarse duration buckets, in seconds.
    ///
    /// Bucketed rather than sent as a number so the event stays groupable and
    /// cannot become a per-user fingerprint.
    static func durationBucket(_ seconds: Double) -> String {
        switch seconds {
        case ..<0:      return "invalid"
        case ..<0.5:    return "under_0.5s"
        case ..<1:      return "0.5s_1s"
        case ..<2:      return "1s_2s"
        case ..<5:      return "2s_5s"
        case ..<10:     return "5s_10s"
        default:        return "over_10s"
        }
    }

    /// Approximate median from a MetricKit histogram.
    ///
    /// MetricKit reports bucketed counts rather than raw samples, so this walks
    /// the buckets to the halfway point and returns that bucket's start. That
    /// is an approximation by construction — which is why the result is then
    /// bucketed again rather than reported as a precise figure.
    static func medianSeconds(
        from histogram: MXHistogram<UnitDuration>
    ) -> Double? {
        let buckets = histogram.bucketEnumerator.compactMap { $0 as? MXHistogramBucket<UnitDuration> }
        guard !buckets.isEmpty else { return nil }

        let total = buckets.reduce(0) { $0 + $1.bucketCount }
        guard total > 0 else { return nil }

        var seen = 0
        for bucket in buckets {
            seen += bucket.bucketCount
            if seen >= total / 2 {
                return bucket.bucketStart.converted(to: .seconds).value
            }
        }
        return buckets.last?.bucketStart.converted(to: .seconds).value
    }
}
