import SwiftUI

@Observable
final class ResultViewModel {
    var didCopyListing: Bool = false

    func copyListing(result: ScanResult) {
        let text = """
        \(result.listingTitle)

        \(result.listingDescription)

        Asking: \(result.formattedRange)
        Condition: \(result.conditionNotes)
        """
        UIPasteboard.general.string = text

        withAnimation {
            didCopyListing = true
        }
        // Reset the confirmation after 2 seconds
        Task {
            try? await Task.sleep(for: .seconds(2))
            withAnimation { didCopyListing = false }
        }
    }
}
