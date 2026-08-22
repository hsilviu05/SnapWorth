import SwiftUI
import SwiftData

struct HistoryView: View {
    let purchaseService: any PurchaseService

    @Environment(\.modelContext) private var modelContext
    @Query private var results: [ScanResult]
    @State private var showPaywall = false
    @State private var vm = HistoryViewModel()
    @State private var selectedResult: ScanResult?
    @State private var isEditing = false
    @State private var recapLabel: String?
    /// Pending single-item deletion, awaiting confirmation.
    ///
    /// "Delete all" in Settings already confirms. A single find vanishing with
    /// no confirmation and no undo was the inconsistency — deletion is
    /// irreversible here (no soft delete), so it earns the same guard.
    @State private var pendingDelete: ScanResult?

    private let columns = [
        GridItem(.flexible(), spacing: 12),
        GridItem(.flexible(), spacing: 12),
    ]

    var filteredResults: [ScanResult] { vm.filtered(results) }

    private var repository: ScanRepository { ScanRepository(context: modelContext) }

    private let hPad: CGFloat = 16
    private let gridSpacing: CGFloat = 12

    var body: some View {
        NavigationStack {
            GeometryReader { geo in
                let cardWidth = (geo.size.width - hPad * 2 - gridSpacing) / 2
                let fixedColumns = [
                    GridItem(.fixed(cardWidth), spacing: gridSpacing),
                    GridItem(.fixed(cardWidth), spacing: gridSpacing),
                ]

                ScrollView {
                    VStack(spacing: 20) {

                        // ── Recap ready banner (notification fallback) ─────────
                        if let recapLabel {
                            RecapBanner(month: recapLabel) {
                                NotificationManager.shared.markRecapViewed()
                                self.recapLabel = nil
                                NotificationCenter.default.post(name: .snapOpenFlips, object: nil)
                            }
                            .padding(.horizontal, hPad)
                        }

                        // ── Total banner ───────────────────────────────────────
                        if !results.isEmpty {
                            PortfolioBanner(
                                totalValue: vm.totalValue(from: results),
                                count: results.count,
                                trend: vm.trendPoints(from: results),
                                // Same entitlement every other gated feature
                                // reads — StoreKit-verified, refreshed on
                                // launch and on every transaction update.
                                isPro: purchaseService.isSubscribed,
                                onUnlock: {
                                    Analytics.shared.track(
                                        .paywallViewed(trigger: .portfolioTrend))
                                    showPaywall = true
                                }
                            )
                            .padding(.horizontal, hPad)
                        }

                        // ── Search ─────────────────────────────────────────────
                        if !results.isEmpty {
                            HStack {
                                Image(systemName: "magnifyingglass")
                                    .snapSymbol(15)
                                    .foregroundStyle(Color.snapWarmGray)
                                    .accessibilityHidden(true)
                                TextField("Search finds…", text: $vm.searchText)
                                    .font(.snapBody)
                                    .accessibilityLabel("Search finds")
                                    .accessibilityHint("Filters by item name or brand")
                                if !vm.searchText.isEmpty {
                                    Button {
                                        vm.searchText = ""
                                    } label: {
                                        Image(systemName: "xmark.circle.fill")
                                            .snapSymbol(15)
                                            .foregroundStyle(Color.snapWarmGray)
                                    }
                                    .snapHitTarget()
                                    .accessibilityLabel("Clear search")
                                }
                            }
                            .padding(12)
                            .background(Color.snapCard)
                            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                            .padding(.horizontal, hPad)
                        }

                        // ── Grid ───────────────────────────────────────────────
                        if results.isEmpty {
                            EmptyFindsView()
                                .frame(maxWidth: .infinity)
                                .padding(.top, 60)
                        } else if filteredResults.isEmpty {
                            NoSearchResultsView(query: vm.searchText)
                                .frame(maxWidth: .infinity)
                                .padding(.top, 60)
                        } else {
                            LazyVGrid(columns: fixedColumns, spacing: gridSpacing) {
                                ForEach(filteredResults) { result in
                                    ScanHistoryCard(result: result, width: cardWidth)
                                        .onTapGesture {
                                            guard !isEditing else { return }
                                            selectedResult = result
                                        }
                                        .contextMenu {
                                            Button(role: .destructive) {
                                                pendingDelete = result
                                            } label: {
                                                Label("Delete", systemImage: "trash")
                                            }
                                        }
                                        .overlay(alignment: .topTrailing) {
                                            if isEditing {
                                                Button {
                                                    pendingDelete = result
                                                } label: {
                                                    Image(systemName: "minus.circle.fill")
                                                        .snapSymbol(22)
                                                        .foregroundStyle(.white, Color.red)
                                                        .background(Color.white.clipShape(Circle()))
                                                }
                                                .snapHitTarget()
                                                .offset(x: 8, y: -8)
                                                .transition(.scale.combined(with: .opacity))
                                                .accessibilityLabel("Delete \(result.itemName)")
                                            }
                                        }
                                        // The card already carries a combined
                                        // label (DesignSystem.ScanHistoryCard);
                                        // add the tap semantics and a rotor
                                        // action so delete is reachable without
                                        // entering edit mode or long-pressing.
                                        .accessibilityHint("Opens this find")
                                        .accessibilityAddTraits(.isButton)
                                        .accessibilityAction(named: "Delete") {
                                            pendingDelete = result
                                        }
                                }
                            }
                            .padding(.horizontal, hPad)
                            .snapAnimation(.spring(duration: 0.25), value: isEditing)
                        }
                    }
                    .padding(.top, 8)
                    .padding(.bottom, 32)
                }
            }
            .scrollDismissesKeyboard(.immediately)
            .background(Color.snapBackground)
            .navigationTitle("My Finds")
            .navigationBarTitleDisplayMode(.large)
            .toolbar {
                if !results.isEmpty {
                    ToolbarItem(placement: .navigationBarLeading) {
                        Button(isEditing ? "Done" : "Edit") {
                            withAnimation(.spring(duration: 0.25)) {
                                isEditing.toggle()
                            }
                        }
                        .font(.dmSans(16, weight: isEditing ? .semibold : .regular,
                                      relativeTo: .body))
                        .foregroundStyle(Color.snapTerracotta)
                        .accessibilityLabel(isEditing ? "Done editing" : "Edit finds")
                        .accessibilityHint(isEditing
                            ? "Stops removing finds"
                            : "Lets you remove finds from your closet")
                    }
                    ToolbarItem(placement: .navigationBarTrailing) {
                        Menu {
                            ForEach(HistorySortOrder.allCases, id: \.self) { order in
                                Button {
                                    vm.sortOrder = order
                                } label: {
                                    HStack {
                                        Text(order.rawValue)
                                        if vm.sortOrder == order {
                                            Image(systemName: "checkmark")
                                        }
                                    }
                                }
                                // Menu rows convey the current choice with a
                                // checkmark glyph; mirror it as a trait so
                                // VoiceOver announces "selected".
                                .accessibilityAddTraits(
                                    vm.sortOrder == order ? [.isButton, .isSelected] : .isButton)
                            }
                        } label: {
                            Image(systemName: "arrow.up.arrow.down")
                                .snapSymbol(16)
                                .foregroundStyle(Color.snapTerracotta)
                        }
                        .disabled(isEditing)
                        .opacity(isEditing ? 0.4 : 1)
                        .snapHitTarget()
                        .accessibilityLabel("Sort")
                        .accessibilityValue(vm.sortOrder.rawValue)
                        .accessibilityHint("Changes the order of your finds")
                    }
                }
            }
            .onChange(of: results.count) { _, _ in
                if results.isEmpty { isEditing = false }
            }
            .task { recapLabel = NotificationManager.shared.readyRecapLabel() }
        }
        .sheet(isPresented: $showPaywall) {
            PaywallView(purchaseService: purchaseService, trigger: .portfolioTrend)
        }
        .sheet(item: $selectedResult) { result in
            ResultView(result: result, purchaseService: purchaseService,
                       onDismiss: { selectedResult = nil })
                .presentationDragIndicator(.visible)
        }
        .confirmationDialog(
            "Delete this find?",
            isPresented: Binding(
                get: { pendingDelete != nil },
                set: { if !$0 { pendingDelete = nil } }
            ),
            titleVisibility: .visible,
            presenting: pendingDelete
        ) { result in
            Button("Delete", role: .destructive) {
                Haptics.light()
                withAnimation(.spring(duration: 0.25)) {
                    vm.delete(result, repository: repository)
                }
                pendingDelete = nil
            }
            Button("Cancel", role: .cancel) { pendingDelete = nil }
        } message: { result in
            Text("\(result.itemName) will be permanently removed from your finds.")
        }
        .alert("Delete Failed", isPresented: Binding(
            get: { vm.deleteError != nil },
            set: { if !$0 { vm.deleteError = nil } }
        )) {
            Button("OK", role: .cancel) { vm.deleteError = nil }
        } message: {
            Text(vm.deleteError ?? "")
        }
    }
}

