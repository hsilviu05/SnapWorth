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
    @State private var showNotifPriming = false
    @State private var showThriftFlip = false

    /// Value-first paywall: the intro paywall is deferred until the user has
    /// actually seen their first result. Shown once, then never again here.
    @AppStorage("hasSeenFirstResultPaywall") private var hasSeenFirstResultPaywall = false

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
                HStack(alignment: .firstTextBaseline) {
                    Text("SnapWorth")
                        .font(.fraunces(20, weight: .bold, relativeTo: .title3))
                        .foregroundStyle(Color.snapOnCharcoal)
                        // Wordmark: must never break mid-word, and the counter
                        // beside it must not squeeze it into doing so.
                        .lineLimit(1)
                        .fixedSize(horizontal: true, vertical: false)
                        .layoutPriority(1)
                        .accessibilityAddTraits(.isHeader)

                    Spacer(minLength: 8)

                    // Free scan counter / upgrade CTA
                    if !purchaseService.isSubscribed {
                        // Server's figure when it has told us; local estimate
                        // otherwise. Reading the compiled-in limit alone showed
                        // "3 free scans left today" against a server enforcing 1.
                        let remaining = vm.freeScansRemaining
                        if remaining == 0 {
                            Button { vm.paywallTrigger = .upgradeButton; vm.showPaywall = true } label: {
                                Text("Upgrade to Pro")
                                    .font(.snapCaption.bold())
                                    .foregroundStyle(Color.snapOnAccent)
                                    .padding(.horizontal, 10)
                                    .padding(.vertical, 5)
                                    .background(Color.snapTerracotta)
                                    .clipShape(Capsule())
                            }
                            .snapHitTarget()
                            .accessibilityLabel("Upgrade to Pro")
                            .accessibilityHint("You've used today's free scans. Opens subscription options.")
                        } else {
                            Text("\(remaining) free scan\(remaining == 1 ? "" : "s") left today")
                                .font(.snapCaption)
                                .foregroundStyle(Color.snapOnCharcoal.opacity(0.8))
                                .multilineTextAlignment(.trailing)
                                .padding(.horizontal, 10)
                                .padding(.vertical, 5)
                                .background(Color.snapCharcoal.opacity(0.5))
                                .clipShape(Capsule())
                                .accessibilityLabel(
                                    "\(remaining) free scan\(remaining == 1 ? "" : "s") left today")
                        }
                    }
                }
                .padding(.horizontal, 20)
                .padding(.top, 8)

                Spacer()

                // Viewfinder frame guide — compact = phone, regular = iPad
                let viewfinderSide: CGFloat = hSizeClass == .regular ? 320 : 300
                RoundedRectangle(cornerRadius: 24, style: .continuous)
                    .strokeBorder(Color.snapOnCharcoal.opacity(0.5), lineWidth: 2)
                    .frame(width: viewfinderSide, height: viewfinderSide)
                    .overlay(CornerAccents())
                    // Framing guide is purely visual; the instruction below
                    // carries the same information for VoiceOver.
                    .accessibilityHidden(true)

                Text("Center the item — tags & logos help")
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapOnCharcoal.opacity(0.7))
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 20)
                    .padding(.top, 16)

                Spacer()

                // Bottom controls
                HStack(alignment: .center) {
                    // Photo library picker
                    PhotosPicker(selection: $vm.selectedPhotoItem, matching: .images) {
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .fill(Color.snapOnCharcoal.opacity(isAnalyzing ? 0.1 : 0.2))
                            .frame(width: 52, height: 52)
                            .overlay(
                                Image(systemName: "photo.on.rectangle")
                                    .snapSymbol(22, weight: .light)
                                    .foregroundStyle(Color.snapOnCharcoal.opacity(isAnalyzing ? 0.4 : 1))
                            )
                    }
                    .disabled(vm.isAnalyzing)
                    .snapHitTarget()
                    .accessibilityLabel("Choose photo from library")
                    .accessibilityHint("Values an item from a photo you already have")
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
                        Haptics.capture()
                        cameraManager.capturePhoto()
                    } label: {
                        ZStack {
                            Circle()
                                .fill(Color.snapOnCharcoal)
                                .frame(width: 80, height: 80)
                            Circle()
                                .strokeBorder(Color.snapOnCharcoal.opacity(0.4), lineWidth: 3)
                                .frame(width: 94, height: 94)
                        }
                    }
                    .disabled(vm.isAnalyzing || cameraManager.authStatus != .authorized)
                    .accessibilityLabel(vm.isAnalyzing ? "Analyzing item" : "Take photo to scan")
                    .accessibilityHint(vm.isAnalyzing
                        ? "Please wait for the current scan to finish"
                        : "Captures the item and estimates its resale value")
                    // The primary action: reachable first under VoiceOver.
                    .accessibilitySortPriority(100)

                    Spacer()

                    // Thrift Flip — decide buy/skip while thrifting
                    Button {
                        showThriftFlip = true
                    } label: {
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .fill(Color.snapOnCharcoal.opacity(isAnalyzing ? 0.1 : 0.2))
                            .frame(width: 52, height: 52)
                            .overlay(
                                VStack(spacing: 1) {
                                    Image(systemName: "arrow.triangle.2.circlepath")
                                        .snapSymbol(20, weight: .light)
                                    Text("Flip")
                                        .font(.dmSans(9, weight: .semibold, relativeTo: .caption2))
                                }
                                .foregroundStyle(Color.snapOnCharcoal.opacity(isAnalyzing ? 0.4 : 1))
                            )
                    }
                    .disabled(vm.isAnalyzing)
                    .snapHitTarget()
                    .accessibilityLabel("Thrift Flip")
                    .accessibilityHint("Check resale profit after fees before you buy")
                }
                .padding(.horizontal, 36)
                .padding(.bottom, 48)
            }

            // ── Analyzing overlay ─────────────────────────────────────────
            // Freeze the captured frame behind the overlay so it's obvious the
            // photo is already taken — the user can lower the phone.
            if vm.isAnalyzing {
                if let shot = vm.capturedImage {
                    Image(uiImage: shot)
                        .resizable()
                        .scaledToFill()
                        .ignoresSafeArea()
                        .transition(.opacity)
                }
                AnalyzingOverlay()
                    .transition(.opacity)
            }
        }
        .snapAnimation(.easeInOut(duration: 0.3), value: vm.isAnalyzing)
        .onChange(of: cameraManager.capturedImage) { _, image in
            guard let image else { return }
            vm.capturedImage = image
            Task { await triggerScan(image: image) }
        }
        .onAppear {
            cameraManager.requestPermissionAndSetup()
            // Warm the Taptic Engine while the camera starts. The shutter tap
            // is the one haptic a user would notice missing, and it fires when
            // the main thread is busiest.
            Haptics.prepare()
        }
        .onChange(of: cameraManager.authStatus) { _, status in
            if status == .denied {
                Analytics.shared.track(.scanFailed(reason: .permission))
            }
        }
        .onDisappear { cameraManager.stopSession() }
        .sheet(isPresented: $showResult, onDismiss: {
            // Runs whether the user taps "Done" or swipes down
            vm.reset()
            cameraManager.capturedImage = nil

            // Value-first paywall: the user has now seen a real result. If this is
            // their first one and they're not subscribed, surface the intro paywall
            // (and skip notif priming this round so we don't stack two prompts).
            if !purchaseService.isSubscribed && !hasSeenFirstResultPaywall {
                hasSeenFirstResultPaywall = true
                vm.paywallTrigger = .onboarding
                Task {
                    // Let the result sheet finish dismissing before presenting.
                    try? await Task.sleep(for: .milliseconds(400))
                    vm.showPaywall = true
                }
                return
            }

            // Otherwise this is a moment of demonstrated value — prime for
            // notifications once, only if never decided.
            Task {
                if await NotificationManager.shared.shouldPrimeAfterScan() {
                    showNotifPriming = true
                }
            }
        }) {
            resultSheet
        }
        .sheet(isPresented: $vm.showPaywall) {
            PaywallView(purchaseService: purchaseService, trigger: vm.paywallTrigger)
        }
        .fullScreenCover(isPresented: $showThriftFlip) {
            ThriftFlipView(purchaseService: purchaseService)
        }
        .alert("Stay on top of your flips", isPresented: $showNotifPriming) {
            Button("Enable notifications") {
                Task {
                    await NotificationManager.shared.enableFromPriming(
                        context: modelContext, purchaseService: purchaseService)
                }
            }
            Button("Not now", role: .cancel) {
                NotificationManager.shared.declinePriming()
            }
        } message: {
            Text("We'll let you know when your monthly recap is ready and remind you to update your ledger. Functional only — no spam, and you can turn these off anytime in Settings.")
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

    // MARK: - Result Sheet

    /// Extracted from `body` deliberately: `body` is a single large expression,
    /// and adding one more inference site inside the sheet closure pushed the
    /// type-checker past its budget ("unable to type-check in reasonable
    /// time"). Splitting it lets each part solve independently.
    @ViewBuilder
    private var resultSheet: some View {
        if let result = vm.scanResult {
            ResultView(
                result: result,
                purchaseService: purchaseService,
                onDismiss: { showResult = false },
                didSave: !vm.saveFailed
            )
            .presentationDetents([.large])
            .presentationDragIndicator(.visible)
        }
    }

    // MARK: - Permission Placeholder
    private var permissionPlaceholder: some View {
        VStack(spacing: 20) {
            Image(systemName: "camera.slash")
                .snapSymbol(48, weight: .light)
                .foregroundStyle(Color.snapOnCharcoal.opacity(0.5))
                .accessibilityHidden(true)

            Text("Camera access needed to scan items")
                .font(.snapBody)
                .foregroundStyle(Color.snapOnCharcoal.opacity(0.8))
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 40)
                .accessibilityAddTraits(.isHeader)

            PrimaryButton(title: "Open Settings") {
                if let url = URL(string: UIApplication.openSettingsURLString) {
                    UIApplication.shared.open(url)
                }
            }
            .frame(maxWidth: 200)
            .accessibilityHint("Opens iOS Settings so you can allow camera access")
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
        .stroke(Color.snapOnCharcoal, lineWidth: thick)
    }
}
