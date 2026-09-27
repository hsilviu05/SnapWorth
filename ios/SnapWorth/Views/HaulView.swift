import SwiftUI

// ═══════════════════════════════════════════════════════════════════
// MARK: - Haul mode (#93)
// ═══════════════════════════════════════════════════════════════════

/// The haul camera: the shutter stays live, each photo drops into a strip
/// along the bottom and is valued in the background, and the running total
/// sits at the top. Finish opens the summary — share card and drafts — over
/// the same screen.
///
/// Its own `CameraManager`, as `TagCameraSheet` has: the Scan tab's camera is
/// stopped underneath (`showHaul` is part of its `isCameraObscured`), and
/// sharing that session would tie this screen's shutter to the single-shot
/// flow's `capturedImage` slot.
///
/// The paywall is never opened by the queue. A hold only shows a banner; the
/// paywall opens when the user taps its Upgrade button.
struct HaulView: View {
    let session: HaulSession
    let purchaseService: any PurchaseService

    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityVoiceOverEnabled) private var voiceOverEnabled
    @StateObject private var camera = CameraManager()

    @State private var showSummary = false
    @State private var sheet: HaulSheet?
    /// Whether the sheet being dismissed is the paywall. `sheet` is already
    /// nil in `onDismiss`, and only a paywall can have bought anything — a
    /// share sheet closing must not restart the subscription retries.
    @State private var paywallShown = false
    @State private var failedItemID: UUID?
    @State private var deletingItemID: UUID?
    @State private var captureNotice: String?
    /// Finish or Done, waiting for captures still being processed to land.
    @State private var closing: CloseAction?

    private enum CloseAction { case finish, done }

    /// Stopping is deferred while a capture is still being processed — see
    /// `updateCamera`.
    private var cameraShouldRun: Bool {
        !showSummary && sheet == nil && scenePhase == .active
    }

    var body: some View {
        ZStack {
            Color.snapCharcoal.ignoresSafeArea()

            // Everything under the summary is hidden from VoiceOver while it
            // is up: SwiftUI does not prune what is only covered, and the
            // shutter there would fire a capture into a stopped session.
            Group {
                switch camera.authStatus {
                case .authorized:
                    CameraPreview(session: camera.session)
                        .ignoresSafeArea()
                case .notDetermined:
                    EmptyView()
                default:
                    CameraPermissionPlaceholder(restricted: camera.authStatus == .restricted)
                }

                captureChrome
            }
            .accessibilityHidden(showSummary)

            if showSummary {
                HaulSummaryView(
                    session: session,
                    purchaseService: purchaseService,
                    isClosing: closing == .done,
                    onShare: shareCard,
                    onShareDrafts: { sheet = .shareDrafts(session.allDraftsText()) },
                    onUpgrade: showPaywall,
                    onRestore: restore,
                    onKeepScanning: { showSummary = false },
                    onDone: { request(.done) })
                .transition(.move(edge: .bottom))
            }
        }
        .snapAnimation(.easeInOut(duration: 0.3), value: showSummary)
        .task {
            camera.onPhoto = { image in
                session.add(image)
                // Sighted users feel the shutter; this is the same moment,
                // heard.
                if UIAccessibility.isVoiceOverRunning {
                    UIAccessibility.post(notification: .announcement,
                                         argument: String(localized: "\(session.items.count) items"))
                }
            }
            Haptics.prepare()
            camera.requestPermissionAndSetup()
            // `onChange(of: scenePhase)` fires on changes only. Haul closed
            // while inactive — Done, then a swipe home before the capture it
            // waited for landed — reopens already active, and the session
            // would still believe it was in the background.
            session.setForeground(scenePhase == .active)
            session.open()
        }
        .onDisappear {
            camera.onPhoto = nil
            camera.stopSession()
        }
        .onChange(of: cameraShouldRun) { _, _ in updateCamera() }
        .onChange(of: camera.pendingCaptures) { _, count in
            if count == 0, let action = closing {
                closing = nil
                perform(action)
            }
            updateCamera()
        }
        .onChange(of: scenePhase) { _, phase in
            session.setForeground(phase == .active)
            if phase == .active {
                session.resumeNow()
                session.pump()
            }
        }
        // A failed capture is not a modal moment here: the next item is
        // already in frame. A line that fades says so without stopping the
        // haul; the alert below stays for a camera that will not start.
        .onChange(of: camera.error) { _, error in
            guard error == .captureFailed else { return }
            camera.error = nil
            let notice = String(localized: "That photo didn't take — try again.")
            captureNotice = notice
            UIAccessibility.post(notification: .announcement, argument: notice)
            Task {
                try? await Task.sleep(for: .seconds(3))
                if captureNotice == notice { captureNotice = nil }
            }
        }
        // A Control Centre or widget scan request means "a live camera": this
        // screen is one, once the summary is out of the way.
        .onReceive(NotificationCenter.default.publisher(for: .snapWidgetOpenScan)) { _ in
            showSummary = false
            sheet = nil
        }
        .sheet(item: $sheet, onDismiss: {
            // A no-op unless something is on hold waiting for exactly this.
            guard paywallShown else { return }
            paywallShown = false
            if purchaseService.isSubscribed { session.resumeAfterPurchase() }
        }) { sheet in
            switch sheet {
            case .paywall(let trigger):
                PaywallView(purchaseService: purchaseService, trigger: trigger)
            case .shareCard(let card):
                ActivityShareSheet(items: [card], onComplete: { _ in
                    Analytics.shared.track(.haulShared)
                })
            case .shareDrafts(let text):
                ActivityShareSheet(items: [text])
            }
        }
        .confirmationDialog("Couldn't value this photo",
                            isPresented: Binding(get: { failedItemID != nil },
                                                 set: { if !$0 { failedItemID = nil } }),
                            titleVisibility: .visible,
                            presenting: failedItem) { item in
            Button("Try again") { session.retry(item.id) }
            Button("Remove photo", role: .destructive) { session.remove(item.id) }
            Button("Cancel", role: .cancel) {}
        } message: { item in
            if let message = item.failureMessage {
                Text(verbatim: message)
            }
        }
        .confirmationDialog("Delete this find?",
                            isPresented: Binding(get: { deletingItemID != nil },
                                                 set: { if !$0 { deletingItemID = nil } }),
                            titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                if let id = deletingItemID { session.remove(id) }
            }
            Button("Cancel", role: .cancel) {}
        }
        .alert("Camera Error", isPresented: Binding(
            get: { camera.error == .setupFailed },
            set: { if !$0 { camera.error = nil } }
        )) {
            Button("OK", role: .cancel) { camera.error = nil }
        } message: {
            Text(camera.error?.errorDescription ?? "")
        }
    }

    // MARK: - Capture phase

    /// Laid out controls first. At accessibility text sizes a pause and a
    /// hold banner together can be taller than the screen, and a plain VStack
    /// centres what overflows — pushing the shutter off the bottom and
    /// Finish off the top. So the top bar, the strip and the shutter are
    /// sized first (priority 2), the banners get what is left (priority 1)
    /// and scroll when that is not enough, and the spacer gets the rest.
    private var captureChrome: some View {
        VStack(spacing: 10) {
            HaulTopBar(session: session, isFinishing: closing == .finish,
                       onFinish: { request(.finish) })
                .layoutPriority(2)

            ViewThatFits(in: .vertical) {
                banners
                ScrollView { banners }
                    .scrollBounceBehavior(.basedOnSize)
            }
            .layoutPriority(1)

            Spacer(minLength: 0)

            if let captureNotice {
                Text(captureNotice)
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapOnCharcoal.opacity(0.9))
                    .padding(.horizontal, 12)
                    .padding(.vertical, 5)
                    .background(Color.snapCharcoal.opacity(0.5))
                    .clipShape(Capsule())
                    .transition(.opacity)
            }

            HaulStrip(session: session,
                      voiceOverEnabled: voiceOverEnabled,
                      canCapture: camera.authStatus == .authorized,
                      onFailedTap: { failedItemID = $0 },
                      onDelete: { deletingItemID = $0 })
                .layoutPriority(2)

            Button {
                Haptics.capture()
                camera.capturePhoto()
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
            // Every tap is a photo: nothing about the queue disables this.
            .disabled(camera.authStatus != .authorized)
            .accessibilityLabel("Take photo to scan")
            .accessibilityHint("Captures the item and estimates its resale value")
            .accessibilitySortPriority(100)
            .padding(.bottom, 28)
            .layoutPriority(2)
        }
        .snapAnimation(.easeInOut(duration: 0.2), value: captureNotice)
    }

    private var banners: some View {
        HaulBanners(session: session, onCamera: true, includeDrafts: false,
                    onUpgrade: showPaywall, onRestore: restore)
            .padding(.horizontal, 16)
    }

    // MARK: - Actions

    private var failedItem: HaulItem? {
        guard let failedItemID else { return nil }
        return session.items.first { $0.id == failedItemID }
    }

    /// Starts at once; stops only when no capture is still being processed,
    /// because stopping the session under one loses that photo without a
    /// trace. `pendingCaptures` reaching zero re-runs this.
    private func updateCamera() {
        if cameraShouldRun {
            if camera.authStatus == .authorized { camera.startSession() }
        } else if camera.pendingCaptures == 0 {
            camera.stopSession()
        }
    }

    /// Finish and Done wait for a photo the shutter has already promised —
    /// up to three seconds, in case a capture never reports back.
    private func request(_ action: CloseAction) {
        guard closing == nil else { return }
        guard camera.pendingCaptures > 0 else {
            perform(action)
            return
        }
        closing = action
        Task {
            try? await Task.sleep(for: .seconds(3))
            if closing == action {
                closing = nil
                perform(action)
            }
        }
    }

    private func perform(_ action: CloseAction) {
        switch action {
        case .finish:
            if session.items.isEmpty {
                session.finishHaul()
                dismiss()
            } else {
                showSummary = true
            }
        case .done:
            session.finishHaul()
            dismiss()
        }
    }

    private func shareCard() {
        guard let card = session.renderShareCard() else { return }
        sheet = .shareCard(card)
    }

    private func showPaywall(_ trigger: PaywallTrigger) {
        paywallShown = true
        sheet = .paywall(trigger)
    }

    private func restore() {
        Task {
            try? await purchaseService.restorePurchases()
            if purchaseService.isSubscribed { session.resumeAfterPurchase() }
        }
    }
}

