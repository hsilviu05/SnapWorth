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

    @Environment(\.scenePhase) private var scenePhase

    /// Mirrors whether a run is live. `Activity.activities` is the truth — this
    /// only exists so the button re-renders, and is re-read on appear because a
    /// run can end while the app is backgrounded (the eight-hour cap) or from
    /// the Lock Screen itself.
    ///
    /// `onAppear` alone was not enough: this view does not disappear when the
    /// app is backgrounded, so the eight-hour cap or an End tapped on the Lock
    /// Screen left the control still reading "End run" for a run that was over.
    /// Re-read on every return to `.active` as well.
    @State private var isRunOn = ThriftRunController.isRunning

    /// Whether Live Activities are switched on for the app, sampled rather than
    /// read inside `body`.
    ///
    /// `ThriftRunController.isAvailable` builds a fresh
    /// `ActivityAuthorizationInfo()` on every call, and nothing published it —
    /// so the control's visibility was only ever right by accident. A user who
    /// turned Live Activities off in Settings and came back still saw the
    /// button, and `Activity.request` throws in exactly that case; one who
    /// turned them *on* did not get the button until something else happened
    /// to re-render this view.
    @State private var isRunAvailable = ThriftRunController.isAvailable

    /// A capture has been asked for and its scan has not finished.
    ///
    /// `vm.isAnalyzing` cannot carry this: it is set inside the async
    /// `startScan`, several hops after the tap, and the shutter's `.disabled`
    /// read it — so two taps inside that window both reached
    /// `capturePhoto()`. Two photos were then delivered, each spawning its own
    /// `triggerScan` task, and the first to finish cleared `vm.capturedImage`
    /// for both: the freeze-frame vanished mid-analysis, leaving the overlay
    /// over a live viewfinder. On the free tier the second scan could also
    /// spend the day's remaining allowance on a photo nobody asked for.
    ///
    /// Set synchronously in the button action, so the guard and the claim are
    /// the same run-loop turn and there is no window at all.
    @State private var captureInFlight = false

    /// Anything presented on top of the camera. See the `onChange` below.
    private var isCameraObscured: Bool {
        showResult || showThriftFlip || showNotifPriming || vm.showPaywall
    }

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
                                    .background(Color.snapTerracottaFill)
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

                // Streak: shown from two days on, for every tier. One day is
                // not a streak, and a "1-day streak" badge reads as a taunt.
                if vm.streak >= 2 {
                    HStack {
                        Spacer()
                        Text("🔥 \(vm.streak)-day streak")
                            .font(.snapCaption.bold())
                            .foregroundStyle(Color.snapOnCharcoal.opacity(0.9))
                            .padding(.horizontal, 10)
                            .padding(.vertical, 4)
                            .background(Color.snapCharcoal.opacity(0.5))
                            .clipShape(Capsule())
                            .accessibilityLabel("\(vm.streak) day scanning streak")
                    }
                    .padding(.horizontal, 20)
                    .padding(.top, 6)
                }

                // Thrift run. Hidden entirely when Live Activities are off for
                // the app — a button that silently does nothing is worse than
                // no button, and `Activity.request` throws in exactly that case.
                if isRunAvailable {
                    ThriftRunControl(isRunning: $isRunOn)
                        .padding(.horizontal, 20)
                        .padding(.top, 6)
                }

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
                        // Claimed here, synchronously, before anything async:
                        // see `captureInFlight`. The `.disabled` below reads it
                        // too, but a disabled Button still has a window while
                        // SwiftUI re-renders, and this closes it.
                        guard !captureInFlight else { return }
                        captureInFlight = true
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
                    .disabled(vm.isAnalyzing || captureInFlight
                              || cameraManager.authStatus != .authorized)
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
        // The capture never arrived: the session was not running, or the
        // delegate could not turn the photo into an image. Without this the
        // shutter would stay claimed and dead for the life of the screen,
        // which is a worse failure than the one being reported.
        .onChange(of: cameraManager.error) { _, error in
            if error != nil { captureInFlight = false }
        }
        .onAppear {
            // Not unconditional. A sheet or cover presented over this view does
            // not fire `onDisappear`, but it *does* fire `onAppear` again when
            // the user switches tabs and comes back — so this started the full
            // photo-preset pipeline behind the result sheet, the paywall and
            // Thrift Flip, which is exactly the battery leak the
            // `isCameraObscured` handler below exists to stop. That handler
            // starts the session when the obstruction goes away, so there is
            // nothing to do here while one is up.
            if !isCameraObscured {
                cameraManager.requestPermissionAndSetup()
            }
            // Warm the Taptic Engine while the camera starts. The shutter tap
            // is the one haptic a user would notice missing, and it fires when
            // the main thread is busiest.
            Haptics.prepare()
        }
        .onChange(of: scenePhase) { _, phase in
            guard phase == .active else { return }
            // Both of these can change while the app is not running: a run can
            // end from the Lock Screen or hit the eight-hour cap, and Live
            // Activities can be switched off for the app in Settings. This
            // view never disappeared, so nothing else would re-read them.
            isRunOn = ThriftRunController.isRunning
            isRunAvailable = ThriftRunController.isAvailable
        }
        .onChange(of: cameraManager.authStatus) { _, status in
            if status == .denied {
                Analytics.shared.track(.scanFailed(reason: .permission))
            }
        }
        .onDisappear { cameraManager.stopSession() }
        // A sheet or cover presented *over* this view does not fire its
        // `onDisappear`, so the full photo-preset capture pipeline — sensor,
        // ISP and a 30fps preview — kept running behind the result sheet, the
        // paywall and Thrift Flip, for however long the user spent reading or
        // typing. Thrift Flip then started a second session on top of it.
        .onChange(of: isCameraObscured) { _, obscured in
            if obscured {
                cameraManager.stopSession()
            } else if cameraManager.authStatus == .authorized {
                cameraManager.startSession()
            }
        }
        // A scan request means "give me a live camera", not "select tab 0".
        //
        // The only observer of this used to be `MainTabView`, which sets
        // `selectedTab = 0` and nothing else. Every presentation over this
        // view is private `@State` here, so when the Scan tab was *already*
        // selected and obscured — the result sheet still up, the intro
        // paywall, the Thrift Flip cover — the drained press changed nothing
        // at all, while `takePendingAction` had already consumed it. The user
        // pressed the Control Centre button, the app came forward, and the
        // sheet they pressed it to get past was still there.
        //
        // Clearing these also re-fires `isCameraObscured` above, which is what
        // restarts the capture session.
        .onReceive(NotificationCenter.default.publisher(for: .snapWidgetOpenScan)) { _ in
            showResult = false
            showThriftFlip = false
            showNotifPriming = false
            vm.showPaywall = false
            vm.reset()
            cameraManager.capturedImage = nil
        }
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
                didSave: !vm.saveFailed,
                coverPrice: true
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
        // Released on every exit, success or failure, so the shutter comes
        // back exactly once per capture.
        defer { captureInFlight = false }
        let repository = ScanRepository(context: modelContext)
        await vm.startScan(image: image, purchaseService: purchaseService, repository: repository)
        // Release the full-resolution capture the moment it stops being
        // needed. Both the upload (1568px) and the stored copy (1024px) are
        // already encoded by now, and the only view that reads this image is
        // the freeze-frame behind the analysing overlay, which has just gone.
        // These references used to be cleared in `sheet(onDismiss:)`, so a
        // 12MP capture — 48.8MB decoded, and up to 195MB on a 48MP HEIF —
        // stayed resident for the whole time the result sheet was open.
        vm.capturedImage = nil
        cameraManager.capturedImage = nil
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

