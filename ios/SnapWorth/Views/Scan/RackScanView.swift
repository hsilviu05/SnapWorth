import PhotosUI
import SwiftUI

// ═══════════════════════════════════════════════════════════════════
// MARK: - Rack scan (#89)
// ═══════════════════════════════════════════════════════════════════
//
// One photo of a rail, shelf or bin; up to three numbered pins on the items
// most worth picking up, each with a rough range and why it stands out.
// "Scan this one" closes the rack and goes back to the Scan camera with the
// item named, so the precise estimate comes from a proper photo of it — a
// crop of a rack photo is a few hundred pixels of a sleeve, too little to
// read a label from.

/// The way into Rack scan, beside Haul above the shutter.
struct RackEntryPill: View {
    let isPro: Bool
    var compactBadge = false
    let isDisabled: Bool
    let action: () -> Void

    var body: some View {
        Button {
            Haptics.selection()
            action()
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "rectangle.split.3x1.fill")
                    .snapSymbol(13, weight: .semibold, relativeTo: .footnote)
                    .accessibilityHidden(true)
                Text("Rack")
                if !isPro {
                    EntryProBadge(compact: compactBadge)
                }
            }
            .lineLimit(1)
            .font(.snapCaption.bold())
            .foregroundStyle(Color.snapOnCharcoal.opacity(isDisabled ? 0.4 : 0.95))
            .padding(.horizontal, 14)
            .padding(.vertical, 6)
            .background(Color.snapCharcoal.opacity(0.5))
            .clipShape(Capsule())
        }
        .disabled(isDisabled)
        .snapHitTarget()
        .accessibilityElement(children: .combine)
        .accessibilityHint(String(localized: "One photo of a rail or shelf, and the items most worth a closer look"))
    }
}

/// What the Scan camera shows after "Scan this one": which item to photograph.
struct RackTargetBanner: View {
    let candidate: RackCandidate
    let onDismiss: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Now scan: \(candidate.name)")
                    .font(.snapCaption.bold())
                if !candidate.position.isEmpty {
                    Text(verbatim: candidate.position)
                        .font(.snapCaption)
                        .opacity(0.85)
                }
            }
            .fixedSize(horizontal: false, vertical: true)
            Button(action: onDismiss) {
                Image(systemName: "xmark")
                    .snapSymbol(11, weight: .bold)
            }
            .snapHitTarget()
            .accessibilityLabel(String(localized: "Dismiss"))
        }
        .foregroundStyle(Color.snapOnCharcoal)
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(Color.snapCharcoal.opacity(0.7))
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .padding(.horizontal, 20)
    }
}

struct RackScanFlow: View {
    /// "Scan this one": the rack closes and the Scan camera names the item.
    let onScanThis: (RackCandidate) -> Void
    let onClose: () -> Void

    @StateObject private var camera = CameraManager()
    @State private var phase: Phase = .capturing
    @State private var pickedItem: PhotosPickerItem?
    @State private var pickFailed = false
    @State private var selected: Int?
    @State private var task: Task<Void, Never>?

    enum Phase {
        case capturing
        case reading(UIImage)
        case done(UIImage, [RackCandidate])
        case failed(UIImage, String)

        var isCapturing: Bool { if case .capturing = self { return true } else { return false } }
    }

