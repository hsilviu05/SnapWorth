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
                        .font(.system(size: 15, weight: .semibold))
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
                        .font(.system(size: 15, weight: .bold, design: .rounded))
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
                         ? "Last known — open SnapWorth to refresh"
                         : context.state.lastItemName.isEmpty
                           ? "Scan something to start the run"
                           : "Last: \(context.state.lastItemName)")
                        .font(.system(size: 12))
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
                    .font(.system(size: 13, weight: .bold, design: .rounded))
                    .foregroundStyle(context.isStale ? Color.wWarmGray : Color.wSage)
                    // The icon beside it is hidden, so this one label carries
                    // the compact presentation on its own.
                    .accessibilityLabel("Thrift run, \(context.state.findsLabel)")
            } minimal: {
                Image(systemName: "camera.viewfinder")
                    .foregroundStyle(Color.wTerracotta)
                    .accessibilityLabel("Thrift run in progress, "
                                        + context.state.findsLabel)
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
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Color.wTerracotta)
                    Text("Thrift run")
                        .font(.system(size: 11, weight: .semibold, design: .serif))
                        .foregroundStyle(Color.wBackground.opacity(0.7))
                }

                Text(state.itemCount > 0 ? state.formattedRange : "Nothing yet")
                    .font(.system(size: 20, weight: .bold, design: .rounded))
                    .foregroundStyle(state.itemCount > 0 && !isStale
                                     ? Color.wSage : Color.wWarmGray)
                    .lineLimit(1)
                    .minimumScaleFactor(0.6)

                Text(subtitle)
                    .font(.system(size: 12))
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
                .font(.system(size: 15, weight: .semibold, design: .rounded))
                .foregroundStyle(Color.wBackground.opacity(isStale ? 0.6 : 0.8))
                .monospacedDigit()
                .lineLimit(1)
                .frame(maxWidth: 74, alignment: .trailing)

                Text(isStale ? "started" : "elapsed")
                    .font(.system(size: 10))
                    .foregroundStyle(Color.wWarmGray)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(spokenLabel)
    }

    private var subtitle: String {
        if isStale { return "Last known — open SnapWorth to refresh" }
        return state.itemCount > 0 ? "\(state.findsLabel) this trip"
                                   : "Scan something to start"
    }

    private var spokenLabel: String {
        guard state.itemCount > 0 else {
            return isStale ? "Thrift run, last known, nothing scanned yet"
                           : "Thrift run started, nothing scanned yet"
        }
        // `formattedRange` is display text; spoken, the en dash is either read
        // as "dash" or dropped, running the two figures together.
        let body = "Thrift run, \(state.findsLabel), "
                 + "worth \(WidgetHaulData.spoken(state.formattedRange))"
        return isStale ? body + ". Last known — open SnapWorth to refresh." : body
    }
}
