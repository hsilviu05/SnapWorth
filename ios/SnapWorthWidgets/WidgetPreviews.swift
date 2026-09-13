//
//  WidgetPreviews.swift
//  Canvas previews for all six widgets, at the sizes they ship in.
//
//  These exist because there was no other cheap way to look at a widget. A
//  widget only draws what is in the App Group blob, so seeing a real one meant
//  running the app in a simulator, completing a scan against the backend, and
//  waiting for a timeline reload — for every change, including one-word copy
//  changes. Everything below renders from a literal, in this file, instantly.
//
//  Wrapped in `#if DEBUG`: `#Preview` generates a registered preview type, and
//  none of it belongs in a submitted binary.
//
//  The Dynamic Type previews at the bottom are the ones worth keeping. Nothing
//  in this extension scaled with the user's text size until 1.4.0, and the fix
//  is invisible at the default size by design — so the only way to see it work
//  is to put the two sizes side by side, which is exactly what those do.

#if DEBUG
import SwiftUI
import WidgetKit

// ── Sample data ──────────────────────────────────────────────────────────────

extension WidgetHaulData {

    /// A library that exercises every field: four finds, a live streak, a
    /// profitable month, and a free tier with one scan left.
    ///
    /// Deliberately not round numbers. `$3,480–$6,200` is wide enough to test
    /// the compact money path, and `totalLikely` sits between the two rather
    /// than at either end, which is the thing the circular complication was
    /// getting wrong before 1.4.0.
    static let previewFree = WidgetHaulData(
        totalLow: 3_480, totalHigh: 6_200, itemCount: 41,
        lastItemName: "Patagonia Better Sweater",
        lastItemRange: "$60–$95",
        updatedAt: Date(timeIntervalSince1970: 1_789_000_000),
        freeScansRemaining: 1,
        isPro: false,
        streak: 6,
        recentFinds: [
            WidgetFind(id: "1", name: "Patagonia Better Sweater", range: "$60–$95"),
            WidgetFind(id: "2", name: "Levi's 501 Vintage",       range: "$40–$70"),
            WidgetFind(id: "3", name: "Le Creuset Dutch Oven",    range: "$180–$260"),
            WidgetFind(id: "4", name: "Dr. Martens 1460",         range: "$55–$90"),
        ],
        monthProfit: 214.50,
        monthFlips: 6,
        streakLastScan: Date(timeIntervalSince1970: 1_789_000_000),
        freeScanAllowance: 1,
        monthSold: 7,
        totalLikely: 4_840
    )

    /// The same library, subscribed. Pro changes the streak/allowance
    /// presentation; since 1.4.0 it no longer changes the month ledger, which
    /// is the thing to check on the Profit widget.
    static var previewPro: WidgetHaulData {
        var haul = previewFree
        haul.isPro = true
        haul.freeScansRemaining = nil
        return haul
    }

    /// Free tier with the allowance spent — the upsell state.
    static var previewSpent: WidgetHaulData {
        var haul = previewFree
        haul.freeScansRemaining = 0
        return haul
    }

    /// A month with sales nobody entered a paid price for. `monthProfit` is nil
    /// while `monthSold` is not, which is the case that used to read "No flips
    /// sold yet this month" beside a Flips screen showing sales.
    static var previewUnpricedMonth: WidgetHaulData {
        var haul = previewPro
        haul.monthProfit = nil
        haul.monthFlips = 0
        haul.monthSold = 3
        return haul
    }

    /// Nothing scanned. Every widget has an empty state and they are easy to
    /// forget about.
    static let previewEmpty = WidgetHaulData.empty

    /// A blob written by 1.3.x: no `totalLikely`, no `recentFinds`. The
    /// extension reads one of these after every app update, until the app next
    /// runs — so it is an ordinary state, not a corner case.
    static let previewLegacyBlob = WidgetHaulData(
        totalLow: 348, totalHigh: 620, itemCount: 8,
        lastItemName: "Patagonia Fleece",
        lastItemRange: "$60–$95",
        updatedAt: Date(timeIntervalSince1970: 1_789_000_000),
        freeScansRemaining: nil, isPro: false, streak: 0,
        recentFinds: [], monthProfit: nil, monthFlips: 0
    )
}

/// A fixed instant, so previews do not drift with the wall clock. Inside the
/// same month as the sample data, which the Profit widget's header depends on.
private let previewNow = Date(timeIntervalSince1970: 1_789_000_000)

// ── Home Screen ──────────────────────────────────────────────────────────────

#Preview("Haul · small", as: .systemSmall) {
    HaulWidget()
} timeline: {
    HaulEntry(date: previewNow, haul: .previewFree)
    HaulEntry(date: previewNow, haul: .previewEmpty)
}

#Preview("Haul · medium", as: .systemMedium) {
    HaulWidget()
} timeline: {
    HaulEntry(date: previewNow, haul: .previewFree)
    HaulEntry(date: previewNow, haul: .previewEmpty)
}

