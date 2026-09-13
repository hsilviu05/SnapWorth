import AVFoundation
import SwiftUI

// MARK: - Camera Manager

@MainActor
final class CameraManager: NSObject, ObservableObject {
    @Published var capturedImage: UIImage?
    @Published var authStatus: AVAuthorizationStatus = .notDetermined
    @Published var error: CameraError?

    nonisolated(unsafe) let session = AVCaptureSession()
    nonisolated(unsafe) private let photoOutput = AVCapturePhotoOutput()
    private var sessionQueue = DispatchQueue(label: "com.snapworth.camera")
    private var isConfigured = false

    override init() {
        super.init()
        authStatus = AVCaptureDevice.authorizationStatus(for: .video)

        // A session this class did not stop itself was never restarted, so any
        // stop that came from the system was permanent: another client taking
        // the camera, or a mediaserverd reset, left `isRunning` false, the
        // preview frozen on its last frame, and `capturePhoto` returning at
        // its own guard — so every shutter tap after that only vibrated.
        // `onAppear` cannot rescue it because the view never disappeared. The
        // user's only fix was force-quitting the app.
        //
        // Selector-based observers rather than the closure form on purpose:
        // NotificationCenter holds these weakly and drops them when the
        // manager goes away, so there is no token to remove from a `deinit`
        // that cannot touch main-actor state — and a second manager is built
        // for every result sheet that offers a tag photo.
        let center = NotificationCenter.default
        center.addObserver(self,
                           selector: #selector(sessionInterruptionEnded(_:)),
                           name: AVCaptureSession.interruptionEndedNotification,
                           object: session)
        center.addObserver(self,
                           selector: #selector(sessionRuntimeError(_:)),
                           name: AVCaptureSession.runtimeErrorNotification,
                           object: session)
    }