// MARK: - Recap Banner (notification fallback → My Flips)
private struct RecapBanner: View {
    let month: String
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            HStack(spacing: 14) {
                Image(systemName: "chart.bar.doc.horizontal")
                    .snapSymbol(20, weight: .medium)
                    .foregroundStyle(Color.snapTerracotta)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Your \(month) recap is ready")
                        .font(.dmSans(15, weight: .semibold))
                        .foregroundStyle(Color.snapEspresso)
                    Text("See your flips for the month")
                        .font(.snapCaption)
                        .foregroundStyle(Color.snapWarmGray)
                }
                Spacer()
                Image(systemName: "chevron.right")
                    .snapSymbol(13, weight: .semibold)
                    .foregroundStyle(Color.snapWarmGray)
            }
            .padding(16)
            .background(Color.snapTerracotta.opacity(0.10))
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .strokeBorder(Color.snapTerracotta.opacity(0.25), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Your \(month) recap is ready")
        .accessibilityHint("Opens My Flips to see the month's results")
        .accessibilityAddTraits(.isButton)
        // Time-sensitive banner: surface it before the rest of the list.
        .accessibilitySortPriority(100)
    }
}

// MARK: - Portfolio Banner
//
// Free users get the headline total and the item count — that is the hook, and
// gating it would remove the reason to reopen the app at all. The trend over
// time is Pro, shown blurred with an unlock prompt rather than hidden, matching
// the soft paywall in ThriftFlipView.
private struct PortfolioBanner: View {
    let totalValue: String
    let count: Int
    var trend: [HistoryViewModel.TrendPoint] = []
    var isPro: Bool = true
    var onUnlock: () -> Void = {}

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Your finds are worth")
                .font(.snapCaption)
                .foregroundStyle(Color.snapSage.opacity(0.8))

