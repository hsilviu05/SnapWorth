import SwiftUI
import SwiftData

/// "My Flips" — the reseller's profit ledger. Computed live from the existing
/// scan store. Free tier sees the current month + last N sold; Pro sees all-time
/// totals, full history and CSV export.
struct FlipsView: View {
    let purchaseService: any PurchaseService

    @Environment(\.displayScale) private var displayScale
    @Query private var allResults: [ScanResult]
    @State private var vm = FlipsViewModel()

    @State private var selectedItem: ScanResult?
    @State private var showPaywall = false
    @State private var paywallTrigger: PaywallTrigger = .ledgerHistory
    @State private var shareItems: [Any]?
    @State private var shareOnComplete: ((String?) -> Void)?
    @State private var showShare = false

    private var isPro: Bool { purchaseService.isSubscribed }
    private var scope: FlipsViewModel.Scope { isPro ? .allTime : .month }
    private var tracked: [ScanResult] { vm.trackedItems(allResults) }

    var body: some View {
        NavigationStack {
            Group {
                if tracked.isEmpty {
                    emptyState
                } else {
                    ledger
                }
            }
            .background(Color.snapBackground)
            .navigationTitle("My Flips")
            .navigationBarTitleDisplayMode(.large)
            .toolbar { toolbarContent }
        }
        .onAppear { Analytics.shared.track(.ledgerDashboardViewed) }
        .sheet(item: $selectedItem) { item in
            ResultView(result: item, onDismiss: { selectedItem = nil })
        }
        .sheet(isPresented: $showPaywall) {
            PaywallView(purchaseService: purchaseService, trigger: paywallTrigger)
        }
        .sheet(isPresented: $showShare) {
            if let items = shareItems {
                ActivityShareSheet(items: items, onComplete: shareOnComplete)
            }
        }
    }

    // MARK: - Ledger

    private var ledger: some View {
        let summary = vm.summary(allResults, scope: scope)
        let items = vm.visibleItems(allResults)
        let capped = isPro ? items : Array(items.prefix(Config.ledgerFreeSoldCap))
        let hidden = items.count - capped.count

        return ScrollView {
            VStack(spacing: 16) {
                summaryHeader(summary)
                statsGrid(summary)
                monthlyCard
                filterChips

                LazyVStack(spacing: 10) {
                    ForEach(capped) { itemRow($0) }
                }

                if hidden > 0 {
                    unlockRow(hidden: hidden)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
            .padding(.bottom, 32)
        }
        .scrollIndicators(.hidden)
    }

    // MARK: - Summary header

    private func summaryHeader(_ s: FlipsViewModel.Summary) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(isPro ? "All-time profit" : "Profit this month")
                .font(.snapCaption)
                .foregroundStyle(Color.snapSage.opacity(0.85))

            Text(vm.signedMoney(s.realizedProfit))
                .font(.fraunces(38, weight: .bold))
                .foregroundStyle(s.realizedProfit < 0 ? Color.snapTerracotta : Color.snapSage)
                .lineLimit(1)
                .minimumScaleFactor(0.5)

            Text("\(s.itemsSold) item\(s.itemsSold == 1 ? "" : "s") sold")
                .font(.snapCaption)
                .foregroundStyle(Color.snapSage.opacity(0.7))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(20)
        .background(Color.snapSage.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .strokeBorder(Color.snapSage.opacity(0.2), lineWidth: 1)
        )
    }

    // MARK: - Stats grid

    private func statsGrid(_ s: FlipsViewModel.Summary) -> some View {
        let cols = [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)]
        return LazyVGrid(columns: cols, spacing: 12) {
            statCard(title: "Invested", value: vm.money(s.totalInvested))
            statCard(title: "Avg ROI", value: s.averageROI.map { vm.roiPercent($0) } ?? "—")
            statCard(
                title: "Best flip",
                value: s.bestFlip?.realizedProfit.map { vm.signedMoney($0) } ?? "—",
                caption: s.bestFlip?.itemName
            )
            statCard(
                title: "Not sold yet",
                value: "\(s.unrealizedCount)",
                caption: s.unrealizedCount > 0 ? "\(vm.money(s.unrealizedInvested)) in" : nil
            )
        }
    }

