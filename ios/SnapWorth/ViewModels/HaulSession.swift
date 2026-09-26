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
            claimed.append(id)
        }
        return claimed
    }

    mutating func release(_ id: ID) {
        inFlight.remove(id)
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

    mutating func hold() { isHeld = true }
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
    /// This photo only: mark it failed and keep it.
    case fail
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
        /// Returns `end`, which is idempotent.
        var beginBackgroundTask: @MainActor (String) -> @MainActor () -> Void
        /// The haul summary was reached for the first time in an open.
        var summaryReached: @MainActor () -> Void
        var sleep: @Sendable (Duration) async throws -> Void
        var now: () -> Date
        var defaults: UserDefaults
        var store: HaulPhotoStore
    }

    // ── Observed state ───────────────────────────────────────────────────
    /// Capture order, which is strip order.
    private(set) var items: [HaulItem] = []
    private(set) var pause: HaulPause?
    private(set) var hold: HaulHold?
    private(set) var draftHold: HaulHold?
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
    @ObservationIgnored private var reportedCompletion = false
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
            beginBackgroundTask: { name in
                let task = HaulBackgroundTask(name: name)
                return { task.end() }
            },
            summaryReached: {
                // Moved here from each scan: a haul is one sitting, and a
                // rating sheet or a recap schedule per photo would interrupt
                // the camera it is meant to celebrate.
                let monthScans = repository.countScansThisMonth()
                Task { await NotificationManager.shared.scheduleMonthlyRecap(monthScanCount: monthScans) }
                Task {
                    try? await Task.sleep(for: .seconds(1.2))
                    ReviewPrompt.promptIfDue()
                }
            },
            sleep: { try await Task.sleep(for: $0) },
            now: { Date() },
            defaults: .standard,
            store: .live))
        shared = session
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
    /// Preparation is chained: each photo waits for the one before it, so
    /// only one full-size decode — 48.8 MB for 12 MP — is alive at a time.
    /// The full image is dropped as soon as its 1568 px JPEG is on disk; the
    /// JPEG itself is read back when its scan starts.
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
                    self.withQueue(lane) { $0.unhold(); $0.beginProbing() }
                    self.pump()
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
                confirmAttempts[lane] = 0
                if lane == .scans { limitHitReported = false }
                beginConfirming(lane)
            default:
                break
            }
        }
    }

    /// The first Finish of an open.
    ///
    /// Here and not in the view's `onDisappear`: a haul most often ends with
    /// the app killed from the switcher while the summary is on screen, which
    /// never fires `onDisappear`. It also puts `haul_completed` before any
    /// `haul_shared`.
    func didReachSummary() {
        guard !reportedCompletion else { return }
        let count = valuedCount + pendingCount
        guard count > 0 else { return }
        reportedCompletion = true
        Analytics.shared.track(.haulCompleted(itemsBucket: AnalyticsEvent.haulSizeBucket(count)))
        deps.summaryReached()
    }

    /// Done. Valued items leave the haul — they are in My Finds — and pending
    /// or failed ones stay for the next open. Scans in flight finish and save.
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
        reportedCompletion = false
    }

    // ── Drafts ───────────────────────────────────────────────────────────

    /// Snap → Sell for every item valued or pending now, for one marketplace.
    ///
    /// Photos captured afterwards are not drafted: the confirmation the user
    /// agreed to named a number. Drafts run one at a time and only while no
    /// scan is waiting — a photo cannot be taken again, a draft can.
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
        // Scans get the shared budget first.
        if scanQueue.isIdle {
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

        do {
            let response = try await deps.scan(image)
            let (result, saved) = await deps.record(response, image, capturedAt)
            succeed(id, capturedAt: capturedAt, result: result, saved: saved)
        } catch {
            handleScanError(error, id: id)
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
        noteSuccess(.scans)
        celebrationDue = true
        guard let index = index(of: id) else { return }
        items[index].state = .done(result, saved: saved)
        if draftSnapshot.contains(id), items[index].draft == .waiting {
            draftQueue.enqueue(id, rank: Self.rank(capturedAt))
        }
    }

    private func handleScanError(_ error: Error, id: UUID) {
        let appError = AppError.from(error)
        let now = deps.now()
        switch Self.disposition(for: appError, lastFailure: lastFailure, offlineStreak: offlineStreak) {
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
            applyOffline(until: now.addingTimeInterval(wait), message: appError.errorDescription ?? "")
        case .entitlement:
            requeueScan(id)
            entitlementRefused(appError, lane: .scans)
        case .halt:
            requeueScan(id)
            scanQueue.hold()
            hold = .halted(appError.errorDescription ?? "")
        case .fail:
            failScan(id, appError)
        }
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
    }

    private func celebrateIfDone() {
        guard celebrationDue, isOpen, pendingCount == 0, valuedCount > 0 else { return }
        celebrationDue = false
        // One haptic for the haul, not one per photo: a buzz per item while
        // the next one is being framed is noise.
        Haptics.success()
        announce(String(localized: "All photos valued. Estimated total: \(HistoryViewModel.money(total))"))
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
            noteSuccess(.drafts)
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
        let wasRateLimited: Bool
        if case .rateLimited? = pause { wasRateLimited = true } else { wasRateLimited = false }
        pause = .rateLimited(until: max(deadline, pause?.until ?? deadline))
        deps.defaults.set(deadline.timeIntervalSince1970, forKey: Self.rateLimitKey)
        if !wasRateLimited {
            announce(AppError.rateLimitMessage(retryAfter: deadline.timeIntervalSince(deps.now())))
        }
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
        if deps.isEntitled() {
            // A response that was in flight before the hold: the retry is
            // already scheduled, and this is not a second refusal.
            if holdValue(lane) == .confirmingSubscription, confirmTasks[lane] != nil {
                withQueue(lane) { $0.hold() }
                return
            }
            beginConfirming(lane)
            return
        }
        withQueue(lane) { $0.hold() }
        setHold(lane, .quota(error.errorDescription ?? ""))
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
            self.withQueue(lane) { $0.unhold(); $0.beginProbing() }
            self.pump()
        }
    }

    private func noteSuccess(_ lane: Lane) {
        scanQueue.succeeded()
        draftQueue.succeeded()
        rateLimitDeadline = nil
        deps.defaults.removeObject(forKey: Self.rateLimitKey)
        confirmAttempts[lane] = 0
        if holdValue(lane) == .confirmingSubscription {
            confirmTasks[lane]?.cancel()
            confirmTasks[lane] = nil
            withQueue(lane) { $0.unhold() }
            setHold(lane, nil)
        }
        if lane == .scans {
            lastFailure = nil
            offlineStreak = 0
            limitHitReported = false
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

    private func announce(_ message: String) {
        guard UIAccessibility.isVoiceOverRunning else { return }
        UIAccessibility.post(notification: .announcement, argument: message)
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
    ///   reached the server, so a probe costs nothing.
    /// * **503** backs off like no connection, twice more — a 503 may use a
    ///   slot — and then halts: three in a row is an outage.
    /// * **An expired session** halts: the client already re-minted once, so
    ///   every photo would fail the same way.
    /// * **A timeout** fails the photo and offers Try again. At 35 s the
    ///   server has usually finished and charged the slot and the model call;
    ///   a silent re-send doubles that.
    /// * **The breaker**: two server failures in a row with the same case and
    ///   message halt the queue. It catches an outage that arrives as
    ///   `.aiFailed`, and the 24-hour "device paused" 422 — which, arriving
    ///   for every photo, would otherwise turn the whole strip red.
    /// * **A local encoding failure** fails the photo and never trips the
    ///   breaker: it used no slot and says nothing about the server.
    nonisolated static func disposition(for error: AppError,
                                        lastFailure: HaulFailureSignature?,
                                        offlineStreak: Int) -> HaulDisposition {
        if error.isPaywall { return .entitlement }
        switch error {
        case .rateLimit(let retryAfter):
            return .rateLimited(clampedPause(retryAfter))
        case .network:
            return .offline(offlineBackoff(streak: offlineStreak))
        case .serverUnavailable:
            return offlineStreak < 3 ? .offline(offlineBackoff(streak: offlineStreak)) : .halt
        case .sessionExpired:
            return .halt
        case .imageEncodingFailed:
            return .fail
        default:
            if let signature = signature(for: error), signature == lastFailure { return .halt }
            return .fail
        }
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
    /// Two deliberate differences. The review prompt is counted but not asked
    /// (`promptingIfDue: false`): Haul asks once, from its summary. And
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
            .scanCompleted(success: true, category: ItemCategory(normalizing: response.category))
        )
        if let milestone = ScanTally.record() {
            Analytics.shared.track(.scanCountMilestone(count: milestone))
        }
        ScanViewModel.noteScanForStreakAndReminder(isPro: purchaseService.isSubscribed)
        ReviewPrompt.recordSuccessfulScan(promptingIfDue: false)

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