            Text(totalValue)
                .font(.fraunces(36, weight: .bold))
                .foregroundStyle(Color.snapSage)

            Text("\(count) item\(count == 1 ? "" : "s") scanned")
                .font(.snapCaption)
                .foregroundStyle(Color.snapSage.opacity(0.7))

            // Extracted: inlining this pushed the banner past what the
            // SwiftUI type-checker will infer in reasonable time.
            TrendStrip(trend: trend, isPro: isPro, onUnlock: onUnlock)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(20)
        .background(Color.snapSage.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .strokeBorder(Color.snapSage.opacity(0.2), lineWidth: 1)
        )
        // The screen's headline figure: one stop, read before the grid.
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Your finds are worth")
        .accessibilityValue("\(totalValue), from \(count) item\(count == 1 ? "" : "s") scanned")
        .accessibilityAddTraits(.isSummaryElement)
        .accessibilitySortPriority(90)
    }
}

// MARK: - No search results
private struct NoSearchResultsView: View {
    let query: String

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "magnifyingglass")
                .snapSymbol(48, weight: .light)
                .foregroundStyle(Color.snapBorder)
                .accessibilityHidden(true)

            Text("No results for \"\(query)\"")
                .font(.fraunces(20, weight: .bold, relativeTo: .title3))
                .foregroundStyle(Color.snapEspresso)

            Text("Try a different item name or brand.")
                .font(.snapBody)
                .foregroundStyle(Color.snapWarmGray)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Empty state
