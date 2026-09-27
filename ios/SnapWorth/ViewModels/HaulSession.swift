import Network
import SwiftUI
import UIKit
import os

// ═══════════════════════════════════════════════════════════════════
// MARK: - Haul mode (#93)
// ═══════════════════════════════════════════════════════════════════
//
// A reseller back from a sourcing trip has a table of items. Haul keeps the
// camera live: each photo is queued and valued in the background while the
// next one is taken, with a running total at the top.
//
// Every photo is a normal scan — the same client call, quota, stats and
// history save as the Scan tab. What this file adds is the part a single
// scan never needed: a queue that respects a 20-requests-an-hour limit shared
// with drafts and trends, and that never loses a photo to it.

// MARK: - Scheduling

/// Which photos are waiting, which are in flight, and whether anything may be
/// sent. Pure, so every rule is tested without a server or a clock.
///
/// Used twice by `HaulSession`: scans, two at a time, and listing drafts, one
/// at a time.
struct HaulQueue<ID: Hashable> {
    let maxInFlight: Int
    /// In capture order.
    private(set) var waiting: [ID] = []
    private(set) var inFlight: Set<ID> = []
    private(set) var pausedUntil: Date?
    /// Released only by `unhold()`, never by the clock.
    private(set) var isHeld = false
    /// One in flight until a request succeeds. Set by any pause.
    ///
    /// `Retry-After` is the time until *one* slot frees — the backend's Lua
    /// script returns oldest + window − now. Resuming two at once would
    /// guarantee a second 429, and a refused request still takes a slot in
    /// the shared IP bucket, which is checked before the device's.
    private(set) var isProbing = false
    /// Where each id sorts: its rank, then the order it was first enqueued
    /// in, so two photos taken in the same millisecond keep their order
    /// through any number of requeues.
    private var order: [ID: (rank: Int, sequence: Int)] = [:]
    private var nextRank = 0
    private var nextSequence = 0
    /// Bumped by every pause and hold, and stamped on each claim, so a
    /// success can say whether its request was sent after the latest one.
    ///
    /// At the limit the usual order is: one request takes the 20th slot and
    /// spends seconds in the model, the other gets an immediate 429. The
    /// admitted one's success arrives *after* the 429 and says nothing about
    /// the budget now — ending the probe on it sends two into a window that
    /// has one slot.
    private var epoch = 0
    private var claimedIn: [ID: Int] = [:]

    init(maxInFlight: Int) {
        self.maxInFlight = max(1, maxInFlight)
    }

    /// Adds `id` to the waiting list at its place in capture order.
    ///
    /// `rank` is where it sorts; nil takes the next number after everything
    /// seen so far. `HaulSession` passes the capture time in milliseconds, so
    /// a photo restored from an earlier launch sorts ahead of one taken in
    /// this one even when the restore finishes second.
    mutating func enqueue(_ id: ID, rank: Int? = nil) {
        let existing = order[id]
        let position = rank ?? existing?.rank ?? nextRank
        let sequence = existing?.sequence ?? nextSequence
        if existing == nil { nextSequence += 1 }
        order[id] = (position, sequence)
        nextRank = max(nextRank, position + 1)
        insertWaiting(id)
    }

    /// Back into `waiting` at its capture position — a photo that was sent
    /// and has to be sent again goes before anything captured after it.
    mutating func requeue(_ id: ID) {
        enqueue(id)
    }

    /// Drops a waiting id. An id in flight leaves through `release`.
    mutating func remove(_ id: ID) {
        waiting.removeAll { $0 == id }
        if !inFlight.contains(id) { order[id] = nil }
    }

    /// Takes as many waiting ids as may be sent now, and marks them in flight.
    /// Nothing while held or paused; one at a time while probing.
    mutating func claim(now: Date) -> [ID] {
        if let until = pausedUntil {
            guard now >= until else { return [] }
            pausedUntil = nil
        }
        guard !isHeld else { return [] }
        let limit = isProbing ? 1 : maxInFlight
        var claimed: [ID] = []
        while inFlight.count < limit,
              let index = waiting.firstIndex(where: { !inFlight.contains($0) }) {
            let id = waiting.remove(at: index)
            inFlight.insert(id)
            claimedIn[id] = epoch
            claimed.append(id)
        }
        return claimed
    }

    mutating func release(_ id: ID) {
        inFlight.remove(id)
        claimedIn[id] = nil
    }

    /// Whether `id`, in flight, was claimed after the latest pause or hold —
    /// whether its success says anything about how things stand now.
    func isCurrent(_ id: ID) -> Bool {
        claimedIn[id] == epoch
    }

    /// A request went through: the budget is there, so stop probing.
    mutating func succeeded() {
        isProbing = false
    }

    /// Pauses until `until`, or later if already paused later: a 429 from the
    /// other request in flight can arrive second and carry a later deadline.
    mutating func pause(until: Date) {
        pausedUntil = max(pausedUntil ?? until, until)
        isProbing = true
        epoch += 1
    }

    /// Ends a pause early. Only ever for an *offline* pause — a rate-limit
    /// deadline is the server's, and cutting it short buys another 429.
    mutating func clearPause() {
        pausedUntil = nil
    }

    /// One in flight until the next success, without a pause.
    mutating func beginProbing() {
        isProbing = true
    }

    mutating func hold() {
        isHeld = true
        epoch += 1
    }
    mutating func unhold() { isHeld = false }

    var isIdle: Bool { waiting.isEmpty && inFlight.isEmpty }

    private mutating func insertWaiting(_ id: ID) {
        guard !waiting.contains(id), let key = order[id] else { return }
        let index = waiting.firstIndex { other in
            guard let theirs = order[other] else { return false }
            return (theirs.rank, theirs.sequence) > (key.rank, key.sequence)
        } ?? waiting.endIndex
        waiting.insert(id, at: index)
    }
}

// MARK: - What a failure means for the rest of the haul

/// What one failed request does to the queue.
///
/// Errors about *the photo* fail that photo. Errors about the device, the
/// network, the service or the entitlement say nothing about the photo — the
/// next one would fail the same way — so they pause or hold the whole queue
/// and put the photo back.
///
/// **Invariant:** every requeue comes with a pause or a hold, so no photo is
/// sent twice in the same turn.
enum HaulDisposition: Equatable {
    /// 429: requeue, pause both queues until the deadline, then probe.
    case rateLimited(TimeInterval)
    /// Nothing reached the server: requeue, pause both with a backoff, probe.
    /// Not counted as an attempt, so a Wi-Fi drop turns no cells red.
    case offline(TimeInterval)
    /// 402: requeue and hold — see `HaulSession.entitlementRefused`.
    case entitlement
    /// Requeue and hold until "Try the rest".
    case halt
    /// The breaker: this photo got the same answer as the one before it.
    /// It fails — the server has given its verdict on it — and the rest are
    /// held. Put back instead, "Try the rest" would send it first and pay a
    /// slot, a model call and, for a safety block, a strike toward the
    /// device's 24-hour pause to hear the same verdict.
    case failAndHalt
    /// This photo only: mark it failed and keep it.
    case fail
}

/// What is known about whether a failed request reached the server, beyond
/// the `AppError` it was flattened into — `AppError.network` covers a
/// connection that was never made and one that dropped mid-request alike.
enum HaulReach: Equatable {
    /// Nothing more than the error says.
    case unknown
    /// The phone had no network path from the send to the failure. The API
    /// session waits for connectivity until its 35 s resource timeout, so
    /// this is how *offline* usually arrives: as `.timeout`, not as
    /// `.notConnectedToInternet`.
    case neverConnected
    /// `URLError.networkConnectionLost`: the connection dropped with the
    /// request under way, so the upload may have landed and the server may
    /// have charged the slot and run the model. It is also what a request
    /// left open when the app suspends usually ends as.
    case droppedMidRequest
}

/// The device's network path as `NWPathMonitor` last reported it.
struct HaulNetworkPath: Equatable, Sendable {
    var isSatisfied: Bool
    /// Bumped each time the path becomes satisfied, so two readings tell
    /// whether there was a path at any moment between them.
    var satisfiedCount: Int
}

/// Two server failures are "the same" when their case and their message
/// match. That is what the breaker compares.
struct HaulFailureSignature: Equatable {
    let kind: String
    let message: String
}

// MARK: - Photos on disk

