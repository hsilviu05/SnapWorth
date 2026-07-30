import SwiftData
import SwiftUI

/// Owns all SwiftData persistence for ScanResult.
/// ViewModels call this instead of touching ModelContext directly.
@MainActor
final class ScanRepository {
    private let context: ModelContext

    init(context: ModelContext) {
        self.context = context
    }

    func save(_ result: ScanResult) throws {
        context.insert(result)
        do {
            try context.save()
        } catch {
            throw AppError.persistence
        }
        scheduleWidgetSync()
    }

    func delete(_ result: ScanResult) throws {
        let id = result.id
        context.delete(result)
        do {
            try context.save()
        } catch {
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
            throw AppError.persistence
        }
        NotificationManager.shared.cancelAllLedger()
        WidgetDataStore.writeHaul(results: [])
    }

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
    private func scheduleWidgetSync() {
        let context = self.context
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(600))
            let all = (try? context.fetch(FetchDescriptor<ScanResult>())) ?? []
            WidgetDataStore.writeHaul(results: all)
        }
    }
}