/// Everything this screen presents as a sheet. One `sheet(item:)`, so two can
/// never be asked for at once.
private enum HaulSheet: Identifiable {
    case paywall(PaywallTrigger)
    case shareCard(UIImage)
    case shareDrafts(String)

    var id: String {
        switch self {
        case .paywall(let trigger): return "paywall-\(trigger.rawValue)"
        case .shareCard:            return "share-card"
        case .shareDrafts:          return "share-drafts"
        }
    }
}

// MARK: - Top bar

private struct HaulTopBar: View {
    let session: HaulSession
    let isFinishing: Bool
    let onFinish: () -> Void

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .center, spacing: 12) {
                HaulTotalLabel(session: session, onCamera: true)
                Spacer(minLength: 8)
                count
                finish
            }
            // Accessibility sizes: the total gets the whole width.
            VStack(alignment: .leading, spacing: 8) {
                HaulTotalLabel(session: session, onCamera: true)
                HStack {
                    count
                    Spacer(minLength: 8)
                    finish
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(Color.snapCharcoal.opacity(0.5))
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .padding(.horizontal, 16)
        .padding(.top, 8)
    }

    /// Every photo taken — the shutter's feedback, so it counts one the
    /// moment it is taken.
    private var count: some View {
        Text("\(session.items.count) items")
            .font(.snapCaption)
            .foregroundStyle(Color.snapOnCharcoal.opacity(0.9))
            // Already inside the total's spoken value, broken down.
            .accessibilityHidden(true)
    }

    private var finish: some View {
        Button(action: onFinish) {
            ZStack {
                // Keeps the button's width while the spinner shows.
                Text("Finish").opacity(isFinishing ? 0 : 1)
                if isFinishing {
                    ProgressView().tint(Color.snapOnAccent)
                }
            }
            .font(.snapCaption.bold())
            .foregroundStyle(Color.snapOnAccent)
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(Color.snapTerracottaFill)
            .clipShape(Capsule())
        }
        .snapHitTarget()
        .accessibilityLabel("Finish")
    }
}