/// Photos waiting to be valued, one JPEG each, under Application Support.
///
/// **Why they are on disk at all.** After a 429 the wait can be most of an
/// hour. The user locks the phone during it, and iOS may end the app; a queue
/// held only in memory would lose every photo in it. About 300–500 KB each,
/// deleted once the photo is valued *and saved*, or removed by the user.
///
/// **The state is in the file name** — `<epochMs>_<uuid>.<state>.jpg` — so
/// one directory listing gives id, capture time and state together:
///
/// * `failed` restores as failed and is re-sent only by a manual Try again.
///   Without it an unusable photo was re-sent on every open, and each 422
///   safety block counts toward the device being paused.
/// * `started` restores as queued but does not emit `scan_started` again.
///
/// The capture time is metadata read from the name, not from the file system,
/// but `PrivacyInfo.xcprivacy`'s C617.1 covers file metadata anywhere in the
/// container either way; the name is chosen for the single listing, not to
/// avoid a declaration.
struct HaulPhotoStore: Sendable {
    enum State: String, Sendable, CaseIterable {
        case queued, started, failed
    }

    struct Entry: Sendable, Equatable {
        let id: UUID
        let capturedAt: Date
        let state: State
    }

    /// Tests pass a temporary directory.
    let directory: URL

    /// `Application Support/HaulPending`, excluded from backup: a photo that
    /// is valued in minutes has no business in an iCloud backup.
    static var live: HaulPhotoStore {
        let base = (try? FileManager.default.url(for: .applicationSupportDirectory,
                                                 in: .userDomainMask,
                                                 appropriateFor: nil, create: true))
            ?? FileManager.default.temporaryDirectory
        return HaulPhotoStore(directory: base.appendingPathComponent("HaulPending", isDirectory: true))
    }

    /// Pending photos older than this are deleted unseen.
    ///
    /// A user whose Pro lapsed cannot reopen Haul, so without a limit their
    /// photos would sit in the container indefinitely. Fourteen days covers a
    /// sourcing trip plus a lapse and a renewal. The "Your photos are kept for
    /// 14 days." copy in `HaulView` states this number; change both together.
    nonisolated static var maxAge: TimeInterval { 14 * 86_400 }

    /// Milliseconds since 1970 — the capture time as the file name holds it.
    static func millis(_ date: Date) -> Int64 {
        Int64((date.timeIntervalSince1970 * 1000).rounded())
    }

    /// `date` at the precision the file name keeps, so a restored photo's
    /// capture time equals the one it was saved with.
    static func captureDate(_ date: Date) -> Date {
        Date(timeIntervalSince1970: Double(millis(date)) / 1000)
    }

    func fileURL(id: UUID, capturedAt: Date, state: State) -> URL {
        directory.appendingPathComponent(
            "\(Self.millis(capturedAt))_\(id.uuidString).\(state.rawValue).jpg")
    }

    /// Written `queued`, with the same protection class as the SwiftData
    /// store (`StoreProtection.level`). `.complete` would fail every write
    /// made after the phone locks — which is when a long pause is waited out.
    func save(_ jpeg: Data, id: UUID, capturedAt: Date) throws {
        try ensureDirectory()
        try jpeg.write(to: fileURL(id: id, capturedAt: capturedAt, state: .queued),
                       options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }

    /// Renames the file to `state`. A rename is atomic, so a kill mid-way
    /// leaves one state or the other, never neither.
    func mark(_ id: UUID, capturedAt: Date, _ state: State) {
        guard let current = existing(id: id, capturedAt: capturedAt),
              current.state != state else { return }
        try? FileManager.default.moveItem(at: current.url,
                                          to: fileURL(id: id, capturedAt: capturedAt, state: state))
    }

    /// The JPEG, whatever state it is in. Nil when there is no file.
    func read(id: UUID, capturedAt: Date) -> Data? {
        guard let current = existing(id: id, capturedAt: capturedAt) else { return nil }
        return try? Data(contentsOf: current.url)
    }

    func remove(id: UUID, capturedAt: Date) {
        for state in State.allCases {
            try? FileManager.default.removeItem(at: fileURL(id: id, capturedAt: capturedAt, state: state))
        }
    }

    /// Everything waiting, oldest first. Deletes, as it goes, files past
    /// `maxAge` and names it cannot read.
    func pending(now: Date) -> [Entry] {
        let fileManager = FileManager.default
        guard let names = try? fileManager.contentsOfDirectory(atPath: directory.path) else { return [] }
        var entries: [Entry] = []
        // Hidden names are left alone: an atomic write in progress on the
        // preparation thread lives under one until it is renamed into place.
        for name in names where !name.hasPrefix(".") {
            let url = directory.appendingPathComponent(name)
            guard let entry = Self.parse(name),
                  now.timeIntervalSince(entry.capturedAt) <= Self.maxAge else {
                try? fileManager.removeItem(at: url)
                continue
            }
            entries.append(entry)
        }
        return entries.sorted { $0.capturedAt < $1.capturedAt }
    }

    func removeAll() {
        try? FileManager.default.removeItem(at: directory)
    }

    static func parse(_ name: String) -> Entry? {
        guard name.hasSuffix(".jpg") else { return nil }
        let parts = name.dropLast(4).split(separator: ".")
        guard parts.count == 2, let state = State(rawValue: String(parts[1])) else { return nil }
        let head = parts[0].split(separator: "_")
        guard head.count == 2, let ms = Int64(head[0]),
              let id = UUID(uuidString: String(head[1])) else { return nil }
        return Entry(id: id, capturedAt: Date(timeIntervalSince1970: Double(ms) / 1000), state: state)
    }

    private func existing(id: UUID, capturedAt: Date) -> (url: URL, state: State)? {
        for state in State.allCases {
            let url = fileURL(id: id, capturedAt: capturedAt, state: state)
            if FileManager.default.fileExists(atPath: url.path) { return (url, state) }
        }
        return nil
    }

    private func ensureDirectory() throws {
        guard !FileManager.default.fileExists(atPath: directory.path) else { return }
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        var url = directory
        try? url.setResourceValues(values)
    }
}

// MARK: - Items

struct HaulItem: Identifiable {
    enum State {
        case preparing
        case queued
        case scanning
        /// `saved` is false when the find could not be written to My Finds.
        case done(ScanResult, saved: Bool)
        /// The server's message, or nil for a photo restored as failed.
        case failed(String?)
    }

    enum DraftState: Equatable {
        case waiting
        case drafting
        case done(GeneratedListing)
        case failed(String)
    }

    let id: UUID
    let capturedAt: Date
    var thumbnail: UIImage?
    var state: State
    var draft: DraftState?

    /// Not valued yet, and not failed: preparing, queued or being scanned.
    var isPending: Bool {
        switch state {
        case .preparing, .queued, .scanning: return true
        case .done, .failed:                 return false
        }
    }

    var isFailed: Bool {
        if case .failed = state { return true }
        return false
    }

    var result: ScanResult? {
        if case .done(let result, _) = state { return result }
        return nil
    }

    /// Valued, but not in My Finds.
    var isUnsaved: Bool {
        if case .done(_, let saved) = state { return !saved }
        return false
    }

    /// The server's reason for a failed photo, when it gave one.
    var failureMessage: String? {
        if case .failed(let message) = state { return message }
        return nil
    }
}

/// Why nothing is being sent, for a while. Both end on their own.
enum HaulPause: Equatable {
    case rateLimited(until: Date)
    /// `message` is the error's own description, shown verbatim.
    case offline(until: Date, message: String)

    var until: Date {
        switch self {
        case .rateLimited(let until), .offline(let until, _): return until
        }
    }

    var isRateLimited: Bool {
        if case .rateLimited = self { return true }
        return false
    }

    /// The banner's two lines as VoiceOver hears them, `remaining` seconds
    /// before the pause ends.
    ///
    /// Not `AppError.rateLimitMessage`: its "Try again in…" asks the user to
    /// act, when the queue resumes by itself and the shutter keeps working,
    /// and it leaves out that the photos are kept.
    func announcement(remaining: TimeInterval) -> String {
        let lead: String
        switch self {
        case .rateLimited:             lead = String(localized: "You've hit the scan limit.")
        case .offline(_, let message): lead = message
        }
        let kept = String(localized: "Your photos are kept — resuming in \(Self.spokenWait(remaining))")
        return String(localized: "\(lead) \(kept)")
    }

    /// Rounded up, like `AppError.rateLimitMessage`, so a label read late is
    /// never shorter than the real wait.
    static func spokenWait(_ remaining: TimeInterval) -> String {
        let seconds = max(1, Int(remaining.rounded(.up)))
        if seconds < 60 {
            return String(localized: "\(seconds) seconds")
        }
        let minutes = Int((Double(seconds) / 60).rounded(.up))
        return String(localized: "\(minutes) minutes")
    }
}

/// Why nothing is being sent until something changes.
enum HaulHold: Equatable {
    /// A 402 while the app believes the user is Pro: the server has not seen
    /// the purchase yet. Retrying.
    case confirmingSubscription
    /// The retries were used up: Try again, or Restore purchases.
    case subscriptionUnconfirmed
    /// The client-side gate: Pro lapsed. Nothing is sent, so a lapsed user's
    /// one free scan a day is not spent by a queue they cannot see.
    case notEntitled
    /// A 402 while not subscribed, with the server's copy.
    case quota(String)
    /// The breaker, an expired session or an outage, with its message.
    case halted(String)

