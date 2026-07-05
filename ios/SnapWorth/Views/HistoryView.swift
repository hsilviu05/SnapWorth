import SwiftUI
import SwiftData

struct HistoryView: View {
    @Environment(\.modelContext) private var modelContext
    @Query(sort: \ScanResult.timestamp, order: .reverse) private var results: [ScanResult]
    @State private var vm = HistoryViewModel()
    @State private var selectedResult: ScanResult?

    private let columns = [
        GridItem(.flexible(), spacing: 12),
        GridItem(.flexible(), spacing: 12),
    ]

    var filteredResults: [ScanResult] {
        guard !vm.searchText.isEmpty else { return results }
        return results.filter {
            $0.itemName.localizedCaseInsensitiveContains(vm.searchText) ||
            $0.brand.localizedCaseInsensitiveContains(vm.searchText)
        }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {

                    // ── Total banner ───────────────────────────────────────
                    if !results.isEmpty {
                        TotalBanner(totalValue: vm.totalValue(from: results), count: results.count)
                            .padding(.horizontal, 20)
                    }

                    // ── Search ─────────────────────────────────────────────
                    if !results.isEmpty {
                        HStack {
                            Image(systemName: "magnifyingglass")
                                .foregroundStyle(Color.snapWarmGray)
                            TextField("Search finds…", text: $vm.searchText)
                                .font(.snapBody)
                            if !vm.searchText.isEmpty {
                                Button {
                                    vm.searchText = ""
                                } label: {
                                    Image(systemName: "xmark.circle.fill")
                                        .foregroundStyle(Color.snapWarmGray)
                                }
                            }
                        }
                        .padding(12)
                        .background(Color.snapCard)
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .padding(.horizontal, 20)
                    }

                    // ── Grid ───────────────────────────────────────────────
                    if results.isEmpty {
                        EmptyFindsView()
                            .padding(.top, 60)
                    } else if filteredResults.isEmpty {
                        NoSearchResultsView(query: vm.searchText)
                            .padding(.top, 60)
                    } else {
                        LazyVGrid(columns: columns, spacing: 12) {
                            ForEach(filteredResults) { result in
                                ScanHistoryCard(result: result)
                                    .onTapGesture { selectedResult = result }
                                    .contextMenu {
                                        Button(role: .destructive) {
                                            vm.delete(result, context: modelContext)
                                        } label: {
                                            Label("Delete", systemImage: "trash")
                                        }
                                    }
                            }
                        }
                        .padding(.horizontal, 20)
                    }
                }
                .padding(.top, 8)
                .padding(.bottom, 32)
            }
            .background(Color.snapBackground)
            .navigationTitle("My Finds")
            .navigationBarTitleDisplayMode(.large)
        }
        .sheet(item: $selectedResult) { result in
            ResultView(result: result, onDismiss: { selectedResult = nil })
        }
    }
}

// MARK: - Total Banner
private struct TotalBanner: View {
    let totalValue: String
    let count: Int

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
}

// MARK: - No search results
private struct NoSearchResultsView: View {
    let query: String

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 48, weight: .light))
                .foregroundStyle(Color.snapBorder)

            Text("No results for \"\(query)\"")
                .font(.fraunces(20, weight: .bold))
                .foregroundStyle(Color.snapEspresso)

            Text("Try a different item name or brand.")
                .font(.snapBody)
                .foregroundStyle(Color.snapWarmGray)
                .multilineTextAlignment(.center)
        }
    }
}

// MARK: - Empty state
private struct EmptyFindsView: View {
    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "bag")
                .font(.system(size: 48, weight: .light))
                .foregroundStyle(Color.snapBorder)

            Text("No finds yet")
                .font(.fraunces(20, weight: .bold))
                .foregroundStyle(Color.snapEspresso)

            Text("Scan your first thrift item\nto see its resale value here.")
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
    }
}
