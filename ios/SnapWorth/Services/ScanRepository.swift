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
        syncWidget()
    }

    func delete(_ result: ScanResult) throws {
        context.delete(result)
        do {
            try context.save()
        } catch {
            throw AppError.persistence
        }
        syncWidget()
    }

    func deleteAll(_ results: [ScanResult]) throws {
        results.forEach { context.delete($0) }
        do {
            try context.save()
        } catch {
            throw AppError.persistence
        }
        WidgetDataStore.writeHaul(results: [])
    }

    func fetchAll() -> [ScanResult] {
        (try? context.fetch(FetchDescriptor<ScanResult>())) ?? []
    }

    private func syncWidget() {
        let all = fetchAll()
        WidgetDataStore.writeHaul(results: all)
    }
}