    var isHalted: Bool {
        if case .halted = self { return true }
        return false
    }

    /// The paywall trigger for this hold's Upgrade button, or nil when it has
    /// none. Drafting is Snap → Sell's funnel; a quota refusal on a scan is
    /// the scan-limit funnel `ScanViewModel` protects.
    func upgradeTrigger(forDrafts: Bool) -> PaywallTrigger? {
        switch self {
        case .notEntitled: return forDrafts ? .snapSell : .haul
        case .quota:       return forDrafts ? .snapSell : .scanLimit
        case .confirmingSubscription, .subscriptionUnconfirmed, .halted: return nil
        }
    }

    /// What VoiceOver hears when this hold begins: the banner's own words,
    /// under the same keys as `HaulHoldBanner`'s.
    var announcement: String {
        switch self {
        case .confirmingSubscription:
            return String(localized: "Confirming your subscription…")
        case .subscriptionUnconfirmed:
            return String(localized: "SnapWorth couldn't confirm your Pro subscription yet, so the rest of this haul is on hold. Your photos are kept.")
        case .notEntitled:
            return String(localized: "Haul mode is part of SnapWorth Pro. Your photos are kept for 14 days.")
        case .quota(let message):
            return message
        case .halted(let message):
            let rest = String(localized: "The rest are on hold so they don't run into the same problem.")
            return String(localized: "\(message) \(rest)")
        }
    }
}

// MARK: - Session

/// One haul: its photos, their queue, and the drafts made from them.
///
/// **One per process.** `live` creates it once and caches it; ScanView holds
/// it and hands it to `HaulView`. Closing Haul (`finishHaul`) clears the
/// valued items — they are in My Finds — and keeps anything pending or failed
/// for the next open. A second live session, which would send the same
/// photos twice, cannot exist.
@MainActor
@Observable
final class HaulSession {
    nonisolated static var maxScansInFlight: Int { 2 }
    /// The wait when a 429 carries no `Retry-After`. Only a fallback: the
    /// backend always sends the header.
    nonisolated static var defaultPause: TimeInterval { 300 }
    nonisolated static var maxPause: TimeInterval { 3600 }
    /// So an automatic re-send never happens in the same turn as the 429.
    nonisolated static var minPause: TimeInterval { 5 }
    /// A 429's deadline, kept across a relaunch: without it, a kill during a
    /// 57-minute wait reopens to two requests at once.
    nonisolated static var rateLimitKey: String { "haul.rateLimitedUntil" }
    nonisolated static var lastMarketplaceKey: String { "haul.lastMarketplace" }
    /// 56pt strip cells at 3×.
    nonisolated static var thumbnailEdge: CGFloat { 168 }

    struct Dependencies {
        var scan: @Sendable (UIImage) async throws -> ScanAPIResponse
        /// Turns a response into a find and saves it — see `recordNormalScan`.
        var record: @MainActor (ScanAPIResponse, UIImage, Date) async -> (ScanResult, saved: Bool)
        var deleteFind: @MainActor (ScanResult) throws -> Void
        var generate: @Sendable (ListingInput, Marketplace) async throws -> GeneratedListing
        var isEntitled: @MainActor () -> Bool
        var refreshEntitlements: @MainActor () async -> Void
        var networkPath: @MainActor () -> HaulNetworkPath
        /// Returns `end`, which is idempotent.
        var beginBackgroundTask: @MainActor (String) -> @MainActor () -> Void
        /// The haul summary was reached for the first time in an open.
        /// `worthAskingForReview` is whether any valued photo is an estimate
        /// the app stands behind (`ReviewPrompt.isWorthAskingAbout`), the same
        /// rule the single-scan result uses before asking for a rating.
        var summaryReached: @MainActor (_ worthAskingForReview: Bool) -> Void
        var sleep: @Sendable (Duration) async throws -> Void
        var now: () -> Date
        var defaults: UserDefaults
        var store: HaulPhotoStore
    }

    // ── Observed state ───────────────────────────────────────────────────
    //
    // Each pause and hold is announced to VoiceOver as it begins, here rather
    // than at each place that sets one: a sighted user sees every one of them
    // as a banner, and a VoiceOver user shooting a pile otherwise hears the
    // count rise while nothing is being valued.

    /// Capture order, which is strip order.
    private(set) var items: [HaulItem] = []
    private(set) var pause: HaulPause? {
        didSet {
            // A later deadline, or a second offline message, is the same
            // pause; only a new kind of pause is news.
            guard let pause, pause.isRateLimited != oldValue?.isRateLimited else { return }
            announce(pause.announcement(remaining: pause.until.timeIntervalSince(deps.now())))
        }
    }
    private(set) var hold: HaulHold? {
        didSet {
            guard let hold, hold != oldValue else { return }
            announce(hold.announcement)
        }
    }
    private(set) var draftHold: HaulHold? {
        didSet {
            // The summary shows the drafts' hold only when it differs from
            // the scans'; so does VoiceOver.
            guard let draftHold, draftHold != oldValue, draftHold != hold else { return }
            announce(draftHold.announcement)
        }
    }
    /// Chosen once per haul.
    private(set) var draftMarketplace: Marketplace?
    private(set) var isOpen = false

    // ── Bookkeeping ──────────────────────────────────────────────────────
    @ObservationIgnored private let deps: Dependencies
    @ObservationIgnored private var scanQueue: HaulQueue<UUID>
    @ObservationIgnored private var draftQueue: HaulQueue<UUID>
    @ObservationIgnored private var isForeground = true
    @ObservationIgnored private var attempts: [UUID: Int] = [:]
    /// Photos whose `scan_started` has been emitted.
    @ObservationIgnored private var started: Set<UUID> = []
    /// Each photo's `is_first`, decided on its first send.
    @ObservationIgnored private var firstFlags: [UUID: Bool] = [:]
    /// The one photo allowed to report `is_first = true`. Two scans in flight
    /// on a new install would otherwise both read `ScanTally` before either
    /// recorded, and the funnel would count two first scans.
    @ObservationIgnored private var firstClaim: UUID?
    /// The JPEG, for a photo whose disk write failed: the only copy.
    @ObservationIgnored private var memoryJPEG: [UUID: Data] = [:]
    /// The original, for a photo that could not be encoded, so Try again can
    /// prepare it again.
    @ObservationIgnored private var unpreparedPhotos: [UUID: UIImage] = [:]
    @ObservationIgnored private var prepTail: Task<Void, Never>?
    @ObservationIgnored private var resumeTask: Task<Void, Never>?
    @ObservationIgnored private var resumeAt: Date?
    @ObservationIgnored private var rateLimitDeadline: Date?
    @ObservationIgnored private var confirmTasks: [Lane: Task<Void, Never>] = [:]
    @ObservationIgnored private var confirmAttempts: [Lane: Int] = [:]
    @ObservationIgnored private var lastFailure: HaulFailureSignature?
    @ObservationIgnored private var offlineStreak = 0
    @ObservationIgnored private var draftSnapshot: Set<UUID> = []
    /// The size `haul_completed` last reported in this open, or nil before
    /// the first summary.
    @ObservationIgnored private var reportedSize: Int?
    @ObservationIgnored private var limitHitReported = false
    @ObservationIgnored private var celebrationDue = false
    @ObservationIgnored private var didRestore = false
    #if DEBUG
    /// Wall-clock time of the first capture since the queue was last empty —
    /// the start of acceptance 1's two minutes.
    @ObservationIgnored private var firstCaptureAt: Date?
    private static let signposter = OSSignposter(subsystem: "eu.snapworth.app", category: "haul")
    private static let log = Logger(subsystem: "eu.snapworth.app", category: "haul")
    #endif

    private enum Lane { case scans, drafts }

    init(_ deps: Dependencies) {
        self.deps = deps
        self.scanQueue = HaulQueue(maxInFlight: Self.maxScansInFlight)
        self.draftQueue = HaulQueue(maxInFlight: 1)
    }

    // ── The one session ──────────────────────────────────────────────────

    private static var shared: HaulSession?

