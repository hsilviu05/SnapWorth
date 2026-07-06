import SwiftUI

@MainActor
@Observable
final class ResultViewModel {
    var didCopyListing: Bool = false

    @ObservationIgnored
    private var resetTask: Task<Void, Never>?

    func shareText(result: ScanResult) -> String {
        "Found a \(result.itemName) worth \(result.formattedRange)\n\n\(result.listingTitle)\n\(result.listingDescription)\n\nValued with SnapWorth"
    }

    func copyListing(result: ScanResult) {
        let text = """
        \(result.listingTitle)

        \(result.listingDescription)

        Asking: \(result.formattedRange)
        Condition: \(result.conditionNotes)
        """
        UIPasteboard.general.string = text

        withAnimation { didCopyListing = true }

        resetTask?.cancel()
        resetTask = Task {
            try? await Task.sleep(for: .seconds(2))
            guard !Task.isCancelled else { return }
            withAnimation { didCopyListing = false }
        }
    }
}
