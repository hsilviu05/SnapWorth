import SwiftUI

@MainActor
@Observable
final class ResultViewModel {
    var didCopyListing: Bool = false
    var shareCard: UIImage?

    @ObservationIgnored
    private var resetTask: Task<Void, Never>?

    deinit { resetTask?.cancel() }

    func prepareShareCard(result: ScanResult, photo: UIImage?, displayScale: CGFloat) {
        let view = ShareCardView(result: result, photo: photo)
        let renderer = ImageRenderer(content: view)
        renderer.scale = max(displayScale, 2)
        shareCard = renderer.uiImage
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