    var body: some View {
        ZStack {
            Color.snapCharcoal.ignoresSafeArea()
            switch phase {
            case .capturing:
                capture
            case .reading(let photo):
                reading(photo)
            case .done(let photo, let candidates):
                done(photo, candidates)
            case .failed(let photo, let message):
                failed(photo, message)
            }
        }
        .onAppear {
            Haptics.prepare()
            camera.onPhoto = { image in read(image) }
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
                read(image)
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
                }
                .padding(.horizontal, 20)
                .padding(.top, 12)

                Spacer()

                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .strokeBorder(Color.snapOnCharcoal.opacity(0.5), lineWidth: 2)
                    .frame(width: 330, height: 420)
                    .accessibilityHidden(true)

                Text("Fit the whole rail in the frame")
                    .font(.snapBody)
                    .foregroundStyle(Color.snapOnCharcoal)
                    .padding(.top, 16)
                    .accessibilityAddTraits(.isHeader)
                Text("One photo of a rail, shelf or bin. We'll point out up to three items worth a closer look.")
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapOnCharcoal.opacity(0.75))
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 40)
                    .padding(.top, 4)

                Spacer()

                HStack(alignment: .center) {
                    PhotosPicker(selection: $pickedItem, matching: .images) {
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .fill(Color.snapOnCharcoal.opacity(0.2))
                            .frame(width: 52, height: 52)
                            .overlay {
                                if pickedItem != nil {
                                    ProgressView().tint(Color.snapOnCharcoal)
                                } else {
                                    Image(systemName: "photo.on.rectangle")
                                        .snapSymbol(22, weight: .light)
                                        .foregroundStyle(Color.snapOnCharcoal)
                                }
                            }
                    }
                    .snapHitTarget()
                    .accessibilityLabel(String(localized: "Choose a rack photo from your library"))

                    Spacer()

                    let canShoot = camera.authStatus == .authorized && pickedItem == nil
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
                    .accessibilityLabel(String(localized: "Take the rack photo"))

                    Spacer()

                    Color.clear.frame(width: 52, height: 52).accessibilityHidden(true)
                }
                .padding(.horizontal, 36)
                .padding(.bottom, 44)
            }
        }
    }

    // MARK: Reading

    private func read(_ image: UIImage) {
        guard phase.isCapturing else { return }
        camera.stopSession()
        phase = .reading(image)
        selected = nil
        task = Task {
            do {
                let response = try await ScanAPIClient.shared.rackScan(image: image)
                guard !Task.isCancelled else { return }
                let candidates = Array(response.candidates.prefix(3))
                Analytics.shared.track(.rackScanCompleted(
                    candidates: RackGeometry.countBucket(candidates.count)))
                Haptics.success()
                phase = .done(image, candidates)
                UIAccessibility.post(notification: .announcement,
                                     argument: Self.announcement(count: candidates.count))
            } catch {
                guard !Task.isCancelled else { return }
                phase = .failed(image, AppError.from(error).errorDescription
                                ?? String(localized: "Something went wrong. Please try again."))
            }
        }
    }

    static func announcement(count: Int) -> String {
        count == 0
            ? String(localized: "Nothing stood out in this photo.")
            : String(localized: "\(count) items worth a closer look.")
    }

    private func reading(_ photo: UIImage) -> some View {
        ZStack {
            Image(uiImage: photo)
                .resizable()
                .scaledToFit()
                .ignoresSafeArea()
            Color.snapCharcoal.opacity(0.6).ignoresSafeArea()
            VStack(spacing: 16) {
                ProgressView().tint(Color.snapOnCharcoal).controlSize(.large)
                Text("Looking along the rail…")
                    .font(.snapBody)
                    .foregroundStyle(Color.snapOnCharcoal)
            }
            .accessibilityElement(children: .combine)
        }
    }

    private func retake() {
        phase = .capturing
        selected = nil
        camera.startSession()
    }

    // MARK: Done

    private func done(_ photo: UIImage, _ candidates: [RackCandidate]) -> some View {
        VStack(spacing: 0) {
            HStack {
                Text("Rack scan")
                    .font(.snapTitle)
                    .foregroundStyle(Color.snapEspresso)
                    .accessibilityAddTraits(.isHeader)
                Spacer()
                Button("Done") { onClose() }
                    .font(.dmSans(15, weight: .semibold))
                    .foregroundStyle(Color.snapTerracottaText)
                    .snapHitTarget()
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)

            ScrollViewReader { proxy in
                ScrollView {
                    VStack(spacing: 16) {
                        RackPinnedPhoto(photo: photo, candidates: candidates, selected: selected) { rank in
                            selected = rank
                            withAnimation { proxy.scrollTo(rank, anchor: .top) }
                        }
                        .padding(.horizontal, 20)

                        if candidates.isEmpty {
                            emptyState
                        } else {
                            ForEach(candidates) { candidate in
                                RackCandidateCard(candidate: candidate,
                                                  isSelected: selected == candidate.rank) {
                                    Analytics.shared.track(.rackCandidateChosen(
                                        rank: String(candidate.rank)))
                                    onScanThis(candidate)
                                }
                                .id(candidate.rank)
                                .padding(.horizontal, 20)
                            }
                            Text("Ranges are rough, judged from a distance. Scan an item for a real estimate.")
                                .font(.snapCaption)
                                .foregroundStyle(Color.snapWarmGray)
                                .multilineTextAlignment(.center)
                                .padding(.horizontal, 28)
                        }

                        Button("Take another photo") { retake() }
                            .font(.dmSans(15, weight: .semibold))
                            .foregroundStyle(Color.snapTerracottaText)
                            .snapHitTarget()
                            .padding(.bottom, 32)
                    }
                    .padding(.top, 4)
                }
            }
        }
        .background(Color.snapBackground.ignoresSafeArea())
    }

    private var emptyState: some View {
        VStack(spacing: 6) {
            Text("Nothing stood out in this photo.")
                .font(.dmSans(16, weight: .semibold))
                .foregroundStyle(Color.snapEspresso)
            Text("Try closer, in better light, or a different rail. An empty answer is better than a guess.")
                .font(.snapBody)
                .foregroundStyle(Color.snapWarmGray)
                .multilineTextAlignment(.center)
        }
        .padding(.horizontal, 28)
        .accessibilityElement(children: .combine)
    }

    // MARK: Failed

    private func failed(_ photo: UIImage, _ message: String) -> some View {
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
            PrimaryButton(title: "Try again") {
                phase = .capturing
                read(photo)
            }
            .padding(.horizontal, 40)
            Button("Take another photo") { retake() }
                .font(.dmSans(15, weight: .semibold))
                .foregroundStyle(Color.snapOnCharcoal)
                .snapHitTarget()
            Button("Cancel") { onClose() }
                .font(.dmSans(15, weight: .semibold))
                .foregroundStyle(Color.snapOnCharcoal.opacity(0.8))
                .snapHitTarget()
        }
    }
}

