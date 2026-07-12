import WidgetKit
import SwiftUI

// ── Timeline ──────────────────────────────────────────────────────────────────

struct HaulEntry: TimelineEntry {
    let date: Date
    let haul: WidgetHaulData
}

struct HaulProvider: TimelineProvider {
    func placeholder(in context: Context) -> HaulEntry {
        HaulEntry(date: .now, haul: .placeholder)
    }

    func getSnapshot(in context: Context, completion: @escaping (HaulEntry) -> Void) {
        completion(HaulEntry(date: .now, haul: context.isPreview ? .placeholder : WidgetReader.readHaul()))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<HaulEntry>) -> Void) {
        let entry = HaulEntry(date: .now, haul: WidgetReader.readHaul())
        // Refresh every hour; the app also calls reloadAllTimelines() after each scan
        let next = Calendar.current.date(byAdding: .hour, value: 1, to: .now)!
        completion(Timeline(entries: [entry], policy: .after(next)))
    }
}

extension WidgetHaulData {
    static let placeholder = WidgetHaulData(
        totalLow: 348, totalHigh: 620, itemCount: 8,
        lastItemName: "Patagonia Fleece",
        lastItemRange: "$60 – $95",
        updatedAt: .now
    )
}

// ── Small widget ──────────────────────────────────────────────────────────────

struct HaulWidgetSmallView: View {
    let haul: WidgetHaulData

    var body: some View {
        ZStack {
            Color.wCharcoal

            VStack(alignment: .leading, spacing: 0) {
                // Header
                HStack(spacing: 4) {
                    Image(systemName: "camera.viewfinder")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Color.wTerracotta)
                    Text("SnapWorth")
                        .font(.system(size: 11, weight: .semibold, design: .serif))
                        .foregroundStyle(Color.wBackground.opacity(0.7))
                }

                Spacer()

                // Value
                Text(haul.itemCount > 0 ? haul.formattedRange : "No scans yet")
                    .font(.system(size: haul.itemCount > 0 ? 20 : 14,
                                  weight: .bold, design: .serif))
                    .foregroundStyle(haul.itemCount > 0 ? Color.wSage : Color.wWarmGray)
                    .minimumScaleFactor(0.6)
                    .lineLimit(1)
                    .padding(.bottom, 2)

                // Label
                Text(haul.itemCount > 0
                     ? "\(haul.itemCount) item\(haul.itemCount == 1 ? "" : "s") scanned"
                     : "Scan your first find")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(Color.wWarmGray)
            }
            .padding(14)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
    }
}

// ── Medium widget ─────────────────────────────────────────────────────────────

struct HaulWidgetMediumView: View {
    let haul: WidgetHaulData

    var body: some View {
        ZStack {
            Color.wCharcoal

            HStack(spacing: 0) {
                // Left — haul value
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 4) {
                        Image(systemName: "camera.viewfinder")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(Color.wTerracotta)
                        Text("SnapWorth")
                            .font(.system(size: 11, weight: .semibold, design: .serif))
                            .foregroundStyle(Color.wBackground.opacity(0.7))
                    }

                    Spacer()

                    Text("Your haul")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(Color.wWarmGray)

                    Text(haul.itemCount > 0 ? haul.formattedRange : "$0")
                        .font(.system(size: 22, weight: .bold, design: .serif))
                        .foregroundStyle(Color.wSage)
                        .minimumScaleFactor(0.6)
                        .lineLimit(1)

                    Text("\(haul.itemCount) item\(haul.itemCount == 1 ? "" : "s")")
                        .font(.system(size: 12))
                        .foregroundStyle(Color.wWarmGray)
                }
                .frame(maxWidth: .infinity, alignment: .leading)

                // Divider
                Rectangle()
                    .fill(Color.wWarmGray.opacity(0.2))
                    .frame(width: 1)
                    .padding(.vertical, 14)

                // Right — last scanned item
                VStack(alignment: .leading, spacing: 4) {
                    Text("Last scanned")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(Color.wWarmGray)

                    Spacer()

                    if haul.itemCount > 0 {
                        Text(haul.lastItemName)
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(Color.wBackground)
                            .lineLimit(2)
                            .minimumScaleFactor(0.8)

                        Text(haul.lastItemRange)
                            .font(.system(size: 16, weight: .bold, design: .serif))
                            .foregroundStyle(Color.wSage)
                    } else {
                        Text("Tap to scan\nyour first find")
                            .font(.system(size: 13))
                            .foregroundStyle(Color.wWarmGray)
                            .lineLimit(2)
                    }

                    Spacer()

                    // Scan CTA chip
                    Label("Scan", systemImage: "camera.fill")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Color.wBackground)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(Color.wTerracotta)
                        .clipShape(Capsule())
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.leading, 14)
            }
            .padding(14)
        }
    }
}

// ── Entry view (routes by family) ─────────────────────────────────────────────

struct HaulWidgetEntryView: View {
    let entry: HaulEntry
    @Environment(\.widgetFamily) private var family

    var body: some View {
        switch family {
        case .systemMedium:
            HaulWidgetMediumView(haul: entry.haul)
        default:
            HaulWidgetSmallView(haul: entry.haul)
        }
    }
}

// ── Widget ────────────────────────────────────────────────────────────────────

struct HaulWidget: Widget {
    let kind = "HaulWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: HaulProvider()) { entry in
            HaulWidgetEntryView(entry: entry)
                .widgetURL(URL(string: "snapworth://history"))
                .containerBackground(Color.wCharcoal, for: .widget)
        }
        .configurationDisplayName("Haul Value")
        .description("See your total resale value at a glance.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}