/// The running total, and the one accessibility element that speaks it.
private struct HaulTotalLabel: View {
    let session: HaulSession
    let onCamera: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Estimated total")
                .font(.snapCaption)
                .foregroundStyle(onCamera ? Color.snapOnCharcoal.opacity(0.8) : Color.snapWarmGray)
            HStack(spacing: 8) {
                Text(verbatim: HistoryViewModel.money(session.total))
                    .font(.fraunces(28, weight: .bold, relativeTo: .title))
                    .foregroundStyle(onCamera ? Color.snapOnCharcoal : Color.snapSageText)
                    .lineLimit(1)
                    .minimumScaleFactor(0.6)
                    .contentTransition(.numericText())
                if session.pendingCount > 0 {
                    ProgressView()
                        .controlSize(.small)
                        .tint(onCamera ? Color.snapOnCharcoal : Color.snapWarmGray)
                        .accessibilityHidden(true)
                }
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Estimated total")
        .accessibilityValue(spokenValue)
        .accessibilityAddTraits(.updatesFrequently)
    }

    /// The total and the items it is the total *of* — the valued ones — then
    /// whatever is not in it yet, or will not be. Together they add up to
    /// the photos taken, which is what the camera's visible count shows.
    private var spokenValue: String {
        let money = HistoryViewModel.money(session.total)
        let items = String(localized: "\(session.valuedCount) items")
        var spoken = String(localized: "\(money), \(items)")
        if session.pendingCount > 0 {
            let waiting = String(localized: "\(session.pendingCount) photos still being valued")
            spoken = String(localized: "\(spoken), \(waiting)")
        }
        if session.failedCount > 0 {
            let failed = String(localized: "\(session.failedCount) photos couldn't be valued")
            spoken = String(localized: "\(spoken), \(failed)")
        }
        return spoken
    }
}