/// The rack photo with a numbered pin on each candidate's box.
struct RackPinnedPhoto: View {
    let photo: UIImage
    let candidates: [RackCandidate]
    let selected: Int?
    let onSelect: (Int) -> Void

    var body: some View {
        let aspect = photo.size.height > 0 ? photo.size.width / photo.size.height : 0.75
        Image(uiImage: photo)
            .resizable()
            .aspectRatio(aspect, contentMode: .fit)
            .overlay {
                GeometryReader { geo in
                    let frame = CGRect(origin: .zero, size: geo.size)
                    ForEach(candidates) { candidate in
                        let rect = candidate.box.rect(in: frame)
                        let isSelected = selected == candidate.rank
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .strokeBorder(Color.snapTerracotta, lineWidth: isSelected ? 4 : 2)
                            .background(Color.snapTerracotta.opacity(isSelected ? 0.15 : 0.05)
                                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous)))
                            .frame(width: rect.width, height: rect.height)
                            .position(x: rect.midX, y: rect.midY)
                            .accessibilityHidden(true)
                        Button { onSelect(candidate.rank) } label: {
                            Text(verbatim: "\(candidate.rank)")
                                .font(.dmSans(15, weight: .bold))
                                .foregroundStyle(Color.snapOnAccent)
                                .frame(width: 30, height: 30)
                                .background(Circle().fill(Color.snapTerracottaFill))
                                .overlay(Circle().strokeBorder(Color.snapOnAccent, lineWidth: 2))
                        }
                        .snapHitTarget()
                        .position(x: min(max(rect.minX + 15, 15), geo.size.width - 15),
                                  y: min(max(rect.minY + 15, 15), geo.size.height - 15))
                        .accessibilityLabel(RackCandidateCard.spoken(candidate))
                        .accessibilityHint(String(localized: "Shows this item below"))
                    }
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
            .accessibilityElement(children: .contain)
            .accessibilityLabel(String(localized: "Rack photo"))
    }
}

struct RackCandidateCard: View {
    let candidate: RackCandidate
    let isSelected: Bool
    let onScanThis: () -> Void

    /// What VoiceOver reads for a pin, and for the card it stands for.
    static func spoken(_ c: RackCandidate) -> String {
        c.position.isEmpty
            ? String(localized: "Pick \(c.rank): \(c.name). Rough range \(c.formattedRange).")
            : String(localized: "Pick \(c.rank): \(c.name), \(c.position). Rough range \(c.formattedRange).")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 12) {
                Text(verbatim: "\(candidate.rank)")
                    .font(.dmSans(15, weight: .bold))
                    .foregroundStyle(Color.snapOnAccent)
                    .frame(width: 30, height: 30)
                    .background(Circle().fill(Color.snapTerracottaFill))
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 3) {
                    // The model's words, in English: a name and a place on a
                    // rail that no fixed sentence could say.
                    Text(verbatim: candidate.name)
                        .font(.dmSans(16, weight: .semibold))
                        .foregroundStyle(Color.snapEspresso)
                    if let brand = candidate.shownBrand {
                        Text(verbatim: brand)
                            .font(.snapCaption)
                            .foregroundStyle(Color.snapWarmGray)
                    }
                    if !candidate.position.isEmpty {
                        Label {
                            Text(verbatim: candidate.position)
                        } icon: {
                            Image(systemName: "mappin")
                        }
                        .font(.snapCaption)
                        .foregroundStyle(Color.snapWarmGray)
                    }
                }
                .fixedSize(horizontal: false, vertical: true)
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(Self.spoken(candidate))

            HStack(spacing: 6) {
                Text(verbatim: candidate.formattedRange)
                    .font(.dmSans(17, weight: .bold))
                    .foregroundStyle(Color.snapEspresso)
                Text("rough")
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)
            }
            .accessibilityHidden(true)

            if !candidate.why.isEmpty {
                Text(verbatim: candidate.why)
                    .font(.snapBody)
                    .foregroundStyle(Color.snapWarmGray)
                    .fixedSize(horizontal: false, vertical: true)
            }

            PrimaryButton(title: "Scan this one", action: onScanThis)
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 24, style: .continuous)
            .strokeBorder(Color.snapTerracotta, lineWidth: isSelected ? 2 : 0))
        .shadow(color: Color.snapCardShadow.opacity(0.08), radius: 24, x: 0, y: 8)
    }
}

/// Rack scan's cover on the Scan tab, as a modifier for the same reason as
/// `HaulPresentation`: `ScanView.body` is at the type-checker's budget.
struct RackPresentation: ViewModifier {
    @Binding var isPresented: Bool
    let onScanThis: (RackCandidate) -> Void

    func body(content: Content) -> some View {
        content.fullScreenCover(isPresented: $isPresented) {
            RackScanFlow(onScanThis: { candidate in
                isPresented = false
                onScanThis(candidate)
            }, onClose: { isPresented = false })
        }
    }
}
