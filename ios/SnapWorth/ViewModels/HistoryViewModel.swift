import SwiftUI
import SwiftData

@Observable
final class HistoryViewModel {
    var searchText: String = ""

    func totalValue(from results: [ScanResult]) -> String {
        let total = results.reduce(0) { $0 + $1.midpointValue }
        return NumberFormatter.snapCurrency.string(from: NSNumber(value: total)) ?? "$\(Int(total))"
    }

    func delete(_ result: ScanResult, context: ModelContext) {
        context.delete(result)
        try? context.save()
    }
}
