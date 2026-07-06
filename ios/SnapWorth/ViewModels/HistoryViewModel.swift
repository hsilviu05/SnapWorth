import SwiftUI
import SwiftData

enum HistorySortOrder: String, CaseIterable {
    case newest = "Newest"
    case mostValuable = "Most Valuable"
}

@Observable
final class HistoryViewModel {
    var searchText: String = ""
    var sortOrder: HistorySortOrder = .newest
    var deleteError: String?

    func sorted(_ results: [ScanResult]) -> [ScanResult] {
        switch sortOrder {
        case .newest:
            return results.sorted { $0.timestamp > $1.timestamp }
        case .mostValuable:
            return results.sorted { $0.midpointValue > $1.midpointValue }
        }
    }

    func filtered(_ results: [ScanResult]) -> [ScanResult] {
        let sorted = sorted(results)
        guard !searchText.isEmpty else { return sorted }
        return sorted.filter {
            $0.itemName.localizedCaseInsensitiveContains(searchText) ||
            $0.brand.localizedCaseInsensitiveContains(searchText)
        }
    }

    func totalValue(from results: [ScanResult]) -> String {
        let total = results.reduce(0) { $0 + $1.midpointValue }
        return NumberFormatter.snapCurrency.string(from: NSNumber(value: total)) ?? "$\(Int(total))"
    }

    func delete(_ result: ScanResult, context: ModelContext) {
        context.delete(result)
        do { try context.save() } catch {
            deleteError = "Could not delete item. Please try again."
        }
    }
}