// MARK: - Banners

/// Pauses and holds, in the camera and on the summary.
private struct HaulBanners: View {
    let session: HaulSession
    let onCamera: Bool
    /// Drafts only run from the summary, so only the summary shows their hold.
    let includeDrafts: Bool
    let onUpgrade: (PaywallTrigger) -> Void
    let onRestore: () -> Void

    var body: some View {
        VStack(spacing: 8) {
            if let pause = session.pause {
                HaulPauseBanner(pause: pause, onCamera: onCamera,
                                onTryAgain: { session.resumeNow() })
            }
            if let hold = session.hold {
                HaulHoldBanner(hold: hold, forDrafts: false, onCamera: onCamera,
                               onResume: { session.resumeHeld() },
                               onUpgrade: onUpgrade, onRestore: onRestore)
            }
            if includeDrafts, let hold = session.draftHold, hold != session.hold {
                HaulHoldBanner(hold: hold, forDrafts: true, onCamera: onCamera,
                               onResume: { session.resumeHeld() },
                               onUpgrade: onUpgrade, onRestore: onRestore)
            }
        }
    }
}

/// A banner carries sentences and buttons that are read rather than glanced
/// at, so over the camera it sits on a denser scrim than the one-word chrome
/// labels' 50%: a paragraph over a white table at 50% is not legible.
private struct HaulBannerBackground: ViewModifier {
    let onCamera: Bool

    @ViewBuilder
    func body(content: Content) -> some View {
        if onCamera {
            content
                .foregroundStyle(Color.snapOnCharcoal)
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.snapCharcoal.opacity(0.85))
                .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        } else {
            content
                .foregroundStyle(Color.snapEspresso)
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
                .snapCard()
        }
    }
}

/// The capsule is drawn at caption size, about 33pt tall; the hit target
/// around it is the 44pt the rest of the screen's controls have.
private struct HaulBannerButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.snapCaption.bold())
            .foregroundStyle(Color.snapOnAccent)
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(Color.snapTerracottaFill)
            .clipShape(Capsule())
            .opacity(configuration.isPressed ? 0.85 : 1)
            .snapHitTarget()
    }
}

private struct HaulPauseBanner: View {
    let pause: HaulPause
    let onCamera: Bool
    let onTryAgain: () -> Void

    var body: some View {
        // Redrawn once a minute, for the spoken label: the visible countdown
        // ticks by itself.
        TimelineView(.periodic(from: .now, by: 60)) { context in
            VStack(alignment: .leading, spacing: 10) {
                message(at: context.date)
                if case .offline = pause {
                    Button("Try again", action: onTryAgain)
                        .buttonStyle(HaulBannerButtonStyle())
                }
            }
            .modifier(HaulBannerBackground(onCamera: onCamera))
        }
    }

    @ViewBuilder
    private func message(at date: Date) -> some View {
        let text = VStack(alignment: .leading, spacing: 4) {
            switch pause {
            case .rateLimited:
                Text("You've hit the scan limit.")
                    .font(.snapBodyMedium)
            case .offline(_, let message):
                Text(verbatim: message)
                    .font(.snapBodyMedium)
            }
            // The lower bound is clamped: a deadline that has just passed
            // would otherwise make an inverted range, which traps.
            Text("Your photos are kept — resuming in \(Text(timerInterval: min(Date.now, pause.until)...pause.until, countsDown: true))")
                .font(.snapCaption)
                .opacity(0.9)
        }
        .fixedSize(horizontal: false, vertical: true)

        switch pause {
        case .rateLimited(let until):
            // One element, saying what the banner says: the photos are kept
            // and the queue resumes by itself. The wait is rounded up, and
            // redrawn once a minute, so it is never read as shorter than it
            // is.
            text
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(pause.announcement(remaining: until.timeIntervalSince(date)))
        case .offline:
            text.accessibilityElement(children: .combine)
        }
    }
}