    private func statCard(title: String, value: String, caption: String? = nil) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)
            Text(value)
                .font(.fraunces(22, weight: .bold))
                .foregroundStyle(Color.snapEspresso)
                .lineLimit(1)
                .minimumScaleFactor(0.55)
            if let caption {
                Text(caption)
                    .font(.dmSans(11))
                    .foregroundStyle(Color.snapWarmGray)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 86, alignment: .topLeading)
        .padding(14)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    // MARK: - Monthly bars

    private var monthlyCard: some View {
        let buckets = vm.monthlyBuckets(allResults)
        let maxVal = buckets
            .map { NSDecimalNumber(decimal: $0.profit).doubleValue }
            .map { Swift.max($0, 0) }
            .max() ?? 0

        return VStack(alignment: .leading, spacing: 12) {
            Text("Last 6 months")
                .snapSectionHeader()

            VStack(spacing: 10) {
                ForEach(buckets) { bucket in
                    monthRow(bucket, maxVal: maxVal)
                }
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
    }

    private func monthRow(_ bucket: FlipsViewModel.MonthBucket, maxVal: Double) -> some View {
        let val = NSDecimalNumber(decimal: bucket.profit).doubleValue
        let frac = maxVal > 0 ? Swift.max(0, val) / maxVal : 0
        return HStack(spacing: 12) {
            Text(bucket.label)
                .font(.dmSans(13, weight: .medium))
                .foregroundStyle(Color.snapWarmGray)
                .frame(width: 40, alignment: .leading)

            GeometryReader { geo in
                Capsule()
                    .fill(val < 0 ? Color.snapTerracotta.opacity(0.55) : Color.snapSage)
                    .frame(width: Swift.max(4, geo.size.width * frac))
                    .frame(maxHeight: .infinity, alignment: .leading)
            }
            .frame(height: 12)

            Text(vm.signedMoney(bucket.profit))
                .font(.dmSans(12, weight: .semibold))
                .foregroundStyle(val < 0 ? Color.snapTerracotta : Color.snapEspresso)
                .frame(width: 74, alignment: .trailing)
        }
    }

    // MARK: - Filter chips

    private var filterChips: some View {
        HStack(spacing: 8) {
            ForEach(FlipsViewModel.StatusFilter.allCases) { f in
                let selected = vm.filter == f
                Button { vm.filter = f } label: {
                    Text(f.rawValue)
                        .font(.dmSans(13, weight: .semibold))
                        .foregroundStyle(selected ? Color.snapBackground : Color.snapWarmGray)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 7)
                        .background(selected ? Color.snapEspresso : Color.clear)
                        .clipShape(Capsule())
                        .overlay(Capsule().strokeBorder(Color.snapBorder, lineWidth: selected ? 0 : 1))
                }
                .buttonStyle(.plain)
            }
            Spacer()
        }
    }

    // MARK: - Item row

    private func itemRow(_ item: ScanResult) -> some View {
        Button { selectedItem = item } label: {
            HStack(spacing: 12) {
                thumbnail(item)

                VStack(alignment: .leading, spacing: 4) {
                    Text(item.itemName)
                        .font(.dmSans(14, weight: .semibold))
                        .foregroundStyle(Color.snapEspresso)
                        .lineLimit(1)
                    statusBadge(item.status)
                }

                Spacer()

                trailingValue(item)
            }
            .padding(12)
            .background(Color.snapCard)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private func thumbnail(_ item: ScanResult) -> some View {
        if let data = item.imageData, let img = UIImage(data: data) {
            Image(uiImage: img)
                .resizable()
                .scaledToFill()
                .frame(width: 46, height: 46)
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        } else {
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(Color.snapBorder)
                .frame(width: 46, height: 46)
                .overlay(Image(systemName: "bag").foregroundStyle(Color.snapWarmGray))
        }
    }

    private func statusBadge(_ status: FlipStatus) -> some View {
        HStack(spacing: 4) {
            Image(systemName: status.systemImage).font(.system(size: 9, weight: .semibold))
            Text(status.label).font(.dmSans(10, weight: .semibold))
        }
        .foregroundStyle(Color.snapWarmGray)
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background(Color.snapBackground)
        .clipShape(Capsule())
    }

    @ViewBuilder
    private func trailingValue(_ item: ScanResult) -> some View {
        if item.status == .sold {
            if let profit = item.realizedProfit {
                Text(vm.signedMoney(profit))
                    .font(.dmSans(15, weight: .bold))
                    .foregroundStyle(profit < 0 ? Color.snapTerracotta : Color.snapSage)
            } else {
                Text("—")
                    .font(.dmSans(15, weight: .bold))
                    .foregroundStyle(Color.snapWarmGray)
            }
        } else if let paid = item.paidPrice {
            VStack(alignment: .trailing, spacing: 1) {
                Text(vm.money(Decimal(paid)))
                    .font(.dmSans(14, weight: .semibold))
                    .foregroundStyle(Color.snapEspresso)
                Text("paid").font(.dmSans(10)).foregroundStyle(Color.snapWarmGray)
            }
        }
    }

    // MARK: - Free-tier unlock

    private func unlockRow(hidden: Int) -> some View {
        Button { routeToPaywall(.ledgerHistory) } label: {
            HStack(spacing: 10) {
                Image(systemName: "lock.fill").font(.system(size: 14, weight: .semibold))
                Text("Unlock \(hidden) more + all-time totals")
                    .font(.dmSans(14, weight: .semibold))
                Spacer()
                Image(systemName: "chevron.right").font(.system(size: 12, weight: .semibold))
            }
            .foregroundStyle(Color.snapBackground)
            .padding(16)
            .background(Color.snapTerracotta)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
        .buttonStyle(.plain)
    }

    // MARK: - Empty state

    private var emptyState: some View {
        VStack(spacing: 16) {
            Image(systemName: "chart.line.uptrend.xyaxis")
                .font(.system(size: 48, weight: .light))
                .foregroundStyle(Color.snapBorder)
            Text("No flips yet")
                .font(.fraunces(20, weight: .bold))
                .foregroundStyle(Color.snapEspresso)
            Text("Scan an item, then mark it Owned or Sold\nto start tracking your profit here.")
                .font(.snapBody)
                .foregroundStyle(Color.snapWarmGray)
                .multilineTextAlignment(.center)
            Button("Start scanning") {
                NotificationCenter.default.post(name: .snapSwitchToScan, object: nil)
            }
            .font(.dmSans(15, weight: .semibold))
            .foregroundStyle(Color.snapBackground)
            .padding(.horizontal, 28)
            .padding(.vertical, 12)
            .background(Color.snapTerracotta)
            .clipShape(Capsule())
            .padding(.top, 4)
        }
        .padding(.horizontal, 40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - Toolbar

    @ToolbarContentBuilder
    private var toolbarContent: some ToolbarContent {
        if !tracked.isEmpty {
            ToolbarItem(placement: .navigationBarTrailing) {
                Menu {
                    Picker("Sort", selection: $vm.sort) {
                        ForEach(FlipsViewModel.SortOrder.allCases) { Text($0.rawValue).tag($0) }
                    }
                    if vm.hasSalesThisMonth(allResults) {
                        Button { shareMonth() } label: {
                            Label("Share this month", systemImage: "square.and.arrow.up")
                        }
                    }
                    Button { exportCSV() } label: {
                        Label("Export CSV", systemImage: "tablecells")
                    }
                } label: {
                    Image(systemName: "ellipsis.circle").foregroundStyle(Color.snapTerracotta)
                }
            }
        }
    }

    // MARK: - Actions

    private func exportCSV() {
        Analytics.shared.track(.ledgerExportTapped)
        guard isPro else { routeToPaywall(.ledgerExport); return }
        guard let url = vm.csvFileURL(allResults) else { return }
        shareOnComplete = nil
        shareItems = [url]
        showShare = true
    }

    private func shareMonth() {
        guard let card = vm.renderMonthCard(allResults, displayScale: displayScale) else { return }
        shareOnComplete = { _ in Analytics.shared.track(.ledgerMonthShared) }
        shareItems = [card]
        showShare = true
    }

    private func routeToPaywall(_ trigger: PaywallTrigger) {
        Analytics.shared.track(.ledgerPaywallHit(trigger: trigger))
        paywallTrigger = trigger
        showPaywall = true
    }
}

// MARK: - Previews

@MainActor
private func previewContainer(_ build: (ModelContext) -> Void) -> ModelContainer {
    let config = ModelConfiguration(isStoredInMemoryOnly: true)
    let container = try! ModelContainer(for: ScanResult.self, configurations: config)
    build(container.mainContext)
    return container
}

private func sample(
    _ name: String, category: String = "clothing",
    paid: Double?, sold: Double? = nil, fees: Double? = nil,
    status: FlipStatus, soldDaysAgo: Int? = nil
) -> ScanResult {
    let r = ScanResult(
        itemName: name, brand: "Brand", category: category,
        conditionNotes: "Good", valueLow: 40, valueHigh: 90,
        confidence: "high", soldListingsCount: 20,
        listingTitle: "", listingDescription: "",
        paidPrice: paid, statusRaw: status.rawValue,
        soldPrice: sold, feesEstimate: fees
    )
    if let soldDaysAgo { r.soldDate = Calendar.current.date(byAdding: .day, value: -soldDaysAgo, to: Date()) }
    return r
}

#Preview("Rich seller (Pro)") {
    FlipsView(purchaseService: MockPurchaseService(forcedSubscribed: true))
        .modelContainer(previewContainer { ctx in
            ctx.insert(sample("Patagonia Better Sweater", paid: 8, sold: 65, fees: 9, status: .sold, soldDaysAgo: 3))
            ctx.insert(sample("Nike Air Max 90", category: "shoes", paid: 20, sold: 110, fees: 14, status: .sold, soldDaysAgo: 20))
            ctx.insert(sample("Coach Crossbody Bag", category: "bags", paid: 15, sold: 95, fees: 12, status: .sold, soldDaysAgo: 48))
            ctx.insert(sample("Levi's 501", paid: 6, sold: 42, fees: 6, status: .sold, soldDaysAgo: 75))
            ctx.insert(sample("Vintage Denim Jacket", paid: 12, status: .listed))
            ctx.insert(sample("Le Creuset Dutch Oven", category: "home", paid: 25, status: .owned))
        })
}

#Preview("One sold item (Free)") {
    FlipsView(purchaseService: MockPurchaseService(forcedSubscribed: false))
        .modelContainer(previewContainer { ctx in
            ctx.insert(sample("Patagonia Better Sweater", paid: 8, sold: 65, fees: 9, status: .sold, soldDaysAgo: 2))
        })
}

#Preview("All owned, none sold") {
    FlipsView(purchaseService: MockPurchaseService(forcedSubscribed: true))
        .modelContainer(previewContainer { ctx in
            ctx.insert(sample("Vintage Denim Jacket", paid: 12, status: .owned))
            ctx.insert(sample("Nike Windbreaker", paid: 18, status: .listed))
            ctx.insert(sample("Ceramic Vase", category: "home", paid: 9, status: .owned))
        })
}

#Preview("Loss-making flip") {
    FlipsView(purchaseService: MockPurchaseService(forcedSubscribed: true))
        .modelContainer(previewContainer { ctx in
            ctx.insert(sample("Overpaid Sneakers", category: "shoes", paid: 120, sold: 80, fees: 12, status: .sold, soldDaysAgo: 5))
            ctx.insert(sample("Mystery Sold (no cost basis)", paid: nil, sold: 40, status: .sold, soldDaysAgo: 10))
        })
}

#Preview("Empty state") {
    FlipsView(purchaseService: MockPurchaseService(forcedSubscribed: false))
        .modelContainer(previewContainer { _ in })
}