    /// The process's session, created on first use. Restores pending photos
    /// once, on creation.
    static func live(purchaseService: any PurchaseService, repository: ScanRepository) -> HaulSession {
        if let shared { return shared }
        let session = HaulSession(Dependencies(
            scan: { image in try await ScanAPIClient.shared.scan(image: image) },
            record: { response, image, capturedAt in
                await HaulSession.recordNormalScan(response, image: image, capturedAt: capturedAt,
                                                   purchaseService: purchaseService,
                                                   repository: repository)
            },
            deleteFind: { try repository.delete($0) },
            generate: { input, marketplace in
                try await ListingAPIClient.shared.generate(input, marketplace: marketplace)
            },
            isEntitled: { purchaseService.isSubscribed },
            refreshEntitlements: { await purchaseService.refreshEntitlements() },
            networkPath: { HaulPathMonitor.shared.current },
            beginBackgroundTask: { name in
                let task = HaulBackgroundTask(name: name)
                return { task.end() }
            },
            summaryReached: { worthAskingForReview in
                // Moved here from each scan: a haul is one sitting, and a
                // rating sheet or a recap schedule per photo would interrupt
                // the camera it is meant to celebrate.
                let monthScans = repository.countScansThisMonth()
                Task { await NotificationManager.shared.scheduleMonthlyRecap(monthScanCount: monthScans) }
                // The summary's totals are the payoff on screen, so this is a
                // moment of value in the sense `ReviewPrompt.requestIfDue`
                // means, and it keeps that function's gap. Only when some
                // estimate here is Medium or High, as for a single result.
                // The pause lets the summary settle first; unlike the old
                // single-scan timer, nothing is covered while it runs.
                guard worthAskingForReview else { return }
                Task {
                    try? await Task.sleep(for: .seconds(1.2))
                    ReviewPrompt.requestIfDue()
                }
            },
            sleep: { try await Task.sleep(for: $0) },
            now: { Date() },
            defaults: .standard,
            store: .live))
        shared = session
        // Started now, so it has a reading by the first send.
        _ = HaulPathMonitor.shared
        Task { await session.restorePending() }
        return session
    }

    /// Clear history: every pending photo goes, and the live session forgets
    /// what it had not sent. A scan already in flight is left to land.
    static func discardAllPending(store: HaulPhotoStore) {
        store.removeAll()
        shared?.dropUnsent()
    }

    // ── Figures ──────────────────────────────────────────────────────────

    private var valuedResults: [ScanResult] { items.compactMap(\.result) }

    /// The sum of each valued item's expected price — `portfolioValue`,
    /// through the same `HistoryViewModel.total(of:)` as My Finds and the
    /// widget, so the three never disagree about one find.
    var total: Decimal { HistoryViewModel.total(of: valuedResults.map(\.portfolioValue)) }
    var valuedCount: Int { valuedResults.count }
    var pendingCount: Int { items.filter(\.isPending).count }
    var failedCount: Int { items.filter(\.isFailed).count }

    /// The most valuable find; the earlier capture wins a tie.
    var topFind: ScanResult? {
        var best: ScanResult?
        for result in valuedResults where best == nil || result.portfolioValue > best!.portfolioValue {
            best = result
        }
        return best
    }

    /// For tests: how many times this photo has been sent since its last
    /// manual Try again. An offline failure is not an attempt.
    func attemptCount(for id: UUID) -> Int { attempts[id] ?? 0 }
    var scansInFlight: Int { scanQueue.inFlight.count }
    var draftsInFlight: Int { draftQueue.inFlight.count }
    var isProbing: Bool { scanQueue.isProbing }

    // ── Lifecycle ────────────────────────────────────────────────────────

    func open() {
        isOpen = true
        // A `.quota` hold outlives the screen, and the way back in after
        // buying is the Scan tab's paywall, whose dismissal never reaches
        // `resumeAfterPurchase`. Only `.quota`: an unconfirmed subscription
        // is retried on the user's say-so, and `.notEntitled` lifts in
        // `pump` by itself.
        if deps.isEntitled() {
            for lane in [Lane.scans, .drafts] {
                if case .quota? = holdValue(lane) { reconfirm(lane) }
            }
        }
        pump()
    }

    /// In the background, scans already in flight finish and nothing new
    /// starts: a request begun as the app suspends is a request whose answer
    /// may never be read.
    func setForeground(_ active: Bool) {
        isForeground = active
        if active { pump() }
    }

    /// A photo from the camera. Synchronous, so capture order is the order
    /// photos were delivered.
    ///
    /// The photo arrives already decoded at the 1568 px upload edge — the
    /// camera's photo delegate runs `ScanAPIClient.decodeForScan` — so there
    /// is no full-size decode here, and the downscale to `maxUploadEdge` in
    /// `prepare` passes it through untouched. Preparation is chained: each
    /// photo waits for the one before it, so one JPEG encode and disk write
    /// runs at a time, and a photo still waiting holds its decoded bitmap,
    /// about 7 MB at 4:3, until its turn. The JPEG itself is read back when
    /// its scan starts.
    func add(_ photo: UIImage) {
        let id = UUID()
        let capturedAt = HaulPhotoStore.captureDate(deps.now())
        items.append(HaulItem(id: id, capturedAt: capturedAt, thumbnail: nil,
                              state: .preparing, draft: nil))
        #if DEBUG
        if firstCaptureAt == nil { firstCaptureAt = Date() }
        #endif
        let end = deps.beginBackgroundTask("haul-prep")
        prepare(id: id, capturedAt: capturedAt, photo: photo, end: end)
    }

    /// Brings back the photos an earlier launch left. Once per launch.
    func restorePending() async {
        guard !didRestore else { return }
        didRestore = true
        let now = deps.now()

        if let raw = deps.defaults.object(forKey: Self.rateLimitKey) as? Double {
            // Probe even when the deadline has passed: the last thing this
            // device heard was a 429, and one request finds out for the price
            // of one.
            scanQueue.beginProbing()
            draftQueue.beginProbing()
            let until = Date(timeIntervalSince1970: raw)
            if until > now {
                rateLimitDeadline = until
                scanQueue.pause(until: until)
                draftQueue.pause(until: until)
                pause = .rateLimited(until: until)
            }
        }

        let store = deps.store
        let edge = Self.thumbnailEdge
        let restored = await Task.detached(priority: .utility) { () -> [(HaulPhotoStore.Entry, UIImage?)] in
            store.pending(now: now).map { entry in
                let thumbnail = store.read(id: entry.id, capturedAt: entry.capturedAt)
                    .flatMap(UIImage.init(data:))
                    .map { ScanAPIClient.downscale($0, maxEdge: edge) }
                return (entry, thumbnail)
            }
        }.value

        for (entry, thumbnail) in restored where index(of: entry.id) == nil {
            let item = HaulItem(id: entry.id, capturedAt: entry.capturedAt, thumbnail: thumbnail,
                                state: entry.state == .failed ? .failed(nil) : .queued, draft: nil)
            let at = items.firstIndex { $0.capturedAt > entry.capturedAt } ?? items.endIndex
            items.insert(item, at: at)
            if entry.state == .started { started.insert(entry.id) }
            if entry.state != .failed {
                scanQueue.enqueue(entry.id, rank: Self.rank(entry.capturedAt))
            }
        }
        pump()
    }

    /// Try again, for one failed photo. A manual retry is a new attempt, so it
    /// emits `scan_started` again.
    func retry(_ id: UUID) {
        guard let index = index(of: id), items[index].isFailed else { return }
        attempts[id] = 0
        started.remove(id)
        firstFlags[id] = nil
        let capturedAt = items[index].capturedAt
        if let photo = unpreparedPhotos.removeValue(forKey: id) {
            items[index].state = .preparing
            prepare(id: id, capturedAt: capturedAt, photo: photo,
                    end: deps.beginBackgroundTask("haul-prep"))
            return
        }
        items[index].state = .queued
        deps.store.mark(id, capturedAt: capturedAt, .queued)
        scanQueue.enqueue(id, rank: Self.rank(capturedAt))
        pump()
    }

    func retryAllFailed() {
        for id in items.filter(\.isFailed).map(\.id) { retry(id) }
    }

    /// Removes a photo, or a find. Not offered for a photo in flight.
    ///
    /// For a valued item this deletes the find from My Finds too — after the
    /// view's "Delete this find?" — which is how a double-tapped duplicate is
    /// undone in both places at once.
    func remove(_ id: UUID) {
        guard let index = index(of: id) else { return }
        let item = items[index]
        switch item.state {
        case .preparing, .scanning:
            return
        case .done(let result, let saved):
            if saved {
                do { try deps.deleteFind(result) } catch { return }
            } else {
                deps.store.remove(id: id, capturedAt: item.capturedAt)
            }
        case .queued, .failed:
            scanQueue.remove(id)
            deps.store.remove(id: id, capturedAt: item.capturedAt)
        }
        forget(id)
        items.remove(at: index)
    }

    /// "Try the rest", or "Try again" on an unconfirmed subscription.
    func resumeHeld() {
        for lane in [Lane.scans, .drafts] {
            switch holdValue(lane) {
            case .halted?:
                if lane == .scans {
                    lastFailure = nil
                    offlineStreak = 0
                }
                setHold(lane, nil)
                withQueue(lane) { $0.unhold(); $0.beginProbing() }
            case .subscriptionUnconfirmed?:
                // One more request, on the user's say-so. The automatic
                // retries stay spent, so a 402 here comes straight back.
                setHold(lane, .confirmingSubscription)
                confirmTasks[lane]?.cancel()
                confirmTasks[lane] = Task { [weak self] in
                    guard let self else { return }
                    await self.deps.refreshEntitlements()
                    guard !Task.isCancelled else { return }
                    self.confirmTasks[lane] = nil
                    self.releaseConfirmation(lane)
                }
            default:
                break
            }
        }
        pump()
    }

