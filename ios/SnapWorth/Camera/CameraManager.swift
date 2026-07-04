import AVFoundation
import SwiftUI
import Combine

// MARK: - Camera Manager

@MainActor
final class CameraManager: NSObject, ObservableObject {
    @Published var capturedImage: UIImage?
    @Published var authStatus: AVAuthorizationStatus = .notDetermined
    @Published var error: CameraError?

    let session = AVCaptureSession()
    private let photoOutput = AVCapturePhotoOutput()
    private var sessionQueue = DispatchQueue(label: "com.snapworth.camera")

    override init() {
        super.init()
        authStatus = AVCaptureDevice.authorizationStatus(for: .video)
    }

    func requestPermissionAndSetup() {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            setupSession()
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
                DispatchQueue.main.async {
                    self?.authStatus = granted ? .authorized : .denied
                    if granted { self?.setupSession() }
                }
            }
        case .denied, .restricted:
            authStatus = .denied
        @unknown default:
            break
        }
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
                self.photoOutput.isHighResolutionCaptureEnabled = true
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

    func capturePhoto() {
        let settings = AVCapturePhotoSettings()
        settings.flashMode = .auto
        photoOutput.capturePhoto(with: settings, delegate: self)
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
