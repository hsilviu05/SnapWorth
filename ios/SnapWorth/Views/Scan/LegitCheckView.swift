import PhotosUI
import SwiftUI

// ═══════════════════════════════════════════════════════════════════
// MARK: - Legit check (#90)
// ═══════════════════════════════════════════════════════════════════
//
// The card on a result, the guided camera it opens, and the read that comes
// back. What each verdict says, and the rule that none of it is a
// certification, are `LegitVerdict`'s; this file only lays them out.

/// "Legit check" on a result: offered when the brand is one commonly faked
/// (`LegitBrands`), and kept when the find already carries a read whatever
/// its brand, so a read is never hidden by a later rename.
struct LegitCheckCard: View {
    let result: ScanResult
    let isPro: Bool
    /// The sheet presents `LegitCheckFlow`.
    let onStart: () -> Void
    let onPaywall: (PaywallTrigger) -> Void

    /// Whether the sheet shows this card at all.
    static func isOffered(for result: ScanResult) -> Bool {
        result.legitRead != nil || LegitBrands.isCommonlyFaked(result.brand)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Legit check")
                    .snapSectionHeader()
                Spacer()
                if !isPro { ProBadge() }
            }

            if let read = result.legitRead {
                LegitReadSummary(read: read)
                PrimaryButton(title: "Check again") { start() }
            } else {
                Text("Photograph the logo, the inside label, the stitching and the hardware, and we'll look for the usual signs of a replica.")
                    .font(.snapBody)
                    .foregroundStyle(Color.snapWarmGray)
                    .fixedSize(horizontal: false, vertical: true)
                PrimaryButton(title: "Start a legit check") { start() }
            }
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .shadow(color: Color.snapCardShadow.opacity(0.08), radius: 24, x: 0, y: 8)
    }

    private func start() {
        if isPro { onStart() } else { onPaywall(.legitCheck) }
    }
}

/// A read, compact: the verdict, the reasons behind it, and the disclaimer.
/// Used on the result card and at the end of the flow.
struct LegitReadSummary: View {
    let read: LegitRead
    var showsDate = true

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: read.verdict.systemImage)
                    .foregroundStyle(read.verdict.tint)
                    .accessibilityHidden(true)
                Text(read.verdict.title)
                    .font(.dmSans(16, weight: .semibold))
                    .foregroundStyle(Color.snapEspresso)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.isHeader)

            Text(read.verdict.explanation)
                .font(.snapBody)
                .foregroundStyle(Color.snapWarmGray)
                .fixedSize(horizontal: false, vertical: true)

            if !read.reasons.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    // The model's own words, in English. A reason names one
                    // detail of one photo, and no fixed sentence could.
                    ForEach(read.reasons, id: \.self) { reason in
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text(verbatim: "•")
                                .foregroundStyle(Color.snapWarmGray)
                                .accessibilityHidden(true)
                            Text(verbatim: reason)
                                .font(.snapBody)
                                .foregroundStyle(Color.snapEspresso)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }

            if !read.unclearPhotos.isEmpty {
                let names = read.unclearPhotos.map(\.title).formatted(.list(type: .and))
                Text("Too unclear to judge: \(names)")
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)
                    .fixedSize(horizontal: false, vertical: true)
            }

            VStack(alignment: .leading, spacing: 2) {
                Text(LegitVerdict.disclaimer)
                if showsDate {
                    Text("Checked \(read.checkedAt.formatted(date: .abbreviated, time: .omitted))")
                }
            }
            .font(.snapCaption)
            .foregroundStyle(Color.snapWarmGray)
            .fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// The guided camera: one step per `LegitPhotoSlot`, then the check.
///
/// Every step can be skipped, because not every item has all four (a tote
/// has no zipper), but the check needs `LegitPhotoSlot.minimumPhotos`. Once
/// it has them, "Check now" ends early.
struct LegitCheckFlow: View {
    let result: ScanResult
    /// The read arrived. Called once, before the user has seen it, so that
    /// dismissing the sheet straight away does not lose it.
    let onRead: (LegitRead) -> Void
    let onClose: () -> Void

    @StateObject private var camera = CameraManager()
    @State private var step = 0
    @State private var photos: [LegitPhotoSlot: UIImage] = [:]
    @State private var phase: Phase = .capturing
    @State private var pickedItem: PhotosPickerItem?
    @State private var pickFailed = false
    @State private var task: Task<Void, Never>?