private struct EmptyFindsView: View {
    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "bag")
                .snapSymbol(48, weight: .light)
                .foregroundStyle(Color.snapBorder)
                .accessibilityHidden(true)

            Text("No finds yet")
                .font(.fraunces(20, weight: .bold, relativeTo: .title3))
                .foregroundStyle(Color.snapEspresso)

            Text("Scan your first thrift item to see its resale value here.")
                .font(.snapBody)
                .foregroundStyle(Color.snapWarmGray)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)

            Button("Start scanning") {
                NotificationCenter.default.post(name: .snapSwitchToScan, object: nil)
            }
            .font(.dmSans(15, weight: .semibold, relativeTo: .subheadline))
            .foregroundStyle(Color.snapOnAccent)
            .padding(.horizontal, 28)
            .padding(.vertical, 12)
            .background(Color.snapTerracotta)
            .clipShape(Capsule())
            .buttonStyle(PressableButtonStyle())
            .snapHitTarget()
            .padding(.top, 4)
            .accessibilityHint("Switches to the camera to scan your first item")
        }
    }
}


// MARK: - Trend strip (Pro)
private struct TrendStrip: View {
    let trend: [HistoryViewModel.TrendPoint]
    let isPro: Bool
    let onUnlock: () -> Void

    var body: some View {
        // Two points minimum: one scan is not a trend.
        if trend.count >= 2 {
            ZStack {
                Sparkline(points: trend)
                    .stroke(Color.snapSage, style: StrokeStyle(
                        lineWidth: 2, lineCap: .round, lineJoin: .round))
                    .frame(height: 44)
                    .padding(.top, 8)
                    .blur(radius: isPro ? 0 : 6)
                    // Hidden from VoiceOver when blurred: announcing a shape
                    // the user cannot see is noise, and the unlock button
                    // carries the actionable information instead.
                    .accessibilityHidden(!isPro)

                if !isPro { unlockButton }
            }
        }
    }

    private var unlockButton: some View {
        Button(action: onUnlock) {
            Text("Unlock value history")
                .font(.snapCaption.weight(.semibold))
                .foregroundStyle(Color.snapSage)
                .padding(.horizontal, 14)
                .padding(.vertical, 7)
                .background(Capsule().fill(Color.snapCard))
                .overlay(Capsule().strokeBorder(
                    Color.snapSage.opacity(0.35), lineWidth: 1))
        }
        .accessibilityLabel("Unlock value history")
        .accessibilityHint("Shows how your portfolio has changed over time. Requires SnapWorth Pro.")
    }
}

// MARK: - Sparkline
//
// Hand-rolled rather than Swift Charts: this is one unlabelled line inside a
// summary card, and Charts brings axes, scales and gesture handling that would
// all have to be turned off again. Fifteen lines of Path is the smaller thing.
private struct Sparkline: Shape {
    let points: [HistoryViewModel.TrendPoint]

    func path(in rect: CGRect) -> Path {
        var path = Path()
        guard points.count >= 2 else { return path }

        let values = points.map { NSDecimalNumber(decimal: $0.total).doubleValue }
        let minV = values.min() ?? 0
        let maxV = values.max() ?? 0
        // A flat portfolio still deserves a line rather than a divide-by-zero:
        // draw it through the middle.
        let span = maxV - minV
        let normalise: (Double) -> Double = { span > 0 ? ($0 - minV) / span : 0.5 }

        for (i, value) in values.enumerated() {
            let x = rect.minX + rect.width * (Double(i) / Double(values.count - 1))
            let y = rect.maxY - rect.height * normalise(value)
            let point = CGPoint(x: x, y: y)
            i == 0 ? path.move(to: point) : path.addLine(to: point)
        }
        return path
    }
}