    func requestPermissionAndSetup() {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            setupSessionIfNeeded()
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
                Task { @MainActor [weak self] in
                    self?.authStatus = granted ? .authorized : .denied
                    if granted { self?.setupSessionIfNeeded() }
                }
            }
        case .denied, .restricted:
            authStatus = .denied
        @unknown default:
            break
        }
    }

    private func setupSessionIfNeeded() {
        guard !isConfigured else {
            startSession()
            return
        }
        setupSession()
    }

    private func setupSession() {
        // Claimed here, on the actor that reads it in `setupSessionIfNeeded`,
        // instead of after the session queue finishes. `startRunning` below
        // blocks that queue for a few hundred milliseconds on real hardware,
        // and ScanView calls `requestPermissionAndSetup` from `onAppear` on a
        // TabView tab — so a tab switch inside that window saw
        // `isConfigured == false` and enqueued a second full configuration
        // pass. A plain `AVCaptureSession` cannot hold two video inputs (that
        // needs `AVCaptureMultiCamSession`), so `canAddInput` refused the
        // duplicate, the guard below failed, and `.setupFailed` was published:
        // a modal "Camera setup failed. Please restart the app." on top of a
        // viewfinder that was running perfectly.
        //
        // The check and the claim are now the same main-actor turn, so there
        // is no hop between them for a second call to slip through.
        isConfigured = true
        sessionQueue.async { [weak self] in
            guard let self else { return }
            self.session.beginConfiguration()
            self.session.sessionPreset = .photo

            // Input
            guard
                let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back),
                let input = try? AVCaptureDeviceInput(device: device),
                self.session.canAddInput(input)
            else {
                // `beginConfiguration` has to be balanced on every path. This
                // one returned without committing, so the session stayed
                // mid-configuration for the life of the process: every later
                // `startRunning` was a no-op and the preview never came back,
                // even if the camera became available. A user who denied
                // access, granted it in Settings and returned got a black
                // viewfinder until they force-quit.
                self.session.commitConfiguration()
                // Give the claim back: configuration genuinely failed, so a
                // later appearance has to be allowed to try the whole thing
                // again. Without this the claim above would turn a real
                // failure into a permanent one.
                Task { @MainActor [weak self] in
                    self?.isConfigured = false
                    self?.error = .setupFailed
                }
                return
            }
            self.session.addInput(input)

            // Output
            if self.session.canAddOutput(self.photoOutput) {
                self.session.addOutput(self.photoOutput)
                if #available(iOS 16.0, *) {
                    // 4032×3024 was hard-coded here. AVFoundation raises
                    // `NSInvalidArgumentException` — an uncatchable abort — for
                    // a value the active format does not list, and this target
                    // installs on iPad in compatibility mode, where the 8MP
                    // (3264×2448) cameras of the iPad 6–9, mini 5 and Air 3
                    // never offered it.
                    //
                    // This stays a *cap*, not a maximum: the 48MP wide camera on
                    // Pro iPhones lists 8064×6048, and asking for it would
                    // quadruple decode cost and memory for an image that is
                    // downscaled to 1568px before it leaves the device. So take
                    // the largest supported size at or under 12MP, and fall back
                    // to the largest on hardware that offers nothing that big.
                    if let dimensions = Self.preferredPhotoDimensions(
                        device.activeFormat.supportedMaxPhotoDimensions) {
                        self.photoOutput.maxPhotoDimensions = dimensions
                    }
                } else {
                    self.photoOutput.isHighResolutionCaptureEnabled = true
                }
            }

            self.session.commitConfiguration()
            self.session.startRunning()
        }
    }

    func startSession() {
        sessionQueue.async { [weak self] in
            guard let self, !self.session.isRunning else { return }
            self.session.startRunning()
        }
    }

    func stopSession() {
        sessionQueue.async { [weak self] in
            guard let self, self.session.isRunning else { return }
            self.session.stopRunning()
        }
    }

    /// Whether a session runtime error is one that starting the session again
    /// fixes. `mediaServicesWereReset` means mediaserverd restarted underneath
    /// us and the session is stopped but still configured, so `startRunning`
    /// is the whole recovery.
    ///
    /// Nothing else is retried, which is why this is a decision rather than an
    /// unconditional restart: a failure a restart cannot fix posts another
    /// runtime error when we retry it, and that is a notification-and-restart
    /// loop for as long as the screen is open.
    ///
    /// Pure and `nonisolated` for the same reason `flashMode` is — the
    /// hardware cases cannot be reproduced in a test, so the decision is
    /// tested apart from the hardware.
    nonisolated static func shouldRestart(after code: AVError.Code) -> Bool {
        code == .mediaServicesWereReset
    }

    /// The camera came back: another client released it, or the app left Split
    /// View. AVFoundation makes no promise to resume the session for us, and
    /// `startSession` no-ops when it is already running, so just ask. A
    /// session we stopped ourselves is never *interrupted*, so this cannot
    /// resurrect the preview behind a result sheet that stopped it.
    ///
    /// `nonisolated` because AVFoundation posts these from its own queue and
    /// an `@objc` selector inserts no actor hop; the hop back onto the main
    /// actor is the explicit `Task`, as in the capture delegate below.
    @objc private nonisolated func sessionInterruptionEnded(_ notification: Notification) {
        Task { @MainActor [weak self] in self?.startSession() }
    }

    /// The `Notification` is read here and never captured — it is not
    /// `Sendable`, so only the decision crosses into the `Task`.
    @objc private nonisolated func sessionRuntimeError(_ notification: Notification) {
        guard let error = notification.userInfo?[AVCaptureSessionErrorKey] as? AVError,
              Self.shouldRestart(after: error.code)
        else { return }
        Task { @MainActor [weak self] in self?.startSession() }
    }

    /// Largest supported size at or below the 12MP cap, else the largest on
    /// offer. Nil only when the format lists nothing at all.
    nonisolated static func preferredPhotoDimensions(
        _ supported: [CMVideoDimensions]
    ) -> CMVideoDimensions? {
        let cap = 4032 * 3024
        func pixels(_ d: CMVideoDimensions) -> Int { Int(d.width) * Int(d.height) }
        let atOrUnderCap = supported.filter { pixels($0) <= cap }
        let candidates = atOrUnderCap.isEmpty ? supported : atOrUnderCap
        return candidates.max { pixels($0) < pixels($1) }
    }

    /// The flash mode to ask for, given what this device actually offers.
    ///
    /// Setting `AVCapturePhotoSettings.flashMode` to a value outside the
    /// output's `supportedFlashModes` raises `NSInvalidArgumentException` —
    /// which is an abort, not a throwable error, so there is nothing to catch.
    /// An iPad running the app in iPhone compatibility mode reports `[.off]`
    /// and nothing else, and `.auto` was being set unconditionally: every
    /// shutter tap killed the process, on the one screen the whole app exists
    /// for.
    ///
    /// Pure and `nonisolated` for the same reason `preferredPhotoDimensions`
    /// is — the hardware case cannot be reproduced in a test, so the decision
    /// is tested apart from the hardware.
    nonisolated static func flashMode(
        preferring preferred: AVCaptureDevice.FlashMode,
        supported: [AVCaptureDevice.FlashMode]
    ) -> AVCaptureDevice.FlashMode? {
        if supported.contains(preferred) { return preferred }
        // `.off` before `.first`: a device that cannot do `.auto` should not be
        // handed `.on` as a consolation, which would fire a flash the user
        // never asked for.
        if supported.contains(.off) { return .off }
        return supported.first
    }

    func capturePhoto() {
        sessionQueue.async { [weak self] in
            guard let self else { return }
            // A tap that arrives while the session is stopped used to return
            // from here without a trace: no photo, no delegate callback, no
            // error — and `Haptics.capture()` has already fired on the way in,
            // so the device confirmed a photo that was never taken and the
            // viewfinder just sat there.
            //
            // The queue is serial, so a tap racing `startRunning` is safely
            // ordered behind it. What is left is the state that does *not*
            // heal on its own: a configuration that failed, a capture runtime
            // error, another app holding the camera. There the shutter is dead
            // for the life of the screen, and publishing the failure is what
            // turns silence into the alert ScanView already presents.
            guard self.session.isRunning else {
                Task { @MainActor [weak self] in self?.error = .captureFailed }
                return
            }
            let settings = AVCapturePhotoSettings()
            if let mode = Self.flashMode(preferring: .auto,
                                         supported: self.photoOutput.supportedFlashModes) {
                settings.flashMode = mode
            }
            self.photoOutput.capturePhoto(with: settings, delegate: self)
        }
    }
}

