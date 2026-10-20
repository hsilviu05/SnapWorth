import SwiftUI
import UIKit

/// Thin wrapper over `UIActivityViewController` so we can share a rendered card
/// image *and* learn which activity the user completed — SwiftUI's `ShareLink`
/// exposes neither a completion nor the chosen activity, which the
/// `share_card_shared { activity_type }` event needs.
struct ActivityShareSheet: UIViewControllerRepresentable {
    let items: [Any]
    /// Called only when the share actually completes; `activityType` is the raw
    /// UIActivity type (e.g. "com.apple.UIKit.activity.PostToFacebook") or nil.
    var onComplete: ((String?) -> Void)? = nil

    func makeUIViewController(context: Context) -> UIActivityViewController {
        let vc = UIActivityViewController(activityItems: items, applicationActivities: nil)
        vc.completionWithItemsHandler = { activityType, completed, _, _ in
            guard completed else { return }
            onComplete?(activityType?.rawValue)
        }
        return vc
    }

    func updateUIViewController(_ vc: UIActivityViewController, context: Context) {}
}