    /// Ends an offline pause now — on return to the foreground, or the
    /// banner's Try again. A rate-limit pause is never cut short.
    func resumeNow() {
        guard case .offline? = pause else { return }
        scanQueue.clearPause()
        draftQueue.clearPause()
        pause = nil
        resumeTask?.cancel()
        resumeTask = nil
        resumeAt = nil
        if let deadline = rateLimitDeadline, deadline > deps.now() {
            scanQueue.pause(until: deadline)
            draftQueue.pause(until: deadline)
            pause = .rateLimited(until: deadline)
        }
        pump()
    }

    /// The paywall closed, or a restore succeeded, and the user is Pro.
    ///
    /// Goes through `.confirmingSubscription` rather than straight back to
    /// sending: the entitlement sync to the server runs detached and is not
    /// awaited, so the first request after a purchase is exactly the one the
    /// server is most likely to still see as free.
    func resumeAfterPurchase() {
        guard deps.isEntitled() else { return }
        for lane in [Lane.scans, .drafts] {
            switch holdValue(lane) {
            case .notEntitled?, .quota?, .subscriptionUnconfirmed?:
                reconfirm(lane)
            default:
                break
            }
        }
    }

    /// A fresh confirmation episode, with its two retries.
    private func reconfirm(_ lane: Lane) {
        confirmAttempts[lane] = 0
        if lane == .scans { limitHitReported = false }
        beginConfirming(lane)
    }

    /// Every Finish of an open.
    ///
    /// The first reports `haul_completed`. Here and not in the view's
    /// `onDisappear`: a haul most often ends with the app killed from the
    /// switcher while the summary is on screen, which never fires
    /// `onDisappear`. It also puts `haul_completed` before any `haul_shared`.
    ///
    /// A later one — after "Keep scanning" — reports again only if the haul
    /// has grown into a larger bucket, as a revision naming the bucket it
    /// replaces. Without it the hauls that grew past their first Finish,
    /// which are the big ones, would be counted at their first size.
    func didReachSummary() {
        let count = valuedCount + pendingCount
        guard count > 0 else { return }
        let bucket = AnalyticsEvent.haulSizeBucket(count)
        guard let reported = reportedSize else {
            reportedSize = count
            Analytics.shared.track(.haulCompleted(itemsBucket: bucket))
            deps.summaryReached(valuedResults.contains {
                ReviewPrompt.isWorthAskingAbout(confidence: $0.confidence)
            })
            return
        }
        let previous = AnalyticsEvent.haulSizeBucket(reported)
        guard count > reported, bucket != previous else { return }
        reportedSize = count
        Analytics.shared.track(.haulCompleted(itemsBucket: bucket, revisedFrom: previous))
    }

    /// Done. Valued items leave the haul — they are in My Finds — and pending
    /// or failed ones stay for the next open. Scans in flight finish and save,
    /// and leave as they land (`succeed`).
    func finishHaul() {
        items.removeAll { $0.result != nil }
        for id in draftQueue.waiting { draftQueue.remove(id) }
        for index in items.indices { items[index].draft = nil }
        draftSnapshot = []
        draftMarketplace = nil
        confirmTasks[.drafts]?.cancel()
        confirmTasks[.drafts] = nil
        draftQueue.unhold()
        draftHold = nil
        isOpen = false
        reportedSize = nil
    }

    // ── Drafts ───────────────────────────────────────────────────────────

    /// Snap → Sell for every item valued or pending now, for one marketplace.
    ///
    /// Photos captured afterwards are not drafted: the confirmation the user
    /// agreed to named a number. Drafts run one at a time and only while no
    /// scan is waiting — a photo cannot be taken again, a draft can — or
    /// while the ones waiting are halted (see `pump`).
    func draftAll(for marketplace: Marketplace) {
        if draftMarketplace == nil {
            draftMarketplace = marketplace
            deps.defaults.set(marketplace.rawValue, forKey: Self.lastMarketplaceKey)
        }
        for index in items.indices {
            let item = items[index]
            guard !item.isFailed else { continue }
            switch item.draft {
            case nil, .failed?: break
            default: continue
            }
            draftSnapshot.insert(item.id)
            items[index].draft = .waiting
            if item.result != nil {
                draftQueue.enqueue(item.id, rank: Self.rank(item.capturedAt))
            }
        }
        pump()
    }

    /// How many items "Draft listings for all" would draft if tapped now —
    /// the number its confirmation names.
    var draftableCount: Int {
        items.filter { item in
            guard !item.isFailed else { return false }
            switch item.draft {
            case nil, .failed?: return true
            default:            return false
            }
        }.count
    }

    var hasWaitingDrafts: Bool { items.contains { $0.draft == .waiting } }
    var hasFinishedDrafts: Bool {
        items.contains {
            if case .done? = $0.draft { return true }
            return false
        }
    }

    /// Drops the drafts not yet started; the one in flight finishes.
    func stopDrafting() {
        for id in draftQueue.waiting { draftQueue.remove(id) }
        for index in items.indices where items[index].draft == .waiting {
            items[index].draft = nil
            draftSnapshot.remove(items[index].id)
        }
    }

    func retryDraft(_ id: UUID) {
        guard let index = index(of: id), items[index].result != nil,
              case .failed? = items[index].draft else { return }
        items[index].draft = .waiting
        draftSnapshot.insert(id)
        draftQueue.enqueue(id, rank: Self.rank(items[index].capturedAt))
        pump()
    }

    /// Every finished draft, for the share sheet — to Notes or Mail, not to a
    /// marketplace, which takes one listing at a time.
    func allDraftsText() -> String {
        items.compactMap { item -> String? in
            guard case .done(let listing)? = item.draft else { return nil }
            return listing.shareText
        }
        .joined(separator: "\n\n———\n\n")
    }

    /// The 1080×1920 story card, or nil with nothing valued.
    func renderShareCard() -> UIImage? {
        guard valuedCount > 0 else { return nil }
        let top = topFind
        let renderer = ImageRenderer(content: HaulShareCardView(
            itemCount: valuedCount, total: total,
            topFindName: top?.itemName, topFindValue: top?.portfolioValue))
        renderer.scale = ResultViewModel.shareCardScale
        return renderer.uiImage
    }

    // ── The queue ────────────────────────────────────────────────────────

    /// Starts whatever may be started. Called after anything that could free
    /// a slot or end a wait.
    func pump() {
        guard isOpen, isForeground else { return }
        let now = deps.now()

        if let current = pause, now >= current.until {
            pause = nil
            announce(String(localized: "Scanning resumed"))
        }
        if let current = pause {
            scheduleResume(at: current.until)
            return
        }

        // The client-side gate: a lapsed subscription sends nothing. It lifts
        // by itself the moment the entitlement comes back.
        guard deps.isEntitled() else {
            if hold == nil, !scanQueue.waiting.isEmpty { hold = .notEntitled }
            if draftHold == nil, !draftQueue.waiting.isEmpty { draftHold = .notEntitled }
            return
        }
        if hold == .notEntitled { hold = nil }
        if draftHold == .notEntitled { draftHold = nil }

        for id in scanQueue.claim(now: now) {
            if let index = index(of: id) { items[index].state = .scanning }
            Task { await self.runScan(id) }
        }
        // Scans get the shared budget first — but photos waiting under a
        // halt spend none of it, and would otherwise keep every draft on its
        // clock icon until the user dealt with them. The commonest halts,
        // the 24-hour device pause and the breaker on an unusable photo, are
        // `/scan`'s alone: `/listing` does not check them. Only a halt: under
        // a 402 hold a draft would be refused as well, and spend a slot to
        // hear it.
        let scansSpendNothing = scanQueue.waiting.isEmpty || hold?.isHalted == true
        if scanQueue.inFlight.isEmpty, scansSpendNothing {
            for id in draftQueue.claim(now: now) {
                Task { await self.runDraft(id) }
            }
        }
    }

    /// Tests' tearDown: no timer outlives a test.
    func cancelTimers() {
        resumeTask?.cancel()
        resumeTask = nil
        resumeAt = nil
        for task in confirmTasks.values { task.cancel() }
        confirmTasks = [:]
    }

    // ── One scan ─────────────────────────────────────────────────────────

    private func runScan(_ id: UUID) async {
        // Covers the scan, the save and the file clean-up, so a response that
        // arrives as the app suspends is saved before it does.
        let end = deps.beginBackgroundTask("haul-scan")
        defer { end() }
        await performScan(id)
        scanQueue.release(id)
        #if DEBUG
        measureIfDrained()
        #endif
        celebrateIfDone()
        pump()
    }

