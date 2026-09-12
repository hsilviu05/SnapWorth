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
                Task { @MainActor [weak self] in self?.error = .setupFailed }
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
            Task { @MainActor [weak self] in self?.isConfigured = true }
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
            guard let self, self.session.isRunning else { return }
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
