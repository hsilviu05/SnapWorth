import SwiftUI
import PhotosUI
import SwiftData

struct ScanView: View {
    let purchaseService: any PurchaseService

    @Environment(\.modelContext) private var modelContext
    @Environment(\.horizontalSizeClass) private var hSizeClass
    @StateObject private var cameraManager = CameraManager()
    @State private var vm = ScanViewModel()
    @State private var showResult = false

    var body: some View {
        let isAnalyzing = vm.isAnalyzing
        ZStack {
            // ── Camera background (warm charcoal) ─────────────────────────
            Color.snapCharcoal.ignoresSafeArea()

            if cameraManager.authStatus == .authorized {
                CameraPreview(session: cameraManager.session)
                    .ignoresSafeArea()
            } else {
                permissionPlaceholder
            }

            // ── Camera UI overlay ─────────────────────────────────────────
            VStack {
                // Top bar
                HStack {
                    Text("SnapWorth")
                        .font(.fraunces(20, weight: .bold))
                        .foregroundStyle(Color.snapBackground)

                    Spacer()

                    // Free scan counter / upgrade CTA
                    if !purchaseService.isSubscribed {
                        let remaining = max(0, Config.freeScansAllowed - vm.freeScansUsed)
                        if remaining == 0 {
                            Button { vm.showPaywall = true } label: {
                                Text("Upgrade to Pro")
                                    .font(.snapCaption.bold())
                                    .foregroundStyle(Color.snapBackground)
                                    .padding(.horizontal, 10)
                                    .padding(.vertical, 5)
                                    .background(Color.snapTerracotta)
                                    .clipShape(Capsule())
                            }
                        } else {
                            Text("\(remaining) free scan\(remaining == 1 ? "" : "s") left")
                                .font(.snapCaption)
                                .foregroundStyle(Color.snapBackground.opacity(0.8))
                                .padding(.horizontal, 10)
                                .padding(.vertical, 5)
                                .background(Color.snapCharcoal.opacity(0.5))
                                .clipShape(Capsule())
                        }
                    }
                }
                .padding(.horizontal, 20)
                .padding(.top, 8)

                Spacer()

                // Viewfinder frame guide — compact = phone, regular = iPad
                let viewfinderSide: CGFloat = hSizeClass == .regular ? 320 : 300
                RoundedRectangle(cornerRadius: 24, style: .continuous)
                    .strokeBorder(Color.snapBackground.opacity(0.5), lineWidth: 2)
                    .frame(width: viewfinderSide, height: viewfinderSide)
                    .overlay(CornerAccents())

                Text("Center the item — tags & logos help")
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapBackground.opacity(0.7))
                    .padding(.top, 16)

                Spacer()

                // Bottom controls
                HStack(alignment: .center) {
                    // Photo library picker
                    PhotosPicker(selection: $vm.selectedPhotoItem, matching: .images) {
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .fill(Color.snapBackground.opacity(isAnalyzing ? 0.1 : 0.2))
                            .frame(width: 52, height: 52)
                            .overlay(
                                Image(systemName: "photo.on.rectangle")
                                    .font(.system(size: 22, weight: .light))
                                    .foregroundStyle(Color.snapBackground.opacity(isAnalyzing ? 0.4 : 1))
                            )
                    }
                    .disabled(vm.isAnalyzing)
                    .accessibilityLabel("Choose photo from library")
                    .onChange(of: vm.selectedPhotoItem) { _, newItem in
                        guard newItem != nil else { return }
                        vm.capturedImage = nil
                        Task {
                            await vm.loadSelectedPhoto()
                            if let img = vm.capturedImage {
                                await triggerScan(image: img)
                            }
                        }
                    }

                    Spacer()

                    // Shutter button
                    Button {
                        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                        cameraManager.capturePhoto()
                    } label: {
                        ZStack {
                            Circle()
                                .fill(Color.snapBackground)
                                .frame(width: 80, height: 80)
                            Circle()
                                .strokeBorder(Color.snapBackground.opacity(0.4), lineWidth: 3)
                                .frame(width: 94, height: 94)
                        }
                    }
                    .disabled(vm.isAnalyzing || cameraManager.authStatus != .authorized)
                    .accessibilityLabel(vm.isAnalyzing ? "Analyzing item" : "Take photo to scan")

                    Spacer()

                    // Placeholder spacer (symmetric layout)
                    Color.clear.frame(width: 52, height: 52)
                }
                .padding(.horizontal, 36)
                .padding(.bottom, 48)
            }

            // ── Analyzing overlay ─────────────────────────────────────────
            if vm.isAnalyzing {
                AnalyzingOverlay()
                    .transition(.opacity)
            }
        }
        .animation(.easeInOut(duration: 0.3), value: vm.isAnalyzing)
        .onChange(of: cameraManager.capturedImage) { _, image in
            guard let image else { return }
            vm.capturedImage = image
            Task { await triggerScan(image: image) }
        }
        .onAppear { cameraManager.requestPermissionAndSetup() }
        .onDisappear { cameraManager.stopSession() }
        .sheet(isPresented: $showResult, onDismiss: {
            // Runs whether the user taps "Done" or swipes down
            vm.reset()
            cameraManager.capturedImage = nil
        }) {
            if let result = vm.scanResult {
                ResultView(result: result, onDismiss: { showResult = false })
                    .presentationDetents([.large])
            }
        }
        .sheet(isPresented: $vm.showPaywall) {
            PaywallView(purchaseService: purchaseService)
        }
        .alert("Scan Failed", isPresented: Binding(
            get: { vm.errorMessage != nil },
            set: { if !$0 { vm.errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) { vm.errorMessage = nil }
        } message: {
            Text(vm.errorMessage ?? "")
        }
        .alert("Camera Error", isPresented: Binding(
            get: { cameraManager.error != nil },
            set: { if !$0 { cameraManager.error = nil } }
        )) {
            Button("OK", role: .cancel) { cameraManager.error = nil }
        } message: {
            Text(cameraManager.error?.errorDescription ?? "")
        }
    }

    // MARK: - Permission Placeholder
    private var permissionPlaceholder: some View {
        VStack(spacing: 20) {
            Image(systemName: "camera.slash")
                .font(.system(size: 48, weight: .light))
                .foregroundStyle(Color.snapBackground.opacity(0.5))

            Text("Camera access needed to scan items")
                .font(.snapBody)
                .foregroundStyle(Color.snapBackground.opacity(0.8))
                .multilineTextAlignment(.center)
                .padding(.horizontal, 40)

            PrimaryButton(title: "Open Settings") {
                if let url = URL(string: UIApplication.openSettingsURLString) {
                    UIApplication.shared.open(url)
                }
            }
            .frame(maxWidth: 200)
        }
    }

    private func triggerScan(image: UIImage) async {
        let repository = ScanRepository(context: modelContext)
        await vm.startScan(image: image, purchaseService: purchaseService, repository: repository)
        if vm.scanResult != nil {
            showResult = true
        }
    }
}

// MARK: - Corner accents for viewfinder
private struct CornerAccents: View {
    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let h = geo.size.height
            let len: CGFloat = 24
            let thick: CGFloat = 3

            ZStack {
                // Top-left
                corner(at: CGPoint(x: 0, y: 0), hLen: len, vLen: len, thick: thick, angle: 0)
                // Top-right
                corner(at: CGPoint(x: w, y: 0), hLen: -len, vLen: len, thick: thick, angle: 0)
                // Bottom-left
                corner(at: CGPoint(x: 0, y: h), hLen: len, vLen: -len, thick: thick, angle: 0)
                // Bottom-right
                corner(at: CGPoint(x: w, y: h), hLen: -len, vLen: -len, thick: thick, angle: 0)
            }
        }
    }

    private func corner(at origin: CGPoint, hLen: CGFloat, vLen: CGFloat, thick: CGFloat, angle: CGFloat) -> some View {
        Path { path in
            path.move(to: CGPoint(x: origin.x + hLen, y: origin.y))
            path.addLine(to: origin)
            path.addLine(to: CGPoint(x: origin.x, y: origin.y + vLen))
        }
        .stroke(Color.snapBackground, lineWidth: thick)
    }
}
