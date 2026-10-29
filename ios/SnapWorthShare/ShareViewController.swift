import ImageIO
import Observation
import SwiftUI
import UIKit
import UniformTypeIdentifiers
import UserNotifications

/// "Share → SnapWorth" from Photos, Vinted, eBay or any app with an image
/// (#410). Saves the photo for the app and says how to get to its value: a
/// share extension cannot open its app, so a notification does that when the
/// user allows them, and otherwise the user is told to open SnapWorth.
final class ShareViewController: UIViewController {
    private let model = ShareModel()

    override func viewDidLoad() {
        super.viewDidLoad()
        let host = UIHostingController(rootView: ShareView(model: model) { [weak self] in
            self?.extensionContext?.completeRequest(returningItems: nil)
        })
        addChild(host)
        host.view.frame = view.bounds
        host.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(host.view)
        host.didMove(toParent: self)

        let providers = (extensionContext?.inputItems as? [NSExtensionItem] ?? [])
            .flatMap { $0.attachments ?? [] }
        Task { await model.receive(providers) }
    }
}

@MainActor
@Observable
final class ShareModel {
    enum State: Equatable {
        case saving
        case saved(notifies: Bool)
        case failed
    }

    /// The same edge the app uploads at (`ScanAPIClient.maxUploadEdge`), so
    /// nothing larger than the scan needs is kept.
    nonisolated static let maxEdge = 1568
    private static let notificationID = "snapworth.shared-image"

    var state: State = .saving

    func receive(_ providers: [NSItemProvider]) async {
        guard let provider = providers.first(where: {
            $0.hasItemConformingToTypeIdentifier(UTType.image.identifier)
        }), let jpeg = await Self.loadJPEG(from: provider) else {
            state = .failed
            return
        }
        do {
            try SharedImageInbox.put(jpeg)
        } catch {
            state = .failed
            return
        }
        state = .saved(notifies: await Self.scheduleNotification())
    }

    /// Downscaled while decoding, so a 48 MP photo never sits in memory whole:
    /// a share extension is killed at a fraction of the app's memory.
    nonisolated static func downscaledJPEG(_ data: Data) -> Data? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: maxEdge,
        ]
        guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary)
        else { return nil }
        return UIImage(cgImage: image).jpegData(compressionQuality: 0.85)
    }

    private static func loadJPEG(from provider: NSItemProvider) async -> Data? {
        await withCheckedContinuation { continuation in
            _ = provider.loadDataRepresentation(forTypeIdentifier: UTType.image.identifier) { data, _ in
                continuation.resume(returning: data.flatMap(ShareModel.downscaledJPEG))
            }
        }
    }

    /// True when a notification will take the user to the value.
    private static func scheduleNotification() async -> Bool {
        let center = UNUserNotificationCenter.current()
        switch await center.notificationSettings().authorizationStatus {
        case .authorized, .provisional, .ephemeral: break
        default: return false
        }
        let content = UNMutableNotificationContent()
        content.title = String(localized: "Ready to value")
        content.body = String(localized: "Tap to see what it's worth.")
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: 1, repeats: false)
        do {
            try await center.add(UNNotificationRequest(identifier: notificationID,
                                                       content: content, trigger: trigger))
            return true
        } catch {
            return false
        }
    }
}

private struct ShareView: View {
    let model: ShareModel
    let onDone: () -> Void

    var body: some View {
        VStack(spacing: 16) {
            Spacer()
            switch model.state {
            case .saving:
                ProgressView()
                Text("Saving…")
                    .font(.headline)
            case .saved(let notifies):
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 52))
                    .foregroundStyle(.green)
                    .accessibilityHidden(true)
                Text("Saved to SnapWorth")
                    .font(.title3.bold())
                Group {
                    if notifies {
                        Text("Tap the notification to see what it's worth.")
                    } else {
                        Text("Open SnapWorth to see what it's worth.")
                    }
                }
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            case .failed:
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 52))
                    .foregroundStyle(.orange)
                    .accessibilityHidden(true)
                Text("Couldn't read this image")
                    .font(.title3.bold())
                Text("Try sharing a photo or a screenshot.")
                    .multilineTextAlignment(.center)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button(action: onDone) {
                Text("Done")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(model.state == .saving)
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(uiColor: .systemBackground))
    }
}
