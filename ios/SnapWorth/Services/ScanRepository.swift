import SwiftData
import SwiftUI

/// A persistence failure that hands back something safe to keep on screen.
///
/// `AppError.from` maps this to `.persistence` like any other storage error, so
/// no caller that only wants to report a failure has to know about it.
enum ScanPersistenceError: Error {
    /// The insert was rolled back. `replacement` is a context-free copy of the
    /// row, taken before the insert, for a caller that is already displaying it.
    case saveFailed(replacement: ScanResult)
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
        // Seeds the denormalised portfolio value and the first history point.
        // Done here rather than in the model's init so every persisted row has
        // one, including any future call site that builds a ScanResult
        // differently.
        result.refreshPortfolioValue()
        // Taken before the insert, so the rollback below cannot reach it. The
        // result sheet is already on screen holding `result`; see
        // `detachedCopy()`.
        let replacement = result.detachedCopy()
        context.insert(result)
        do {
            try context.save()
        } catch {
            context.rollback()
            throw ScanPersistenceError.saveFailed(replacement: replacement)
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

    /// Recomputes the widget's haul summary, off the presentation path.
    ///
    /// The aggregate genuinely needs every record, so running it synchronously
    /// inside `save` put an O(history) main-actor fetch directly in the way of
    /// the result sheet's presentation animation. Deferring lets the sheet
    /// settle first; a widget has no latency requirement.
    ///
    /// Captures the `ModelContext`, not `self`. Repositories are constructed
    /// per call site as locals (`ScanRepository(context: modelContext)`), so a
    /// `[weak self]` capture would be nil by the time this ran and the widget
    /// would silently stop updating. The context outlives the repository.
    /// Re-sync the widget after something other than an insert or a delete
    /// changed a value.
    ///
    /// Changing an item's condition re-prices it — the only way a value moves
    /// without a row being added or removed — and nothing told the widget, so
    /// it kept showing the pre-correction total until the next scan.
    func refreshWidget() {
        scheduleWidgetSync()
    }

    private func scheduleWidgetSync() {
        let context = self.context
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(600))
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