private struct HaulHoldBanner: View {
    let hold: HaulHold
    let forDrafts: Bool
    let onCamera: Bool
    let onResume: () -> Void
    let onUpgrade: (PaywallTrigger) -> Void
    let onRestore: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            switch hold {
            case .confirmingSubscription:
                HStack(spacing: 8) {
                    ProgressView()
                        .tint(onCamera ? Color.snapOnCharcoal : Color.snapWarmGray)
                        .accessibilityHidden(true)
                    Text("Confirming your subscription…")
                        .font(.snapBodyMedium)
                }
                .accessibilityElement(children: .combine)
            case .subscriptionUnconfirmed:
                Text("SnapWorth couldn't confirm your Pro subscription yet, so the rest of this haul is on hold. Your photos are kept.")
                    .font(.snapBody)
                HStack(spacing: 8) {
                    Button("Try again", action: onResume)
                    Button("Restore purchases", action: onRestore)
                }
                .buttonStyle(HaulBannerButtonStyle())
            case .notEntitled:
                Text("Haul mode is part of SnapWorth Pro. Your photos are kept for 14 days.")
                    .font(.snapBody)
                upgradeButton
            case .quota(let message):
                Text(verbatim: message)
                    .font(.snapBody)
                upgradeButton
            case .halted(let message):
                Text(verbatim: message)
                    .font(.snapBodyMedium)
                Text("The rest are on hold so they don't run into the same problem.")
                    .font(.snapCaption)
                Button("Try the rest", action: onResume)
                    .buttonStyle(HaulBannerButtonStyle())
            }
        }
        .fixedSize(horizontal: false, vertical: true)
        .modifier(HaulBannerBackground(onCamera: onCamera))
    }

    @ViewBuilder
    private var upgradeButton: some View {
        if let trigger = hold.upgradeTrigger(forDrafts: forDrafts) {
            Button("Upgrade to Pro") { onUpgrade(trigger) }
                .buttonStyle(HaulBannerButtonStyle())
        }
    }
}

// MARK: - Strip

private struct HaulStrip: View {
    let session: HaulSession
    let voiceOverEnabled: Bool
    /// Without camera access the hint to snap each item is a promise the
    /// shutter cannot keep; the placeholder behind says what to do instead.
    let canCapture: Bool
    let onFailedTap: (UUID) -> Void
    let onDelete: (UUID) -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(spacing: 8) {
            if session.failedCount >= 2 {
                Button("Try all again") { session.retryAllFailed() }
                    .buttonStyle(HaulBannerButtonStyle())
            }

            if session.items.isEmpty {
                if canCapture { emptyHint }
            } else {
                strip
            }
        }
    }

    private var emptyHint: some View {
        Text("Snap each item — they're valued as you go.")
            .font(.snapCaption)
            .foregroundStyle(Color.snapOnCharcoal.opacity(0.9))
            .multilineTextAlignment(.center)
            .padding(.horizontal, 12)
            .padding(.vertical, 5)
            .background(Color.snapCharcoal.opacity(0.5))
            .clipShape(Capsule())
            .padding(.horizontal, 20)
    }

    private var strip: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(spacing: 8) {
                    ForEach(session.items) { item in
                        HaulCell(item: item,
                                 onFailedTap: { onFailedTap(item.id) },
                                 onRetry: { session.retry(item.id) },
                                 onRemove: { session.remove(item.id) },
                                 onDelete: { onDelete(item.id) })
                            .id(item.id)
                    }
                }
                .padding(.horizontal, 16)
            }
            // Not under VoiceOver: moving the strip under a user who
            // is reading an earlier cell takes their place away.
            .onChange(of: session.items.count) { old, new in
                guard new > old, !voiceOverEnabled, let last = session.items.last else { return }
                withAnimation(reduceMotion ? nil : .easeOut(duration: 0.25)) {
                    proxy.scrollTo(last.id, anchor: .trailing)
                }
            }
        }
        .frame(height: 88)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Photos in this haul")
    }
}

private struct HaulCell: View {
    let item: HaulItem
    let onFailedTap: () -> Void
    let onRetry: () -> Void
    let onRemove: () -> Void
    let onDelete: () -> Void