    #if DEBUG
    /// Acceptance 1 is "12 items in under 2 minutes", measured on a device
    /// from the first shutter tap to the last valuation. This is that
    /// measurement: a signpost for Instruments and a log line for Console.
    private func measureIfDrained() {
        guard pendingCount == 0, let start = firstCaptureAt else { return }
        firstCaptureAt = nil
        let valued = valuedCount
        let seconds = Int(Date().timeIntervalSince(start).rounded())
        Self.signposter.emitEvent("haul", "\(valued) valued in \(seconds)s")
        Self.log.debug("haul: \(valued) valued in \(seconds)s")
    }
    #endif

    private func performScan(_ id: UUID) async {
        guard let index = index(of: id) else { return }
        let capturedAt = items[index].capturedAt
        items[index].state = .scanning
        attempts[id, default: 0] += 1

        if firstFlags[id] == nil {
            let isFirst = ScanTally.isFirstScan() && firstClaim == nil
            if isFirst { firstClaim = id }
            firstFlags[id] = isFirst
        }
        // Once per photo: a re-send after a pause is the same scan.
        if !started.contains(id) {
            started.insert(id)
            Analytics.shared.track(.scanStarted(isFirst: firstFlags[id] ?? false))
            deps.store.mark(id, capturedAt: capturedAt, .started)
        }

        let store = deps.store
        let inMemory = memoryJPEG[id]
        let loaded = await Task.detached(priority: .userInitiated) { () -> (found: Bool, image: UIImage?) in
            guard let data = inMemory ?? store.read(id: id, capturedAt: capturedAt) else {
                return (false, nil)
            }
            return (true, UIImage(data: data))
        }.value

        guard loaded.found else {
            // The file went between the claim and here: Clear history.
            forget(id)
            items.removeAll { $0.id == id }
            return
        }
        guard let image = loaded.image else {
            failScan(id, .imageEncodingFailed)
            return
        }

        let pathAtSend = deps.networkPath()
        do {
            let response = try await deps.scan(image)
            let (result, saved) = await deps.record(response, image, capturedAt)
            succeed(id, capturedAt: capturedAt, result: result, saved: saved)
        } catch {
            let reach = Self.reach(of: error, pathAtSend: pathAtSend, pathNow: deps.networkPath())
            handleScanError(error, reach: reach, id: id)
        }
    }

    private func succeed(_ id: UUID, capturedAt: Date, result: ScanResult, saved: Bool) {
        memoryJPEG[id] = nil
        if saved {
            deps.store.remove(id: id, capturedAt: capturedAt)
        } else {
            // Not in My Finds, so the photo is the only way to value it again.
            // Kept, and restored as failed on the next launch.
            deps.store.mark(id, capturedAt: capturedAt, .failed)
        }
        noteSuccess(.scans, id)
        guard let index = index(of: id) else { return }
        if !isOpen, saved {
            // Done was tapped while this was in flight. It is in My Finds,
            // which is where `finishHaul` sent every other valued item — and
            // kept here, in a session that outlives the screen, it would be a
            // live `@Model` that My Finds can delete without telling the
            // haul, and the next open would read it.
            forget(id)
            items.remove(at: index)
            return
        }
        celebrationDue = true
        items[index].state = .done(result, saved: saved)
        if draftSnapshot.contains(id), items[index].draft == .waiting {
            draftQueue.enqueue(id, rank: Self.rank(capturedAt))
        }
    }

    private func handleScanError(_ error: Error, reach: HaulReach, id: UUID) {
        let appError = AppError.from(error)
        let now = deps.now()
        switch Self.disposition(for: appError, lastFailure: lastFailure,
                                offlineStreak: offlineStreak, reach: reach) {
        case .rateLimited(let wait):
            requeueScan(id)
            applyRateLimit(until: now.addingTimeInterval(wait))
        case .offline(let wait):
            attempts[id, default: 1] -= 1
            requeueScan(id)
            // The other request in flight reporting the same outage is not a
            // second one, and must not double the backoff.
            if case .offline(let until, _)? = pause, until > now { return }
            offlineStreak += 1
            // A timeout with no path is the phone being offline; say that,
            // not "The request timed out".
            let shown = reach == .neverConnected ? AppError.network : appError
            applyOffline(until: now.addingTimeInterval(wait), message: shown.errorDescription ?? "")
        case .entitlement:
            requeueScan(id)
            entitlementRefused(appError, lane: .scans)
        case .halt:
            requeueScan(id)
            halt(appError)
        case .failAndHalt:
            failScan(id, appError)
            halt(appError)
        case .fail:
            failScan(id, appError)
        }
    }

    /// Holds the scans behind "Try the rest". A subscription confirmation
    /// scheduled by the other request in flight is cancelled: it would lift
    /// this hold on its own and send under the halted banner.
    private func halt(_ error: AppError) {
        confirmTasks[.scans]?.cancel()
        confirmTasks[.scans] = nil
        scanQueue.hold()
        hold = .halted(error.errorDescription ?? "")
    }

    private func requeueScan(_ id: UUID) {
        guard let index = index(of: id) else { return }
        items[index].state = .queued
        scanQueue.requeue(id)
    }

    private func failScan(_ id: UUID, _ error: AppError) {
        guard let index = index(of: id) else { return }
        items[index].state = .failed(error.errorDescription)
        if items[index].draft == .waiting {
            items[index].draft = nil
            draftSnapshot.remove(id)
        }
        deps.store.mark(id, capturedAt: items[index].capturedAt, .failed)
        Analytics.shared.track(.scanFailed(reason: ScanFailureReason(error),
                                           isFirst: firstFlags[id] ?? false))
        if firstClaim == id { firstClaim = nil }
        if let signature = Self.signature(for: error) { lastFailure = signature }
        announce(String(localized: "Couldn't value this photo"))
    }

    private func celebrateIfDone() {
        guard celebrationDue, isOpen, pendingCount == 0, valuedCount > 0 else { return }
        celebrationDue = false
        let money = HistoryViewModel.money(total)
        guard failedCount > 0 else {
            // One haptic for the haul, not one per photo: a buzz per item
            // while the next one is being framed is noise.
            Haptics.success()
            announce(String(localized: "All photos valued. Estimated total: \(money)"))
            return
        }
        // Not "all photos valued", and no success haptic: some were not.
        // Two counts, so two plural keys — see ios/Localization/README.md.
        let valued = String(localized: "\(valuedCount) photos valued")
        let failed = String(localized: "\(failedCount) photos couldn't be valued")
        announce(String(localized: "\(valued), \(failed). Estimated total: \(money)"))
    }

    // ── One draft ────────────────────────────────────────────────────────

    private func runDraft(_ id: UUID) async {
        let end = deps.beginBackgroundTask("haul-draft")
        defer { end() }
        await performDraft(id)
        draftQueue.release(id)
        pump()
    }

    private func performDraft(_ id: UUID) async {
        guard let index = index(of: id), let result = items[index].result,
              let marketplace = draftMarketplace else { return }
        items[index].draft = .drafting
        let input = ListingInput(result: result, condition: result.condition)
        do {
            let listing = try await deps.generate(input, marketplace)
            noteSuccess(.drafts, id)
            guard let index = self.index(of: id) else { return }
            items[index].draft = .done(listing)
            Analytics.shared.track(.listingGenerated(marketplace: marketplace.rawValue))
        } catch {
            let appError = AppError.from(error)
            guard let index = self.index(of: id) else { return }
            if case .rateLimit(let retryAfter) = appError {
                items[index].draft = .waiting
                draftQueue.requeue(id)
                applyRateLimit(until: deps.now().addingTimeInterval(Self.clampedPause(retryAfter)))
            } else if appError.isPaywall {
                items[index].draft = .waiting
                draftQueue.requeue(id)
                entitlementRefused(appError, lane: .drafts)
            } else {
                items[index].draft = .failed(appError.errorDescription
                                             ?? String(localized: "Something went wrong. Please try again."))
            }
        }
    }

    // ── Waits and holds ──────────────────────────────────────────────────

    private func applyRateLimit(until: Date) {
        scanQueue.pause(until: until)
        draftQueue.pause(until: until)
        let deadline = max(rateLimitDeadline ?? until, until)
        rateLimitDeadline = deadline
        // Announced by `pause`'s didSet, the first time only.
        pause = .rateLimited(until: max(deadline, pause?.until ?? deadline))
        deps.defaults.set(deadline.timeIntervalSince1970, forKey: Self.rateLimitKey)
        scheduleResume(at: pause?.until ?? deadline)
    }

