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
                                    startedAt: context.attributes.startedAt)
                .activityBackgroundTint(Color.wCharcoal)
                .activitySystemActionForegroundColor(Color.wBackground)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    Label("\(context.state.itemCount)", systemImage: "camera.viewfinder")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(Color.wTerracotta)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Text(context.state.formattedRange)
                        .font(.system(size: 15, weight: .bold, design: .rounded))
                        .foregroundStyle(Color.wSage)
                        .lineLimit(1)
                        .minimumScaleFactor(0.6)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    // The last item rather than the elapsed time: a timer
                    // ticking next to a money figure reads like a countdown to
                    // something, and nothing here expires.
                    Text(context.state.lastItemName.isEmpty
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
            } compactTrailing: {
                // Compact has room for one number, and the count is the one
                // that changes on every scan.
                Text("\(context.state.itemCount)")
                    .font(.system(size: 13, weight: .bold, design: .rounded))
                    .foregroundStyle(Color.wSage)
            } minimal: {
                Image(systemName: "camera.viewfinder")
                    .foregroundStyle(Color.wTerracotta)
            }
            .widgetURL(URL(string: "snapworth://scan"))
            .keylineTint(Color.wTerracotta)
        }
    }
}

struct ThriftRunLockScreenView: View {
    let state: ThriftRunAttributes.ContentState
    let startedAt: Date

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
                    .foregroundStyle(state.itemCount > 0 ? Color.wSage : Color.wWarmGray)
                    .lineLimit(1)
                    .minimumScaleFactor(0.6)

                Text(state.itemCount > 0
                     ? "\(state.findsLabel) this trip"
                     : "Scan something to start")
                    .font(.system(size: 12))
                    .foregroundStyle(Color.wWarmGray)
                    .lineLimit(1)
            }

            Spacer(minLength: 0)

            // Elapsed, not a countdown: a run has no deadline, and the system
            // keeps this ticking without the app being woken to update it.
            VStack(alignment: .trailing, spacing: 2) {
                Text(startedAt, style: .timer)
                    .font(.system(size: 15, weight: .semibold, design: .rounded))
                    .foregroundStyle(Color.wBackground.opacity(0.8))
                    .monospacedDigit()
                    .lineLimit(1)
                    .frame(maxWidth: 74, alignment: .trailing)
                Text("elapsed")
                    .font(.system(size: 10))
                    .foregroundStyle(Color.wWarmGray)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(state.itemCount > 0
                            ? "Thrift run, \(state.findsLabel), worth \(state.formattedRange)"
                            : "Thrift run started, nothing scanned yet")
    }
}
