import ActivityKit
import SwiftUI
import WidgetKit

// ── Thrift run ───────────────────────────────────────────────────────────────
//
// The one surface that matches how the app is actually used. A scan is not a
// single event you come back from — you are in a shop for forty minutes doing
// six of them, deciding each time whether the next thing is worth the money.
// This keeps the running total of *this trip* on the Lock Screen and in the
// Dynamic Island while that happens.

struct ThriftRunLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: ThriftRunAttributes.self) { context in
            ThriftRunLockScreenView(state: context.state,
                                    startedAt: context.attributes.startedAt,
                                    isStale: context.isStale)
                // The Dynamic Island carried this and the Lock Screen did not,
                // so a tap on the banner — the surface you actually see with
                // the phone locked, mid-run, which is the whole point of the
                // feature — opened the app on whatever tab was last selected
                // instead of the camera.
                .widgetURL(URL(string: "snapworth://scan"))
                .activityBackgroundTint(Color.wCharcoal)
                .activitySystemActionForegroundColor(Color.wBackground)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    Label("\(context.state.itemCount)", systemImage: "camera.viewfinder")
                        .wFont(15, weight: .semibold)
                        .foregroundStyle(Color.wTerracotta)
                        // Without this the region announced
                        // "camera.viewfinder, 3" — a symbol name and a bare
                        // number. Every presentation below was unlabelled the
                        // same way, and the minimal one announced nothing but
                        // the symbol name.
                        .accessibilityLabel(context.state.findsLabel)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Text(context.state.formattedRange)
                        .wFont(15, weight: .bold, design: .rounded)
                        .foregroundStyle(context.isStale ? Color.wWarmGray : Color.wSage)
                        .lineLimit(1)
                        .minimumScaleFactor(0.6)
                        .accessibilityLabel(
                            WidgetHaulData.spoken(context.state.formattedRange))
                }
                DynamicIslandExpandedRegion(.bottom) {
                    // The last item rather than the elapsed time: a timer
                    // ticking next to a money figure reads like a countdown to
                    // something, and nothing here expires.
                    Text(context.isStale
                         ? String(localized: "Last known — open SnapWorth to refresh")
                         : context.state.lastItemName.isEmpty
                           ? String(localized: "Scan something to start the run")
                           : String(localized: "Last: \(context.state.lastItemName)"))
                        .wFont(12)
                        .foregroundStyle(Color.wWarmGray)
                        .lineLimit(1)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            } compactLeading: {
                Image(systemName: "camera.viewfinder")
                    .foregroundStyle(Color.wTerracotta)
                    .accessibilityHidden(true)
            } compactTrailing: {
                // Compact has room for one number, and the count is the one
                // that changes on every scan.
                Text("\(context.state.itemCount)")
                    .wFont(13, weight: .bold, design: .rounded)
                    .foregroundStyle(context.isStale ? Color.wWarmGray : Color.wSage)
                    // The icon beside it is hidden, so this one label carries
                    // the compact presentation on its own.
                    .accessibilityLabel(String(localized: "Thrift run, \(context.state.findsLabel)"))
            } minimal: {
                Image(systemName: "camera.viewfinder")
                    .foregroundStyle(Color.wTerracotta)
                    .accessibilityLabel(String(localized:
                        "Thrift run in progress, \(context.state.findsLabel)"))
            }
            .widgetURL(URL(string: "snapworth://scan"))
            .keylineTint(Color.wTerracotta)
        }
    }
}

struct ThriftRunLockScreenView: View {
    let state: ThriftRunAttributes.ContentState
    let startedAt: Date
    /// `context.isStale`: the app has not updated this Activity since its
    /// `staleDate`, so the figure is last-known rather than current.
    ///
    /// Nothing read this. The app sets a `staleDate` 90 minutes out, but
    /// setting the flag and rendering it are two different jobs — so someone
    /// who scanned three things, pocketed the phone and drove home saw the
    /// same live-looking total with the elapsed timer climbing past the
    /// 8-hour cap the app uses to end a run it can no longer see.
    var isStale: Bool = false

    var body: some View {
        HStack(alignment: .center, spacing: 14) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 4) {
                    Image(systemName: "camera.viewfinder")
                        .wFont(11, weight: .semibold)
                        .foregroundStyle(Color.wTerracotta)
                    Text("Thrift run")
                        .wFont(11, weight: .semibold, design: .serif)
                        .foregroundStyle(Color.wBackground.opacity(0.7))
                }

                Text(state.itemCount > 0
                     ? state.formattedRange
                     : String(localized: "Nothing yet"))
                    .wFont(20, weight: .bold, design: .rounded)
                    .foregroundStyle(state.itemCount > 0 && !isStale
                                     ? Color.wSage : Color.wWarmGray)
                    .lineLimit(1)
                    .minimumScaleFactor(0.6)

                Text(subtitle)
                    .wFont(12)
                    .foregroundStyle(Color.wWarmGray)
                    .lineLimit(1)
            }

            Spacer(minLength: 0)

            // Elapsed, not a countdown: a run has no deadline, and the system
            // keeps this ticking without the app being woken to update it.
            //
            // Which is exactly why it stops once the Activity is stale. A
            // timer the system keeps climbing is a claim that the run is still
            // going; when the figure beside it is last-known, the honest
            // thing to show is when the run began.
            VStack(alignment: .trailing, spacing: 2) {
                Group {
                    if isStale {
                        Text(startedAt, style: .time)
                    } else {
                        Text(startedAt, style: .timer)
                    }
                }
                .wFont(15, weight: .semibold, design: .rounded)
                .foregroundStyle(Color.wBackground.opacity(isStale ? 0.6 : 0.8))
                .monospacedDigit()
                .lineLimit(1)
                .frame(maxWidth: 74, alignment: .trailing)

                Text(isStale ? "started" : "elapsed")
                    .wFont(10)
                    .foregroundStyle(Color.wWarmGray)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(spokenLabel)
    }

    private var subtitle: String {
        if isStale { return String(localized: "Last known — open SnapWorth to refresh") }
        return state.itemCount > 0
            ? String(localized: "\(state.findsLabel) this trip")
            : String(localized: "Scan something to start")
    }

    private var spokenLabel: String {
        guard state.itemCount > 0 else {
            return isStale
                ? String(localized: "Thrift run, last known, nothing scanned yet")
                : String(localized: "Thrift run started, nothing scanned yet")
        }
        // `formattedRange` is display text; spoken, the en dash is either read
        // as "dash" or dropped, running the two figures together.
        //
        // The third figure on the banner is the time, and `children: .combine`
        // below discards the timer's own synthesised label — so it reaches
        // VoiceOver only if this string names it. What it names is the
        // *start*, not the elapsed count on the right: that one is
        // `Text(style: .timer)`, which the system keeps climbing without
        // re-rendering this view, so a duration spelled into a string here
        // would freeze at the last scan and be confidently wrong an hour
        // later. When the run began cannot go stale, and the stale branch
        // already shows exactly that.
        let started = startedAt.formatted(date: .omitted, time: .shortened)
        let body = String(localized:
            "Thrift run, \(state.findsLabel), worth \(WidgetHaulData.spoken(state.formattedRange)), started \(started)")
        guard isStale else { return body }
        return String(localized: "\(body). Last known — open SnapWorth to refresh.")
    }
}
