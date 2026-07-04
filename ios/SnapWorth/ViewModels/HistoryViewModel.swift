import SwiftUI
import SwiftData

@Observable
final class HistoryViewModel {
    var searchText: String = ""

    func totalValue(from results: [ScanResult]) -> String {
        let total = results.reduce(0) { $0 + $1.midpointValue }
        let fmt = NumberFormatter()
        fmt.numberStyle = .currency
        fmt.maximumFractionDigits = 0
        return fmt.string(from: NSNumber(value: total)) ?? "$\(Int(total))"
    }

    func delete(_ result: ScanResult, context: ModelContext) {
        context.delete(result)
        try? context.save()
    }
}