#Preview("Recent finds · medium", as: .systemMedium) {
    RecentFindsWidget()
} timeline: {
    HaulOnlyEntry(date: previewNow, haul: .previewFree)
    // The header figure is the whole library; the rows are the last two. The
    // word "Haul" in front of it is what stops it reading as their sum.
    HaulOnlyEntry(date: previewNow, haul: .previewLegacyBlob)
    HaulOnlyEntry(date: previewNow, haul: .previewEmpty)
}

#Preview("Recent finds · large", as: .systemLarge) {
    RecentFindsWidget()
} timeline: {
    HaulOnlyEntry(date: previewNow, haul: .previewFree)
}

#Preview("Scans left", as: .systemSmall) {
    ScansLeftWidget()
} timeline: {
    HaulOnlyEntry(date: previewNow, haul: .previewFree)
    HaulOnlyEntry(date: previewNow, haul: .previewSpent)
    HaulOnlyEntry(date: previewNow, haul: .previewPro)
}

#Preview("Profit this month", as: .systemSmall) {
    MonthProfitWidget()
} timeline: {
    // Free first, deliberately: before 1.4.0 this read "Pro" / "Track profit
    // with Pro" while the Flips tab showed the same user the same number.
    HaulOnlyEntry(date: previewNow, haul: .previewFree)
    HaulOnlyEntry(date: previewNow, haul: .previewPro)
    HaulOnlyEntry(date: previewNow, haul: .previewUnpricedMonth)
    HaulOnlyEntry(date: previewNow, haul: .previewEmpty)
}

#Preview("Quick Scan", as: .systemSmall) {
    QuickScanWidget()
} timeline: {
    QuickScanEntry(date: previewNow, itemCount: 41)
    QuickScanEntry(date: previewNow, itemCount: 1)
    QuickScanEntry(date: previewNow, itemCount: 0)
}

// ── Lock Screen ──────────────────────────────────────────────────────────────

#Preview("Lock Screen · circular", as: .accessoryCircular) {
    LockScreenHaulWidget()
} timeline: {
    // Since 1.4.0 this is the midpoint of the range, not its top: $4,840, not
    // $6,200. It should agree with "Your finds are worth" in the app.
    LockScreenEntry(date: previewNow, haul: .previewFree)
    // A 1.3.x blob carries no midpoint, so it falls back to the top.
    LockScreenEntry(date: previewNow, haul: .previewLegacyBlob)
    LockScreenEntry(date: previewNow, haul: .previewEmpty)
}

#Preview("Lock Screen · rectangular", as: .accessoryRectangular) {
    LockScreenHaulWidget()
} timeline: {
    LockScreenEntry(date: previewNow, haul: .previewFree)
    LockScreenEntry(date: previewNow, haul: .previewEmpty)
}

#Preview("Lock Screen · inline", as: .accessoryInline) {
    LockScreenHaulWidget()
} timeline: {
    LockScreenEntry(date: previewNow, haul: .previewFree)
}

#Preview("Scans left · circular", as: .accessoryCircular) {
    ScansLeftWidget()
} timeline: {
    HaulOnlyEntry(date: previewNow, haul: .previewFree)
    HaulOnlyEntry(date: previewNow, haul: .previewSpent)
}

// ── Dynamic Type ─────────────────────────────────────────────────────────────
//
// The point of these: at `.large` (the default) every widget must look exactly
// as it did before 1.4.0, and at `.accessibility3` the text must grow without
// clipping. Both halves matter — the first is the promise that this change is
// invisible to anyone who has not turned Larger Text on.
//
// These preview the *views* rather than the widgets, because a `#Preview(as:)`
// has nowhere to put an environment override.

private struct TypeSizeRow<Content: View>: View {
    let label: String
    let width: CGFloat
    let height: CGFloat
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(.secondary)
            HStack(alignment: .top, spacing: 16) {
                ForEach([DynamicTypeSize.large, .accessibility3], id: \.self) { size in
                    VStack(spacing: 4) {
                        content()
                            .frame(width: width, height: height)
                            .background(Color.wCharcoal)
                            .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
                            .environment(\.dynamicTypeSize, size)
                        Text(size == .large ? "default" : "AX3")
                            .font(.system(size: 9))
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
        .padding()
    }
}

#Preview("Dynamic Type · Recent finds") {
    TypeSizeRow(label: "Recent finds — medium", width: 329, height: 155) {
        RecentFindsView(haul: .previewFree)
            .padding(16)
    }
}

#Preview("Dynamic Type · Profit this month") {
    TypeSizeRow(label: "Profit this month — small", width: 155, height: 155) {
        MonthProfitView(haul: .previewFree, now: previewNow)
            .padding(16)
    }
}

#Preview("Dynamic Type · Scans left") {
    TypeSizeRow(label: "Scans left — small", width: 155, height: 155) {
        ScansLeftView(haul: .previewFree, now: previewNow)
            .padding(16)
    }
}

#Preview("Dynamic Type · Haul") {
    TypeSizeRow(label: "Haul — small", width: 155, height: 155) {
        HaulWidgetSmallView(haul: .previewFree)
    }
}

#Preview("Dynamic Type · Lock Screen rectangular") {
    TypeSizeRow(label: "Lock Screen — rectangular", width: 160, height: 72) {
        LockScreenRectangularView(haul: .previewFree, now: previewNow)
            .padding(8)
    }
}
#endif
