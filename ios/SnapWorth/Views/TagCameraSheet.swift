import PhotosUI
import SwiftData
import SwiftUI

// ═══════════════════════════════════════════════════════════════════
// MARK: - Tag camera (#88)
// ═══════════════════════════════════════════════════════════════════

/// A camera for one close-up of a label, with a guide shaped like a care tag.
///
/// Its own `CameraManager` rather than the scan tab's: this is presented over
/// a result sheet, and reusing the tab's session would leave the scan camera
/// running behind two layers of presentation.
///
/// The scan screen's states, not just its happy path. This used to show the
/// preview only when access was authorized and otherwise a disabled shutter
/// over black, with nothing to say why — so a user who had denied the camera
/// and scanned from Photos reached a screen that looked broken. It also had
/// no way to use a label photo already in the camera roll, and a failed
/// capture only vibrated.
struct TagCameraSheet: View {
    /// Nil when the user backed out.
    let onCapture: (UIImage?) -> Void

    @StateObject private var camera = CameraManager()
    @State private var pickedItem: PhotosPickerItem?
    @State private var pickFailed = false
    @State private var delivered = false

    /// The guide and shutter only mean something while there is, or may soon
    /// be, a viewfinder behind them.
    private var cameraUsable: Bool {
        camera.authStatus == .authorized || camera.authStatus == .notDetermined
    }

    /// Non-nil from the pick until its load finishes, which for an iCloud-only
    /// photo can be seconds.
    private var isLoadingPick: Bool { pickedItem != nil }

    /// The one way out of this sheet, and it opens once.
    ///
    /// Cancel, the shutter and a library pick all end the sheet, and each of
    /// the last two is a paid re-scan. A pick that finished loading after
    /// Cancel, or after a shutter capture, used to deliver a second image —
    /// re-scanning an item the user had backed out of, or re-scanning it twice.
    /// Cancelling the load is not enough on its own: the cover's task is only
    /// cancelled when the view disappears, after the dismissal animation.
    private func deliver(_ image: UIImage?) {
        guard !delivered else { return }
        delivered = true
        onCapture(image)
    }

    var body: some View {
        ZStack {
            Color.snapCharcoal.ignoresSafeArea()

            // The same three states as ScanView.
            switch camera.authStatus {
            case .authorized:
                CameraPreview(session: camera.session)
                    .ignoresSafeArea()
            case .notDetermined:
                // The system prompt is on screen, over this.
                EmptyView()
            case .restricted:
                CameraPermissionPlaceholder(restricted: true)
            default:
                CameraPermissionPlaceholder(restricted: false)
            }

            VStack(spacing: 0) {
                HStack {
                    Button("Cancel") { deliver(nil) }
                        .font(.dmSans(15, weight: .semibold))
                        .foregroundStyle(Color.snapOnCharcoal)
                        .snapHitTarget()
                    Spacer()
                }
                .padding(.horizontal, 20)
                .padding(.top, 12)

                Spacer()

                if cameraUsable {
                    // A tag is wider than it is tall and sits close to the lens;
                    // the guide says "fill this" without a paragraph of copy.
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .strokeBorder(Color.snapOnCharcoal.opacity(0.6), lineWidth: 2)
                        .frame(width: 300, height: 190)
                        .accessibilityHidden(true)

                    Text("Fill the frame with the label")
                        .font(.snapBody)
                        .foregroundStyle(Color.snapOnCharcoal)
                        .padding(.top, 16)
                    Text("Care tag, size label, sole stamp or serial plate. Hold steady — the small print is the point.")
                        .font(.snapCaption)
                        .foregroundStyle(Color.snapOnCharcoal.opacity(0.7))
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 40)
                        .padding(.top, 4)
                }

                Spacer()

                HStack(alignment: .center) {
                    // The label may already be in the camera roll, and with the
                    // camera refused this is the only way in at all.
                    PhotosPicker(selection: $pickedItem, matching: .images) {
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .fill(Color.snapOnCharcoal.opacity(0.2))
                            .frame(width: 52, height: 52)
                            .overlay {
                                // Something has to show while an iCloud photo
                                // downloads, or the pick looks like it did nothing.
                                if isLoadingPick {
                                    ProgressView().tint(Color.snapOnCharcoal)
                                } else {
                                    Image(systemName: "photo.on.rectangle")
                                        .snapSymbol(22, weight: .light)
                                        .foregroundStyle(Color.snapOnCharcoal)
                                }
                            }
                    }
                    .snapHitTarget()
                    .accessibilityLabel("Choose the label photo from your library")

                    Spacer()

                    // Not while a pick is loading: the two would race to be the
                    // tag photo.
                    let canShoot = camera.authStatus == .authorized && !isLoadingPick
                    Button {
                        Haptics.capture()
                        camera.capturePhoto()
                    } label: {
                        ZStack {
                            Circle().fill(Color.snapOnCharcoal).frame(width: 80, height: 80)
                            Circle().strokeBorder(Color.snapOnCharcoal.opacity(0.4), lineWidth: 3)
                                .frame(width: 94, height: 94)
                        }
                    }
                    .disabled(!canShoot)
                    .opacity(canShoot ? 1 : 0.35)
                    .accessibilityLabel("Take the label photo")

                    Spacer()

                    // Balances the library tile, so the shutter stays centred.
                    Color.clear
                        .frame(width: 52, height: 52)
                        .accessibilityHidden(true)
                }
                .padding(.horizontal, 36)
                .padding(.bottom, 44)
            }
        }
        .onAppear {
            Haptics.prepare()
            camera.requestPermissionAndSetup()
        }
        .onDisappear { camera.stopSession() }
        .onChange(of: camera.capturedImage) { _, image in
            guard let image else { return }
            deliver(image)
        }
        // Tied to the pick, so a newer pick or the sheet going away cancels it.
        .task(id: pickedItem) {
            guard let item = pickedItem else { return }
            let data = try? await item.loadTransferable(type: Data.self)
            // Superseded, dismissed, or the sheet already answered: this load
            // has nothing left to say — not even that it failed.
            guard !Task.isCancelled, !delivered else { return }
            if let data, let image = UIImage(data: data) {
                deliver(image)
            } else {
                pickFailed = true
            }
            pickedItem = nil
        }
        // The capture did not arrive: the session was not running, or the
        // photo could not be decoded. ScanView says so in the same words; here
        // it only vibrated.
        .alert("Camera Error", isPresented: Binding(
            get: { camera.error != nil },
            set: { if !$0 { camera.error = nil } }
        )) {
            Button("OK", role: .cancel) { camera.error = nil }
        } message: {
            Text(camera.error?.errorDescription ?? "")
        }
        .alert("Couldn't load the selected photo. Please try another.", isPresented: $pickFailed) {
            Button("OK", role: .cancel) {}
        }
    }
}