    private func applyOffline(until: Date, message: String) {
        scanQueue.pause(until: until)
        draftQueue.pause(until: until)
        if let current = pause, case .rateLimited = current, current.until >= until {
            scheduleResume(at: current.until)
            return
        }
        pause = .offline(until: max(until, pause?.until ?? until), message: message)
        scheduleResume(at: pause?.until ?? until)
    }

    private func scheduleResume(at until: Date) {
        if resumeAt == until, resumeTask != nil { return }
        resumeTask?.cancel()
        resumeAt = until
        let delay = max(0, until.timeIntervalSince(deps.now()))
        let sleep = deps.sleep
        resumeTask = Task { [weak self] in
            do { try await sleep(.milliseconds(Int64((delay * 1000).rounded()))) } catch { return }
            guard let self, !Task.isCancelled else { return }
            self.resumeTask = nil
            self.resumeAt = nil
            self.pump()
        }
    }

    /// A 402.
    ///
    /// While the app believes the user is Pro, it is almost always a purchase
    /// or restore the server has not seen yet — the entitlement sync is
    /// detached. That is not the paywall, and it is not a spent free scan:
    /// confirm and retry instead. Otherwise the user really is out of scans,
    /// and the scan-limit funnel records it exactly as a single scan would —
    /// but nothing here presents the paywall. Haul only holds, and the banner
    /// offers Upgrade.
    private func entitlementRefused(_ error: AppError, lane: Lane) {
        // A halt from the other request in flight stays: it is lifted by the
        // user, and a confirmation would lift it after 5 s on its own —
        // past, say, the 24-hour device pause. "Try the rest" sends again,
        // and a 402 then is handled from scratch.
        let halted = holdValue(lane)?.isHalted == true
        if deps.isEntitled() {
            // A response that was in flight before the hold: the retry is
            // already scheduled, and this is not a second refusal.
            if halted || (holdValue(lane) == .confirmingSubscription && confirmTasks[lane] != nil) {
                withQueue(lane) { $0.hold() }
                return
            }
            beginConfirming(lane)
            return
        }
        withQueue(lane) { $0.hold() }
        if !halted { setHold(lane, .quota(error.errorDescription ?? "")) }
        guard lane == .scans else { return }
        // What `ScanViewModel.startScan` does with a 402 — the server has
        // refused, so the counter must stop advertising a scan — minus
        // `showPaywall`.
        FreeScanCounter.serverRemaining = 0
        if !limitHitReported {
            limitHitReported = true
            Analytics.shared.track(.freeScanLimitHit)
        }
    }

    /// At most two retries an episode, at 5 s and then 20 s — so at most two
    /// rate-limit slots — then `.subscriptionUnconfirmed`.
    private func beginConfirming(_ lane: Lane) {
        withQueue(lane) { $0.hold() }
        let attempt = confirmAttempts[lane] ?? 0
        guard attempt < 2 else {
            confirmTasks[lane]?.cancel()
            confirmTasks[lane] = nil
            setHold(lane, .subscriptionUnconfirmed)
            return
        }
        confirmAttempts[lane] = attempt + 1
        setHold(lane, .confirmingSubscription)
        let delay: Duration = attempt == 0 ? .seconds(5) : .seconds(20)
        let sleep = deps.sleep
        confirmTasks[lane]?.cancel()
        confirmTasks[lane] = Task { [weak self] in
            guard let self else { return }
            await self.deps.refreshEntitlements()
            do { try await sleep(delay) } catch { return }
            guard !Task.isCancelled else { return }
            self.confirmTasks[lane] = nil
            self.releaseConfirmation(lane)
        }
    }

    /// The confirmation's retry. Only a queue still held for *this* reason is
    /// let go: a halt that arrived meanwhile — from the other request in
    /// flight — is the user's to lift, and sending under its banner would
    /// spend a request on what it just said to stop for.
    private func releaseConfirmation(_ lane: Lane) {
        guard holdValue(lane) == .confirmingSubscription else { return }
        withQueue(lane) { $0.unhold(); $0.beginProbing() }
        pump()
    }

    /// `id` went through.
    ///
    /// Whatever it was claimed under, the server and the network work, so the
    /// breaker's run and the offline streak end. What it says about the
    /// *budget* and the *entitlement* depends on when it was sent: a request
    /// claimed before the latest pause or hold — usually the one that took
    /// the 20th slot while the other got the 429 — answers for the moment it
    /// was admitted, not for now. Only a request sent since ends the probe,
    /// forgets the deadline (in memory and on disk) or confirms the
    /// subscription.
    private func noteSuccess(_ lane: Lane, _ id: UUID) {
        if lane == .scans {
            lastFailure = nil
            offlineStreak = 0
        }
        let isCurrent = lane == .scans ? scanQueue.isCurrent(id) : draftQueue.isCurrent(id)
        guard isCurrent else { return }
        scanQueue.succeeded()
        draftQueue.succeeded()
        rateLimitDeadline = nil
        deps.defaults.removeObject(forKey: Self.rateLimitKey)
        confirmAttempts[lane] = 0
        if lane == .scans { limitHitReported = false }
        if holdValue(lane) == .confirmingSubscription {
            confirmTasks[lane]?.cancel()
            confirmTasks[lane] = nil
            withQueue(lane) { $0.unhold() }
            setHold(lane, nil)
        }
    }

    // ── Preparation ──────────────────────────────────────────────────────

    private enum Prepared: Sendable {
        /// `unsaved` is the JPEG when the disk write failed.
        case ready(thumbnail: UIImage, unsaved: Data?)
        case encodeFailed
    }

    private func prepare(id: UUID, capturedAt: Date, photo: UIImage,
                         end: @escaping @MainActor () -> Void) {
        let previous = prepTail
        let store = deps.store
        let edge = Self.thumbnailEdge
        prepTail = Task { [weak self] in
            await previous?.value
            let prepared = await Task.detached(priority: .userInitiated) { () -> Prepared in
                let upload = ScanAPIClient.downscale(photo, maxEdge: ScanAPIClient.maxUploadEdge)
                guard let jpeg = upload.jpegData(compressionQuality: 0.8) else { return .encodeFailed }
                let thumbnail = ScanAPIClient.downscale(upload, maxEdge: edge)
                do {
                    try store.save(jpeg, id: id, capturedAt: capturedAt)
                    return .ready(thumbnail: thumbnail, unsaved: nil)
                } catch {
                    return .ready(thumbnail: thumbnail, unsaved: jpeg)
                }
            }.value
            self?.finishPreparing(id, capturedAt: capturedAt, prepared, photo: photo)
            end()
        }
    }

    private func finishPreparing(_ id: UUID, capturedAt: Date, _ prepared: Prepared, photo: UIImage) {
        guard let index = index(of: id) else {
            // Removed while it was being prepared — Clear history. Its file
            // would otherwise come back on the next open.
            deps.store.remove(id: id, capturedAt: capturedAt)
            return
        }
        switch prepared {
        case .encodeFailed:
            unpreparedPhotos[id] = photo
            items[index].state = .failed(AppError.imageEncodingFailed.errorDescription)
            announce(String(localized: "Couldn't value this photo"))
        case .ready(let thumbnail, let unsaved):
            items[index].thumbnail = thumbnail
            items[index].state = .queued
            if let unsaved { memoryJPEG[id] = unsaved }
            scanQueue.enqueue(id, rank: Self.rank(capturedAt))
        }
        pump()
    }

    // ── Helpers ──────────────────────────────────────────────────────────

    private func index(of id: UUID) -> Int? {
        items.firstIndex { $0.id == id }
    }

    private static func rank(_ capturedAt: Date) -> Int {
        Int(HaulPhotoStore.millis(capturedAt))
    }

    private func forget(_ id: UUID) {
        scanQueue.remove(id)
        draftQueue.remove(id)
        draftSnapshot.remove(id)
        memoryJPEG[id] = nil
        unpreparedPhotos[id] = nil
        attempts[id] = nil
        if firstClaim == id { firstClaim = nil }
    }

    private func dropUnsent() {
        let dropped = items.filter {
            if case .scanning = $0.state { return false }
            return true
        }.map(\.id)
        for id in dropped { forget(id) }
        items.removeAll { dropped.contains($0.id) }
    }

    private func holdValue(_ lane: Lane) -> HaulHold? {
        lane == .scans ? hold : draftHold
    }

    private func setHold(_ lane: Lane, _ value: HaulHold?) {
        switch lane {
        case .scans:  hold = value
        case .drafts: draftHold = value
        }
    }

    private func withQueue(_ lane: Lane, _ body: (inout HaulQueue<UUID>) -> Void) {
        switch lane {
        case .scans:  body(&scanQueue)
        case .drafts: body(&draftQueue)
        }
    }