    enum Phase: Equatable {
        case capturing
        case checking
        case done(LegitRead)
        case failed(String)
    }

    private var slots: [LegitPhotoSlot] { LegitPhotoSlot.allCases }
    private var slot: LegitPhotoSlot { slots[min(step, slots.count - 1)] }
    private var canCheck: Bool { photos.count >= LegitPhotoSlot.minimumPhotos }
    private var isLoadingPick: Bool { pickedItem != nil }

    var body: some View {
        ZStack {
            Color.snapCharcoal.ignoresSafeArea()
            switch phase {
            case .capturing:
                capture
            case .checking:
                checking
            case .done(let read):
                done(read)
            case .failed(let message):
                failed(message)
            }
        }
        .onAppear {
            Haptics.prepare()
            // Every capture is delivered once, in order: a fast second tap
            // must not overwrite the first in a single published slot.
            camera.onPhoto = { image in accept(image) }
            camera.requestPermissionAndSetup()
        }
        .onDisappear {
            camera.stopSession()
            task?.cancel()
        }
        .task(id: pickedItem) {
            guard let item = pickedItem else { return }
            let data = try? await item.loadTransferable(type: Data.self)
            guard !Task.isCancelled else { return }
            if let data, let image = await ScanAPIClient.decodedForScan(data) {
                accept(image)
            } else {
                pickFailed = true
            }
            pickedItem = nil
        }
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

    // MARK: Capturing

    private var capture: some View {
        ZStack {
            switch camera.authStatus {
            case .authorized:
                CameraPreview(session: camera.session).ignoresSafeArea()
            case .notDetermined:
                EmptyView()
            case .restricted:
                CameraPermissionPlaceholder(restricted: true)
            default:
                CameraPermissionPlaceholder(restricted: false)
            }

            VStack(spacing: 0) {
                HStack {
                    Button("Cancel") { onClose() }
                        .font(.dmSans(15, weight: .semibold))
                        .foregroundStyle(Color.snapOnCharcoal)
                        .snapHitTarget()
                    Spacer()
                    Text("Step \(step + 1) of \(slots.count)")
                        .font(.snapCaption)
                        .foregroundStyle(Color.snapOnCharcoal.opacity(0.8))
                }
                .padding(.horizontal, 20)
                .padding(.top, 12)

                stepDots.padding(.top, 10)

                Spacer()

                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .strokeBorder(Color.snapOnCharcoal.opacity(0.6), lineWidth: 2)
                    .frame(width: slot.guideSize.width, height: slot.guideSize.height)
                    .overlay {
                        Image(systemName: slot.systemImage)
                            .snapSymbol(44, weight: .ultraLight)
                            .foregroundStyle(Color.snapOnCharcoal.opacity(0.25))
                    }
                    .accessibilityHidden(true)

                Text(slot.title)
                    .font(.snapBody)
                    .foregroundStyle(Color.snapOnCharcoal)
                    .padding(.top, 16)
                    .accessibilityAddTraits(.isHeader)
                Text(slot.instruction)
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapOnCharcoal.opacity(0.75))
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 40)
                    .padding(.top, 4)

                Spacer()

                HStack(spacing: 24) {
                    Button(step == slots.count - 1 ? "Skip and check" : "Skip") { skip() }
                        .font(.dmSans(15, weight: .semibold))
                        .foregroundStyle(Color.snapOnCharcoal)
                        .snapHitTarget()
                        .disabled(step == slots.count - 1 && !canCheck)
                        .opacity(step == slots.count - 1 && !canCheck ? 0.4 : 1)
                    if canCheck && step < slots.count - 1 {
                        Button("Check now") { startCheck() }
                            .font(.dmSans(15, weight: .semibold))
                            .foregroundStyle(Color.snapOnCharcoal)
                            .snapHitTarget()
                    }
                }
                .padding(.bottom, 12)

                HStack(alignment: .center) {
                    PhotosPicker(selection: $pickedItem, matching: .images) {
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .fill(Color.snapOnCharcoal.opacity(0.2))
                            .frame(width: 52, height: 52)
                            .overlay {
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
                    .accessibilityLabel(String(localized: "Choose this photo from your library"))

                    Spacer()

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
                    .accessibilityLabel(String(localized: "Take this photo"))
                    .accessibilityValue(slot.title)

                    Spacer()

                    // The last photo taken, so the step before can be seen
                    // to have landed.
                    Group {
                        if let last = lastPhoto {
                            Image(uiImage: last)
                                .resizable()
                                .scaledToFill()
                        } else {
                            Color.clear
                        }
                    }
                    .frame(width: 52, height: 52)
                    .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                    .accessibilityHidden(true)
                }
                .padding(.horizontal, 36)
                .padding(.bottom, 44)
            }
        }
    }

    private var lastPhoto: UIImage? {
        slots[..<min(step, slots.count)].reversed().lazy.compactMap { photos[$0] }.first
    }

    private var stepDots: some View {
        HStack(spacing: 6) {
            ForEach(Array(slots.enumerated()), id: \.offset) { index, slot in
                Capsule()
                    .fill(photos[slot] != nil
                          ? Color.snapOnCharcoal
                          : Color.snapOnCharcoal.opacity(index == step ? 0.6 : 0.25))
                    .frame(width: index == step ? 22 : 8, height: 6)
            }
        }
        .accessibilityElement()
        .accessibilityLabel(String(localized: "\(photos.count) of \(slots.count) photos taken"))
    }

    private func accept(_ image: UIImage) {
        guard phase == .capturing, step < slots.count else { return }
        photos[slots[step]] = image
        advance()
    }

    private func skip() {
        guard phase == .capturing else { return }
        advance()
    }

    private func advance() {
        if step < slots.count - 1 {
            step += 1
        } else if canCheck {
            startCheck()
        }
    }

    // MARK: Checking

    private var checking: some View {
        VStack(spacing: 16) {
            ProgressView().tint(Color.snapOnCharcoal).controlSize(.large)
            Text("Checking the details…")
                .font(.snapBody)
                .foregroundStyle(Color.snapOnCharcoal)
        }
        .accessibilityElement(children: .combine)
    }

    private func startCheck() {
        guard canCheck else { return }
        camera.stopSession()
        phase = .checking
        let sent = slots.compactMap { slot in photos[slot].map { (slot, $0) } }
        let brand = result.brand, name = result.itemName, category = result.category
        task = Task {
            do {
                let response = try await ScanAPIClient.shared.legitCheck(
                    photos: sent, brand: brand, itemName: name, category: category)
                guard !Task.isCancelled else { return }
                let read = LegitRead(response: response)
                Analytics.shared.track(.legitCheckCompleted(verdict: read.verdict))
                onRead(read)
                Haptics.success()
                phase = .done(read)
                UIAccessibility.post(notification: .announcement, argument: read.verdict.title)
            } catch {
                guard !Task.isCancelled else { return }
                phase = .failed(Self.message(for: AppError.from(error)))
            }
        }
    }

    /// The error's own words, except a rate limit's: "You've hit the scan
    /// limit" is wrong here, because checks have their own hourly bucket.
    static func message(for error: AppError) -> String {
        if case .rateLimit = error {
            return String(localized: "You've run a lot of legit checks this hour. Try again later.")
        }
        return error.errorDescription ?? String(localized: "Something went wrong. Please try again.")
    }

    // MARK: Done / failed

    private func done(_ read: LegitRead) -> some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Legit check")
                        .font(.snapTitle)
                        .foregroundStyle(Color.snapEspresso)
                    LegitReadSummary(read: read, showsDate: false)
                }
                .padding(24)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            PrimaryButton(title: "Done") { onClose() }
                .padding(.horizontal, 24)
                .padding(.bottom, 24)
        }
        .background(Color.snapBackground.ignoresSafeArea())
    }

    private func failed(_ message: String) -> some View {
        VStack(spacing: 20) {
            Image(systemName: "exclamationmark.triangle")
                .snapSymbol(36, weight: .light)
                .foregroundStyle(Color.snapOnCharcoal)
                .accessibilityHidden(true)
            Text(message)
                .font(.snapBody)
                .foregroundStyle(Color.snapOnCharcoal)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
            PrimaryButton(title: "Try again") { startCheck() }
                .padding(.horizontal, 40)
            Button("Cancel") { onClose() }
                .font(.dmSans(15, weight: .semibold))
                .foregroundStyle(Color.snapOnCharcoal)
                .snapHitTarget()
        }
    }
}
