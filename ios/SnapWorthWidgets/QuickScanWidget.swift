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
        ZStack {
            // Background — terracotta gradient
            LinearGradient(
                colors: [Color.wTerracotta, Color(hex: "B84E2A")],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )

            VStack(spacing: 0) {
                // Header wordmark
                HStack {
                    Text("SnapWorth")
                        .font(.system(size: 11, weight: .semibold, design: .serif))
                        .foregroundStyle(Color.wBackground.opacity(0.75))
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
                        Text("\(entry.itemCount) items in your haul")
                            .font(.system(size: 10, weight: .medium))
                            .foregroundStyle(Color.wBackground.opacity(0.65))
                    } else {
                        Text("Find out what it's worth")
                            .font(.system(size: 10, weight: .medium))
                            .foregroundStyle(Color.wBackground.opacity(0.65))
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
                .containerBackground(Color.wTerracotta, for: .widget)
        }
        .configurationDisplayName("Quick Scan")
        .description("One tap to scan any secondhand item.")
        .supportedFamilies([.systemSmall])
    }
}