    var body: some View {
        Group {
            if item.isFailed {
                Button(action: onFailedTap) { content }
                    .buttonStyle(.plain)
            } else {
                content
            }
        }
        .contextMenu {
            if item.result != nil {
                Button("Delete", role: .destructive, action: onDelete)
            }
            if isQueued {
                Button("Remove photo", role: .destructive, action: onRemove)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(label)
        .accessibilityHint(item.isFailed ? String(localized: "Try again or remove it") : "")
        .accessibilityActions {
            if item.result != nil {
                Button("Delete", action: onDelete)
            }
            if item.isFailed {
                Button("Try again", action: onRetry)
                Button("Remove photo", action: onRemove)
            }
            if isQueued {
                Button("Remove photo", action: onRemove)
            }
        }
    }

    /// Waiting its turn. A duplicate or a blurred shot can go before it is
    /// sent: sending it would spend one of the hour's 20 requests, and save
    /// a find and a scan to the stats only to be deleted. Not while it is
    /// being prepared — `HaulSession.remove` cannot stop that.
    private var isQueued: Bool {
        if case .queued = item.state { return true }
        return false
    }

    private var content: some View {
        VStack(spacing: 4) {
            thumbnail
                .overlay { glyph }
                .overlay(alignment: .topTrailing) { badge }
            Group {
                if let result = item.result {
                    Text(verbatim: HistoryViewModel.money(result.portfolioValue))
                        .font(.dmSans(12, weight: .semibold, relativeTo: .caption))
                        .foregroundStyle(Color.snapOnCharcoal)
                        .lineLimit(1)
                        .minimumScaleFactor(0.7)
                } else {
                    Text(verbatim: " ")
                        .font(.dmSans(12, weight: .semibold, relativeTo: .caption))
                }
            }
            // The cell is a fixed 56pt; the spoken label carries the full value.
            .dynamicTypeSize(...DynamicTypeSize.xLarge)
        }
        .frame(width: 64)
    }

    private var thumbnail: some View {
        Group {
            if let image = item.thumbnail {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFill()
            } else {
                Color.snapOnCharcoal.opacity(0.15)
            }
        }
        .frame(width: 56, height: 56)
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(Color.snapOnCharcoal.opacity(item.isFailed ? 0.9 : 0.3),
                              lineWidth: item.isFailed ? 2 : 1)
        )
    }

    @ViewBuilder
    private var glyph: some View {
        switch item.state {
        case .preparing, .queued:
            Image(systemName: "clock")
                .snapSymbol(16, weight: .semibold)
                .foregroundStyle(Color.snapOnCharcoal)
                .padding(6)
                .background(Color.snapCharcoal.opacity(0.6))
                .clipShape(Circle())
        case .scanning:
            ProgressView()
                .tint(Color.snapOnCharcoal)
                .padding(6)
                .background(Color.snapCharcoal.opacity(0.6))
                .clipShape(Circle())
        case .done, .failed:
            EmptyView()
        }
    }

    @ViewBuilder
    private var badge: some View {
        if item.isFailed {
            Image(systemName: "exclamationmark.circle.fill")
                .snapSymbol(16, weight: .bold)
                .symbolRenderingMode(.palette)
                .foregroundStyle(Color.snapOnAccent, Color.snapTerracottaFill)
                .offset(x: 5, y: -5)
        } else if item.isUnsaved {
            Image(systemName: "exclamationmark.triangle.fill")
                .snapSymbol(14, weight: .bold)
                .symbolRenderingMode(.palette)
                .foregroundStyle(Color.snapCharcoal, Color.snapAmber)
                .offset(x: 5, y: -5)
        }
    }

    private var label: String {
        switch item.state {
        case .preparing, .queued:
            return String(localized: "Waiting to be valued")
        case .scanning:
            return String(localized: "Analyzing item")
        case .done(let result, let saved):
            let value = HistoryViewModel.money(result.portfolioValue)
            let valued = String(localized: "\(result.itemName), estimated \(value)")
            guard !saved else { return valued }
            let warning = String(localized: "Couldn't save to My Finds — this result won't be kept")
            return String(localized: "\(valued), \(warning)")
        case .failed:
            return String(localized: "Couldn't value this photo")
        }
    }
}

// MARK: - Summary

private struct HaulSummaryView: View {
    let session: HaulSession
    let purchaseService: any PurchaseService
    let isClosing: Bool
    let onShare: () -> Void
    let onShareDrafts: () -> Void
    let onUpgrade: (PaywallTrigger) -> Void
    let onRestore: () -> Void
    let onKeepScanning: () -> Void
    let onDone: () -> Void

    /// Moved here when the summary appears: otherwise VoiceOver stays on the
    /// Finish button it covered.
    @AccessibilityFocusState private var headerFocused: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                header

                HaulBanners(session: session, onCamera: false, includeDrafts: true,
                            onUpgrade: onUpgrade, onRestore: onRestore)

