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
                    // never offered it. Ask the format what it supports.
                    if let best = device.activeFormat.supportedMaxPhotoDimensions
                        .max(by: { Int($0.width) * Int($0.height) < Int($1.width) * Int($1.height) }) {
                        self.photoOutput.maxPhotoDimensions = best
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

    func capturePhoto() {
        sessionQueue.async { [weak self] in
            guard let self, self.session.isRunning else { return }
            let settings = AVCapturePhotoSettings()
            settings.flashMode = .auto
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
