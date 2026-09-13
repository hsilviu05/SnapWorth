import SwiftData
import SwiftUI

/// A persistence failure.
///
/// `AppError.from` maps this to `.persistence` like any other storage error, so
/// no caller that only wants to report a failure has to know about it.
///
/// **No payload, deliberately.** Both cases used to carry the `ScanResult` a
/// caller should keep displaying after a rollback. `Error` requires `Sendable`
/// under Swift 6 and a SwiftData `@Model` is not one, so the compiler flagged
/// both cases — correctly in principle, even though nothing here crosses an
/// actor: this type, the repository and both view models are all `@MainActor`,
/// and the one non-isolated reader (`AppError.from`) matches on the case and
/// never touched the payload.
///
/// `@unchecked Sendable` would have silenced it by asserting something untrue
/// of a managed model. The real problem was the design: a persistence error is
/// the wrong place to carry a view's display object, and a repository has no
/// business deciding what a screen shows next. A caller that needs to survive
/// the rollback takes its own `detachedCopy()` before calling `save` — which is
/// also where the knowledge of whether it needs one lives.
enum ScanPersistenceError: Error {
    /// The insert was rolled back, so the row passed to `save` is no longer
    /// registered with any context.
    case saveFailed

    /// The store failed to open at launch, so this session is running on a
    /// throwaway in-memory container and nothing written to it survives.
    ///
    /// Separate from `saveFailed` because the honest thing to say is different:
    /// that one is a write that failed and can be retried, this one is a write
    /// that would *succeed* and be discarded at quit. Thrown before the insert,
    /// so the caller's row is untouched.
    case storeUnavailable
}

/// Owns all SwiftData persistence for ScanResult.
/// ViewModels call this instead of touching ModelContext directly.
@MainActor
final class ScanRepository {
    private let context: ModelContext

    init(context: ModelContext) {
        self.context = context
    }

    // ── Why every failure path rolls back ────────────────────────────────────
    //
    // `context` here is `sharedModelContainer.mainContext` (injected by
    // `.modelContainer` in SnapWorthApp), and nothing in the app sets
    // `autosaveEnabled`, so it is on. A failed `save()` used to leave the
    // pending change sitting in that shared context, which has two
    // consequences:
    //
    //   1. SwiftData saves the *whole* context, so one unsavable change makes
    //      every later `context.save()` in the session fail too, from any call
    //      site. Delete a find, add a find, clear history — all broken for the
    //      rest of the launch by one bad row.
    //   2. Autosave keeps retrying the change the user was told had failed, so
    //      the row can appear later anyway, contradicting the message.
    //
    // The existing tests could not see it: they build on `ModelContext(container)`,
    // a secondary context whose autosave defaults to *false*.

    func save(_ result: ScanResult) throws {
        // A write to a fallback store is not a save.
        //
        // When the on-disk store cannot be opened, `SnapWorthApp` substitutes
        // an in-memory container so the app still runs — and `context.save()`
        // against it *succeeds*. Nothing on this path consulted the flag, so
        // the session behaved exactly like a healthy one: the server charged a
        // quota unit, the free-scan counter decremented, and the result sheet
        // told the user the find was added to My Finds. It showed in History
        // for the rest of the session and was gone on the next launch — a scan
        // they paid for, positively claimed as saved, beside a library that
        // looked empty for no stated reason.
        //
        // Thrown before the insert, so `result` is untouched and the caller
        // keeps the row it is already displaying.
        guard !AppLaunchState.isRunningOnFallbackStore else {
            throw ScanPersistenceError.storeUnavailable
        }
        // Seeds the denormalised portfolio value and the first history point.
        // Done here rather than in the model's init so every persisted row has
        // one, including any future call site that builds a ScanResult
        // differently.
        result.refreshPortfolioValue()
        context.insert(result)
        do {
            try context.save()
        } catch {
            // Rolls back, so `result` is no longer registered with any context.
            // A caller still displaying it swaps in a copy it took beforehand —
            // see `ScanPersistenceError`.
            context.rollback()
            throw ScanPersistenceError.saveFailed
        }
        scheduleWidgetSync()
    }

    func delete(_ result: ScanResult) throws {
        let id = result.id
        context.delete(result)
        do {
            try context.save()
        } catch {
            // Safe without a copy: these rows are already persisted, so
            // rollback restores them to their stored state rather than
            // discarding them.
            context.rollback()
            throw AppError.persistence
        }
        // No orphaned ledger follow-up for a deleted item.
        Task { await NotificationManager.shared.cancelLedgerFollowUp(itemID: id) }
        scheduleWidgetSync()
    }

    func deleteAll(_ results: [ScanResult]) throws {
        results.forEach { context.delete($0) }
        do {
            try context.save()
        } catch {
            context.rollback()
            throw AppError.persistence
        }
        NotificationManager.shared.cancelAllLedger()
        WidgetDataStore.writeHaul(results: [])
        // And the run, which is computed from the same array. Every other
        // mutation goes through `scheduleWidgetSync`, which updates both; this
        // one wrote the blob directly and left the Live Activity showing the
        // total of scans that no longer exist.
        Task { await ThriftRunController.update(results: []) }
    }

