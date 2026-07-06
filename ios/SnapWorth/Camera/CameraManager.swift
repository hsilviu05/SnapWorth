import AVFoundation
import SwiftUI

// MARK: - Camera Manager

@MainActor
final class CameraManager: NSObject, ObservableObject {
    @Published var capturedImage: UIImage?
    @Published var authStatus: AVAuthorizationStatus = .notDetermined
    @Published var error: CameraError?

    let session = AVCaptureSession()
    private let photoOutput = AVCapturePhotoOutput()
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
                DispatchQueue.main.async {
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
                DispatchQueue.main.async { self.error = .setupFailed }
                return
            }
            self.session.addInput(input)

            // Output
            if self.session.canAddOutput(self.photoOutput) {
                self.session.addOutput(self.photoOutput)
                if #available(iOS 16.0, *) {
                    self.photoOutput.maxPhotoDimensions = CMVideoDimensions(width: 4032, height: 3024)
                } else {
                    self.photoOutput.isHighResolutionCaptureEnabled = true
                }
            }

            self.session.commitConfiguration()
            self.session.startRunning()
            // isConfigured is @MainActor — must hop back to main thread to write it
            DispatchQueue.main.async { self.isConfigured = true }
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
            guard let self else { return }
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