    /// Queued behind whatever VoiceOver is saying rather than cutting it off:
    /// a failed photo and the halt it trips arrive in the same turn, and the
    /// second would otherwise silence the first. Nothing with Haul closed —
    /// a scan landing late would speak over whatever screen is up.
    private func announce(_ message: String) {
        guard isOpen, UIAccessibility.isVoiceOverRunning else { return }
        let queued = NSAttributedString(string: message,
                                        attributes: [.accessibilitySpeechQueueAnnouncement: true])
        UIAccessibility.post(notification: .announcement, argument: queued)
    }
}

// MARK: - The error policy

extension HaulSession {
    /// Pure and `nonisolated`, like `CameraManager.flashMode`, so each row of
    /// the policy is tested apart from any server.
    ///
    /// * **429** waits for the server's `Retry-After`, clamped: at least 5 s
    ///   so a re-send is never the same turn, at most an hour, which is the
    ///   window itself.
    /// * **402** is the entitlement, not the photo.
    /// * **No connection** backs off 15 → 30 → 60 → 120 → 300 s. Nothing
    ///   reached the server, so a probe costs nothing. That covers the
    ///   connect-time failures, and a timeout with no network path from send
    ///   to failure (`HaulReach.neverConnected`) — which is how offline
    ///   usually arrives, the API session waiting for connectivity until it
    ///   times out.
    /// * **A dropped connection** (`HaulReach.droppedMidRequest`) fails the
    ///   photo, like a timeout: the upload may have landed and been charged.
    /// * **503** backs off like no connection, twice more — a 503 may use a
    ///   slot — and then halts: three in a row is an outage.
    /// * **An expired session** halts: the client already re-minted once, so
    ///   every photo would fail the same way.
    /// * **A timeout** fails the photo and offers Try again. At 35 s the
    ///   server has usually finished and charged the slot and the model call;
    ///   a silent re-send doubles that.
    /// * **The breaker**: two server failures in a row with the same case and
    ///   message fail the second photo too and halt the rest. It catches an
    ///   outage that arrives as `.aiFailed`, and the 24-hour "device paused"
    ///   422 — which, arriving for every photo, would otherwise turn the
    ///   whole strip red.
    /// * **A local encoding failure** fails the photo and never trips the
    ///   breaker: it used no slot and says nothing about the server.
    nonisolated static func disposition(for error: AppError,
                                        lastFailure: HaulFailureSignature?,
                                        offlineStreak: Int,
                                        reach: HaulReach = .unknown) -> HaulDisposition {
        if error.isPaywall { return .entitlement }
        switch error {
        case .rateLimit(let retryAfter):
            return .rateLimited(clampedPause(retryAfter))
        case .network where reach == .droppedMidRequest:
            return .fail
        case .network:
            return .offline(offlineBackoff(streak: offlineStreak))
        case .timeout where reach == .neverConnected:
            return .offline(offlineBackoff(streak: offlineStreak))
        case .serverUnavailable:
            return offlineStreak < 3 ? .offline(offlineBackoff(streak: offlineStreak)) : .halt
        case .sessionExpired:
            return .halt
        case .imageEncodingFailed:
            return .fail
        default:
            if let signature = signature(for: error), signature == lastFailure { return .failAndHalt }
            return .fail
        }
    }

    /// Reads, before the error is flattened into an `AppError`, what the
    /// transport knows about whether the request reached the server.
    nonisolated static func reach(of error: Error, pathAtSend: HaulNetworkPath,
                                  pathNow: HaulNetworkPath) -> HaulReach {
        if (error as? URLError)?.code == .networkConnectionLost { return .droppedMidRequest }
        let neverSatisfied = !pathAtSend.isSatisfied && !pathNow.isSatisfied
            && pathAtSend.satisfiedCount == pathNow.satisfiedCount
        return neverSatisfied ? .neverConnected : .unknown
    }

    nonisolated static func clampedPause(_ retryAfter: TimeInterval?) -> TimeInterval {
        min(maxPause, max(minPause, retryAfter ?? defaultPause))
    }

    nonisolated static func offlineBackoff(streak: Int) -> TimeInterval {
        let steps: [TimeInterval] = [15, 30, 60, 120, 300]
        return steps[min(max(0, streak), steps.count - 1)]
    }

    /// What the breaker compares, for a failure that came from the server.
    /// Nil for anything local.
    nonisolated static func signature(for error: AppError) -> HaulFailureSignature? {
        switch error {
        case .timeout:                return HaulFailureSignature(kind: "timeout", message: "")
        // A refused connection refuses the next photo too; it tripped the
        // breaker as `.unknown` before it had a case of its own, and should.
        case .connectionNotTrusted:   return HaulFailureSignature(kind: "connectionNotTrusted", message: "")
        case .unusablePhoto(let msg): return HaulFailureSignature(kind: "unusablePhoto", message: msg)
        case .aiFailed(let msg):      return HaulFailureSignature(kind: "aiFailed", message: msg)
        case .unknown(let msg):       return HaulFailureSignature(kind: "unknown", message: msg)
        default:                      return nil
        }
    }
}

// MARK: - A capture becomes a normal scan

extension HaulSession {
    /// Everything `ScanViewModel.startScan` does after a successful response,
    /// in the same order — that function is the reference, and a change to
    /// one belongs in the other.
    ///
    /// Two deliberate differences. The scan is counted for the review prompt
    /// and nothing is asked here — `recordSuccessfulScan` only counts, as it
    /// does for every path; Haul asks once, from its summary. And
    /// there is no `Haptics.success()` here — Haul plays one when the queue
    /// empties.
    ///
    /// `timestamp` is the capture time, not now: a photo restored days later
    /// lands in My Finds, and in the monthly recap, under the day it was taken.
    static func recordNormalScan(_ response: ScanAPIResponse, image: UIImage, capturedAt: Date,
                                 purchaseService: any PurchaseService,
                                 repository: ScanRepository) async -> (ScanResult, saved: Bool) {
        let jpegData = await ScanAPIClient.encodeForStorage(image)
        let result = ScanResult(
            timestamp: capturedAt,
            itemName: response.itemName,
            brand: response.brand,
            category: response.category,
            conditionNotes: response.conditionNotes,
            valueLow: response.estValueLowUsd,
            valueHigh: response.estValueHighUsd,
            confidence: response.confidence,
            soldListingsCount: response.soldListingsCount,
            listingTitle: response.listingTitle,
            listingDescription: response.listingDescription,
            imageData: jpegData,
            valuationDetailData: ValuationDetail(response: response)?.encoded()
        )

        if !purchaseService.isSubscribed {
            FreeScanCounter.increment()
            FreeScanCounter.serverRemaining = response.freeScansRemaining
        }

        Analytics.shared.track(
            .scanCompleted(success: true, category: ScanCategory(normalizing: response.category))
        )
        if let milestone = ScanTally.record() {
            Analytics.shared.track(.scanCountMilestone(count: milestone))
        }
        ScanViewModel.noteScanForStreakAndReminder(isPro: purchaseService.isSubscribed)
        ReviewPrompt.recordSuccessfulScan()

        let backup = result.detachedCopy()
        do {
            try repository.save(result)
            return (result, true)
        } catch {
            // As in `startScan`: only a rolled-back insert needs the copy.
            // `.storeUnavailable` is thrown before the insert, so `result` is
            // still a valid, unregistered object.
            if let failure = error as? ScanPersistenceError, case .saveFailed = failure {
                return (backup, false)
            }
            return (result, false)
        }
    }
}

// MARK: - Background time

/// One `beginBackgroundTask`, ended exactly once — by the work finishing or
/// by the expiration handler, whichever comes first.
@MainActor
private final class HaulBackgroundTask {
    private var identifier: UIBackgroundTaskIdentifier = .invalid
    private var ended = false

    init(name: String) {
        identifier = UIApplication.shared.beginBackgroundTask(withName: name) { [weak self] in
            MainActor.assumeIsolated { self?.end() }
        }
    }

    func end() {
        guard !ended else { return }
        ended = true
        if identifier != .invalid {
            UIApplication.shared.endBackgroundTask(identifier)
        }
        identifier = .invalid
    }
}

// MARK: - Network path

/// The device's network path, for `HaulSession.reach(of:)`.
///
/// Starts optimistic — satisfied — until its first reading, which arrives
/// almost at once: an unknown path then leaves a timeout failing, as it
/// always did, rather than guessing it offline.
@MainActor
final class HaulPathMonitor {
    static let shared = HaulPathMonitor()

    private(set) var current = HaulNetworkPath(isSatisfied: true, satisfiedCount: 0)
    private let monitor = NWPathMonitor()

    private init() {
        monitor.pathUpdateHandler = { [weak self] path in
            let satisfied = path.status == .satisfied
            MainActor.assumeIsolated { self?.update(satisfied: satisfied) }
        }
        monitor.start(queue: .main)
    }

    private func update(satisfied: Bool) {
        if satisfied, !current.isSatisfied { current.satisfiedCount += 1 }
        current.isSatisfied = satisfied
    }
}