// MARK: - AVCapturePhotoCaptureDelegate
extension CameraManager: AVCapturePhotoCaptureDelegate {
    nonisolated func photoOutput(
        _ output: AVCapturePhotoOutput,
        didFinishProcessingPhoto photo: AVCapturePhoto,
        error: Error?
    ) {
        guard
            error == nil,
            let data = photo.fileDataRepresentation(),
            let image = UIImage(data: data)
        else {
            Task { @MainActor [weak self] in self?.error = .captureFailed }
            return
        }
        Task { @MainActor [weak self] in self?.capturedImage = image }
    }
}

// MARK: - Camera Preview (UIViewRepresentable)
struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession

    func makeUIView(context: Context) -> PreviewUIView {
        let view = PreviewUIView()
        view.session = session
        return view
    }

    func updateUIView(_ uiView: PreviewUIView, context: Context) {}
}

final class PreviewUIView: UIView {
    override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }

    // Safe: layerClass is overridden to AVCaptureVideoPreviewLayer so UIKit guarantees this type.
    // swiftlint:disable:next force_cast — guaranteed by the layerClass override above.
    var previewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }

    var session: AVCaptureSession? {
        get { previewLayer.session }
        set {
            previewLayer.session = newValue
            previewLayer.videoGravity = .resizeAspectFill
        }
    }
}

// MARK: - Error
enum CameraError: LocalizedError {
    case setupFailed
    case captureFailed
    case permissionDenied

    var errorDescription: String? {
        switch self {
        case .setupFailed:       return "Camera setup failed. Please restart the app."
        case .captureFailed:     return "Could not capture photo. Try again."
        case .permissionDenied:  return "Camera access denied. Enable it in Settings."
        }
    }
}