    // ── On the portfolio total, and why there is no aggregate here ────────────
    //
    // An earlier pass added a `portfolioSummary()` using
    // `FetchDescriptor.propertiesToFetch` to sum without materialising the
    // externalStorage image blobs. It was removed because it does not pay off
    // in this app, and dead code that advertises an optimisation nothing
    // performs is worse than no code.
    //
    // `HistoryView` lists scans with `@Query`, so every row is already resident
    // when the banner renders. A second fetch to sum them would be *additive*
    // cost, not a saving. The reduce itself is cheap and got cheaper:
    // `portfolioValue` reads the denormalised `portfolioValueRaw` column when
    // present, so a touched library sums plain Decimals instead of running a
    // condition-adjusted division per item per render.
    //
    // The aggregate becomes worth having the moment the list stops loading
    // everything — i.e. when it is paged. That is a larger change than this
    // feature, and doing half of it here would only look like the work.

    func fetchAll() -> [ScanResult] {
        (try? context.fetch(FetchDescriptor<ScanResult>())) ?? []
    }

    /// Number of scans recorded in the current calendar month.
    ///
    /// Uses `fetchCount` with a date predicate rather than fetching every
    /// record and filtering in memory. The old form was O(history) on the main
    /// actor and ran on the result-presentation path, so its cost grew for the
    /// lifetime of the install.
    ///
    /// The predicate compares against a precomputed month boundary because
    /// SwiftData predicates cannot call `Calendar` APIs.
    func countScansThisMonth(now: Date = Date()) -> Int {
        let calendar = Calendar.current
        guard let start = calendar.dateInterval(of: .month, for: now)?.start else {
            return 0
        }
        let descriptor = FetchDescriptor<ScanResult>(
            predicate: #Predicate { $0.timestamp >= start }
        )
        return (try? context.fetchCount(descriptor)) ?? 0
    }

    /// Re-sync the widget after something other than an insert or a delete
    /// changed a value.
    ///
    /// Changing an item's condition re-prices it — the only way a value moves
    /// without a row being added or removed — and nothing told the widget, so
    /// it kept showing the pre-correction total until the next scan.
    func refreshWidget() {
        scheduleWidgetSync()
    }

    /// The sync waiting to run, so the next one can cancel it.
    ///
    /// `static`, and that is the whole point rather than an oversight: a
    /// repository is constructed per call site as a local
    /// (`ScanRepository(context: modelContext)` — `HistoryView.repository` is
    /// a *computed* property, so even one view makes a new one every time), so
    /// an instance property could never see the previous call's task and the
    /// cancellation would be a no-op. The class is `@MainActor`, so this is
    /// main-actor state: there is no race to guard.
    ///
    /// Readable from the tests — `private(set)` — because cancellation is the
    /// whole behaviour and there is nothing else to observe: the work itself
    /// writes to the App Group, which a test host has no access to.
    private(set) static var widgetSync: Task<Void, Never>?

    /// Recomputes the widget's haul summary, off the presentation path,
    /// coalescing a burst of mutations into one write.
    ///
    /// The aggregate genuinely needs every record, so running it synchronously
    /// inside `save` put an O(history) main-actor fetch directly in the way of
    /// the result sheet's presentation animation. Deferring lets the sheet
    /// settle first; a widget has no latency requirement.
    ///
    /// The delay alone was not a debounce, though the comment here called it
    /// one. Each call started its own detached `Task`, so clearing a
    /// twenty-item history ran twenty full-history fetches, twenty blob writes
    /// and twenty `reloadAllTimelines()` calls 600ms apart — nineteen of them
    /// computing a result identical to the last. Cancelling the pending task
    /// is what makes the delay do what it claimed.
    ///
    /// `Task.sleep` throws on cancellation and `try?` swallows that, so the
    /// explicit `isCancelled` check is what actually stops the work; without
    /// it a cancelled sync would sleep, wake, and carry on regardless.
    ///
    /// Captures the `ModelContext`, not `self`. Repositories are constructed
    /// per call site as locals, so a `[weak self]` capture would be nil by the
    /// time this ran and the widget would silently stop updating. The context
    /// outlives the repository.
    private func scheduleWidgetSync() {
        Self.widgetSync?.cancel()
        let context = self.context
        Self.widgetSync = Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(600))
            guard !Task.isCancelled else { return }
            let all = (try? context.fetch(FetchDescriptor<ScanResult>())) ?? []
            WidgetDataStore.writeHaul(results: all)
            // Same hook, same debounce. A thrift run moves for exactly the
            // reasons the widgets do — a scan added, removed, or re-graded —
            // so giving it its own trigger would be a second thing to keep in
            // step with this one.
            await ThriftRunController.update(results: all)
        }
    }
}