                if let top = session.topFind {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("top find")
                            .snapSectionHeader()
                        Text(verbatim: top.itemName)
                            .font(.snapBodyMedium)
                            .foregroundStyle(Color.snapEspresso)
                            .fixedSize(horizontal: false, vertical: true)
                        Text(verbatim: HistoryViewModel.money(top.portfolioValue))
                            .font(.fraunces(22, weight: .bold, relativeTo: .title2))
                            .foregroundStyle(Color.snapSageText)
                    }
                    .padding(20)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .snapCard()
                    .accessibilityElement(children: .combine)
                }

                PrimaryButton(title: "Share haul", action: onShare)
                    .disabled(session.valuedCount == 0)

                HaulDraftsSection(session: session, purchaseService: purchaseService,
                                  onShareDrafts: onShareDrafts, onUpgrade: onUpgrade)

                VStack(spacing: 12) {
                    PrimaryButton(title: "Done", isLoading: isClosing, action: onDone)
                    GhostButton(title: "Keep scanning", action: onKeepScanning)
                }
                .padding(.top, 4)
            }
            .padding(20)
        }
        .background(Color.snapBackground.ignoresSafeArea())
        .accessibilityAddTraits(.isModal)
        .onAppear { session.didReachSummary() }
        .task {
            // After the slide-in: focus asked for while the view is still
            // arriving is not reliably taken.
            try? await Task.sleep(for: .milliseconds(350))
            headerFocused = true
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("This haul")
                .font(.snapHeadline)
                .foregroundStyle(Color.snapEspresso)
                .accessibilityAddTraits(.isHeader)
                .accessibilityFocused($headerFocused)
            HaulTotalLabel(session: session, onCamera: false)
            // The items the total is the total of. Pending and failed photos
            // are in neither, and each has its own line below.
            Text("\(session.valuedCount) items")
                .font(.snapBody)
                .foregroundStyle(Color.snapWarmGray)
                .accessibilityHidden(true)
            if session.pendingCount > 0 {
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(session.pendingCount) photos still being valued")
                        .font(.snapBodyMedium)
                    Text("Close now and they'll carry on the next time you open Haul.")
                        .font(.snapCaption)
                }
                .foregroundStyle(Color.snapEspresso)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityElement(children: .combine)
            }
            if session.failedCount > 0 {
                // Without this line a haul where every photo failed has a
                // total of nothing and Share and Draft greyed out, with no
                // reason given.
                VStack(alignment: .leading, spacing: 8) {
                    Text("\(session.failedCount) photos couldn't be valued")
                        .font(.snapBodyMedium)
                        .foregroundStyle(Color.snapTerracottaText)
                        .fixedSize(horizontal: false, vertical: true)
                    Button("Try all again") { session.retryAllFailed() }
                        .buttonStyle(HaulBannerButtonStyle())
                }
            }
        }
        .padding(.top, 24)
    }
}

private struct HaulDraftsSection: View {
    let session: HaulSession
    let purchaseService: any PurchaseService
    let onShareDrafts: () -> Void
    let onUpgrade: (PaywallTrigger) -> Void

    /// The last marketplace drafted for, as a starting point — never a silent
    /// default: nothing is chosen until the user chooses.
    @AppStorage(HaulSession.lastMarketplaceKey) private var lastMarketplace = ""
    @State private var marketplace: Marketplace?
    @State private var showConfirm = false
    @State private var copiedID: UUID?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Snap → Sell")
                .font(.snapTitle)
                .foregroundStyle(Color.snapEspresso)
                .accessibilityAddTraits(.isHeader)

            Picker("Marketplace", selection: $marketplace) {
                Text("Choose a marketplace").tag(Marketplace?.none)
                ForEach(Marketplace.allCases) { market in
                    Text(verbatim: market.displayName).tag(Marketplace?.some(market))
                }
            }
            .pickerStyle(.menu)
            .tint(Color.snapTerracottaText)
            // Chosen once per haul: drafts already written are in its voice.
            .disabled(session.draftMarketplace != nil)

            PrimaryButton(title: "Draft listings for all") {
                // Re-checked here, not trusted from the entry: a haul can
                // outlive the subscription that opened it.
                guard purchaseService.isSubscribed else {
                    onUpgrade(.snapSell)
                    return
                }
                showConfirm = true
            }
            .disabled(marketplace == nil || session.draftableCount == 0)

            if session.hasWaitingDrafts {
                Button("Stop drafting") { session.stopDrafting() }
                    .font(.snapBodyMedium)
                    .foregroundStyle(Color.snapTerracottaText)
                    .snapHitTarget()
            }

