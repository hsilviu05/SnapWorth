import WidgetKit
import SwiftUI

// ── Timeline ──────────────────────────────────────────────────────────────────

struct QuickScanEntry: TimelineEntry {
    let date: Date
    // Haul data shown as a small motivator below the button
    let itemCount: Int
}

struct QuickScanProvider: TimelineProvider {
    func placeholder(in context: Context) -> QuickScanEntry {
        QuickScanEntry(date: .now, itemCount: 8)
    }

    func getSnapshot(in context: Context, completion: @escaping (QuickScanEntry) -> Void) {
        let count = context.isPreview ? 8 : WidgetReader.readHaul().itemCount
        completion(QuickScanEntry(date: .now, itemCount: count))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<QuickScanEntry>) -> Void) {
        let count = WidgetReader.readHaul().itemCount
        let entry = QuickScanEntry(date: .now, itemCount: count)
        let next  = Calendar.current.date(byAdding: .hour, value: 1, to: .now)!
        completion(Timeline(entries: [entry], policy: .after(next)))
    }
}

// ── View ──────────────────────────────────────────────────────────────────────

struct QuickScanWidgetView: View {
    let entry: QuickScanEntry

    var body: some View {
        // The gradient is the widget's `containerBackground` rather than a
        // rectangle drawn as content — see `HaulWidgetSmallView` for why.
        ZStack {
            VStack(spacing: 0) {
                // Header wordmark
                HStack {
                    Text("SnapWorth")
                        .font(.system(size: 11, weight: .semibold, design: .serif))
                        .foregroundStyle(Color.wBackground)
                    Spacer()
                }

                Spacer()

                // Camera icon
                ZStack {
                    Circle()
                        .fill(Color.wBackground.opacity(0.18))
                        .frame(width: 52, height: 52)

                    Image(systemName: "camera.fill")
                        .font(.system(size: 24, weight: .medium))
                        .foregroundStyle(Color.wBackground)
                }

                Spacer()

                // Label
                VStack(spacing: 2) {
                    Text("Scan now")
                        .font(.system(size: 14, weight: .bold))
                        .foregroundStyle(Color.wBackground)

                    if entry.itemCount > 0 {
                        Text("\(WidgetHaulData.itemsLabel(entry.itemCount)) in your haul")
                            .font(.system(size: 10, weight: .medium))
                            .foregroundStyle(Color.wBackground)
                    } else {
                        Text("Find out what it's worth")
                            .font(.system(size: 10, weight: .medium))
                            .foregroundStyle(Color.wBackground)
                    }
                }
            }
            .padding(14)
        }
    }
}

// ── Widget ────────────────────────────────────────────────────────────────────

struct QuickScanWidget: Widget {
    let kind = "QuickScanWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: QuickScanProvider()) { entry in
            QuickScanWidgetView(entry: entry)
                // Deep-links directly to the camera scan screen
                .widgetURL(URL(string: "snapworth://scan"))
                // Terracotta at the *fill* values, and the labels above it
                // at full cream. It was `wTerracotta -> #B84E2A` with the
                // 10pt caption at 65% cream: 2.93:1, under even the 3:1 floor
                // for non-text. The worst point on this tile is now 5.43:1.
                .containerBackground(for: .widget) {
                    LinearGradient(
                        colors: [Color.wTerracottaFill, Color.wTerracottaFillDeep],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing)
                }
        }
        .configurationDisplayName("Quick Scan")
        .description("One tap to scan any secondhand item.")
        .supportedFamilies([.systemSmall])
    }
}