// MARK: - Thrift run control

/// Starts and ends the Live Activity from the scan screen.
///
/// Deliberately one control rather than a start button and a separate end
/// button somewhere else: a run you cannot see how to stop is a run people
/// stop trusting.
private struct ThriftRunControl: View {
    @Binding var isRunning: Bool

    /// Set when `start()` came back false. Reading the state back afterwards
    /// keeps the *button* honest — it does not claim a run that never began —
    /// but it left the tap itself completely silent: the label stayed "Start a
    /// run", nothing appeared on the Lock Screen, and nothing said why. The
    /// comment below has always claimed this control avoids "a button that
    /// silently does nothing", and until now that was only half true.
    @State private var startRefused = false

    var body: some View {
        HStack {
            Spacer()
            VStack(alignment: .trailing, spacing: 4) {
                Button {
                    Haptics.selection()
                    Task {
                        if isRunning {
                            await ThriftRunController.end()
                            startRefused = false
                        } else {
                            // `start` returns false when the system refuses:
                            // permission revoked between the availability
                            // check and the request, or too many Activities
                            // live. The return used to be discarded.
                            let started = ThriftRunController.start()
                            startRefused = !started
                            if !started {
                                Haptics.failure()
                                // Spoken, not just drawn. The line below
                                // appears where there was nothing a moment
                                // ago, and a VoiceOver user who just activated
                                // this button has no reason to go looking for
                                // it.
                                UIAccessibility.post(
                                    notification: .announcement,
                                    argument: "Couldn't start the run. "
                                        + "Check Live Activities in Settings.")
                            }
                        }
                        // Read back rather than toggling: a button that lies
                        // about its own state is how a user ends up with two
                        // runs.
                        isRunning = ThriftRunController.isRunning
                    }
                } label: {
                    Label(isRunning ? "End run" : "Start a run",
                          systemImage: isRunning ? "stop.circle.fill" : "play.circle.fill")
                        .font(.snapCaption.bold())
                        .foregroundStyle(Color.snapOnCharcoal.opacity(0.9))
                        .padding(.horizontal, 10)
                        .padding(.vertical, 4)
                        .background(Color.snapCharcoal.opacity(0.5))
                        .clipShape(Capsule())
                }
                .snapHitTarget()
                .accessibilityLabel(isRunning ? "End thrift run" : "Start a thrift run")
                .accessibilityHint(isRunning
                                   ? "Removes the running total from your Lock Screen"
                                   : "Shows a running total of this trip on your Lock Screen")

                if startRefused {
                    Text("Couldn't start — check Live Activities in Settings")
                        .font(.snapCaption)
                        .foregroundStyle(Color.snapOnCharcoal.opacity(0.9))
                        .padding(.horizontal, 10)
                        .padding(.vertical, 4)
                        .background(Color.snapCharcoal.opacity(0.5))
                        .clipShape(Capsule())
                        .transition(.opacity)
                }
            }
        }
        .snapAnimation(.easeInOut(duration: 0.2), value: startRefused)
        .onAppear { isRunning = ThriftRunController.isRunning }
    }
}