            Text("SnapWorth writes it — you paste & post. We never post for you.")
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)
                .fixedSize(horizontal: false, vertical: true)

            ForEach(session.items.filter { $0.draft != nil }) { item in
                HaulDraftRow(item: item, copied: copiedID == item.id,
                             onCopy: { copy(item) },
                             onRetry: { session.retryDraft(item.id) })
            }

            if session.hasFinishedDrafts {
                Button("Share all drafts", action: onShareDrafts)
                    .font(.snapBodyMedium)
                    .foregroundStyle(Color.snapTerracottaText)
                    .snapHitTarget()
            }

            if let market = session.draftMarketplace {
                Button("Open \(market.displayName)") { market.openSellPage() }
                    .font(.snapBodyMedium)
                    .foregroundStyle(Color.snapTerracottaText)
                    .snapHitTarget()
            }
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .snapCard()
        .onAppear {
            if marketplace == nil {
                marketplace = session.draftMarketplace ?? Marketplace(rawValue: lastMarketplace)
            }
        }
        .confirmationDialog(confirmTitle, isPresented: $showConfirm, titleVisibility: .visible) {
            Button("Draft listings for all") {
                if let marketplace { session.draftAll(for: marketplace) }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Drafts use the same hourly limit as scans. Scanning may pause for up to an hour.")
        }
    }

    /// Assembled from two keys, because one plural key agrees with one number
    /// — see `ios/Localization/README.md`.
    private var confirmTitle: String {
        guard let marketplace else { return "" }
        let listings = String(localized: "\(session.draftableCount) listings")
        return String(localized: "Draft \(listings) for \(marketplace.displayName)?")
    }

    private func copy(_ item: HaulItem) {
        guard case .done(let listing)? = item.draft else { return }
        UIPasteboard.general.string = listing.shareText
        Haptics.light()
        // The button's "Copied" is a silent label swap.
        UIAccessibility.post(notification: .announcement, argument: String(localized: "Copied"))
        copiedID = item.id
        Task {
            try? await Task.sleep(for: .seconds(2))
            if copiedID == item.id { copiedID = nil }
        }
    }
}

private struct HaulDraftRow: View {
    let item: HaulItem
    let copied: Bool
    let onCopy: () -> Void
    let onRetry: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Group {
                if let image = item.thumbnail {
                    Image(uiImage: image).resizable().scaledToFill()
                } else {
                    Color.snapBorder
                }
            }
            .frame(width: 40, height: 40)
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 2) {
                if let result = item.result {
                    Text(verbatim: result.itemName)
                        .font(.snapBodyMedium)
                        .foregroundStyle(Color.snapEspresso)
                        .lineLimit(2)
                }
                status
            }

            Spacer(minLength: 8)

            trailing
        }
    }

    @ViewBuilder
    private var status: some View {
        switch item.draft {
        case .waiting?:
            if item.result == nil {
                Text("Waiting to be valued")
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)
            } else {
                HStack(spacing: 6) {
                    Image(systemName: "clock")
                        .snapSymbol(13)
                        .accessibilityHidden(true)
                    Text("Waiting to be drafted")
                        .font(.snapCaption)
                }
                .foregroundStyle(Color.snapWarmGray)
            }
        case .drafting?:
            HStack(spacing: 6) {
                ProgressView().controlSize(.small).accessibilityHidden(true)
                Text("Writing your listing…")
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)
            }
        case .failed(let reason)?:
            VStack(alignment: .leading, spacing: 2) {
                Text("Couldn't draft this one")
                    .foregroundStyle(Color.snapTerracottaText)
                // Why, which the line above cannot say: a refused photo and
                // an outage call for different things.
                Text(verbatim: reason)
                    .foregroundStyle(Color.snapWarmGray)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .font(.snapCaption)
        case .done?, nil:
            EmptyView()
        }
    }

    @ViewBuilder
    private var trailing: some View {
        switch item.draft {
        case .done?:
            Button(action: onCopy) {
                Text(copied ? String(localized: "Copied") : String(localized: "Copy"))
            }
            .buttonStyle(HaulBannerButtonStyle())
            // Found by the rotor as one of a list of buttons, so it names
            // its item.
            .accessibilityLabel(copyLabel)
        case .failed?:
            Button("Try again", action: onRetry)
                .buttonStyle(HaulBannerButtonStyle())
        default:
            EmptyView()
        }
    }

    private var copyLabel: String {
        guard let name = item.result?.itemName else { return String(localized: "Copy") }
        return String(localized: "Copy listing for \(name)")
    }
}
