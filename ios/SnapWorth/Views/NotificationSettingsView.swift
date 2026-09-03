import SwiftUI
import UIKit

/// Per-category opt-outs for local notifications. All ON by default; each can be
/// disabled independently and is respected at schedule time by NotificationManager.
struct NotificationSettingsView: View {
    /// Whether the user is Pro — the free-scan reminder is meaningless then
    /// and is hidden rather than shown disabled.
    var isPro: Bool = false

    @State private var recapOn  = NotificationManager.shared.isEnabled(.recap)
    @State private var ledgerOn = NotificationManager.shared.isEnabled(.ledger)
    @State private var trialOn  = NotificationManager.shared.isEnabled(.trial)
    @State private var portfolioOn = NotificationManager.shared.isEnabled(.portfolio)
    @State private var freeScanOn = NotificationManager.shared.isEnabled(.freeScan)
    @State private var freeScanTime: Date = {
        let t = NotificationManager.shared.freeScanReminderTime
        return Calendar.current.date(bySettingHour: t.hour, minute: t.minute, second: 0, of: Date()) ?? Date()
    }()
    @State private var systemDenied = false

    var body: some View {
        List {
            if systemDenied {
                Section {
                    Button {
                        if let url = URL(string: UIApplication.openSettingsURLString) {
                            UIApplication.shared.open(url)
                        }
                    } label: {
                        HStack(spacing: 14) {
                            Image(systemName: "bell.slash")
                                .snapSymbol(16, weight: .medium)
                                .foregroundStyle(Color.snapTerracotta)
                                .frame(width: 24)
                            VStack(alignment: .leading, spacing: 2) {
                                Text("Notifications are off")
                                    .font(.snapBody)
                                    .foregroundStyle(Color.snapEspresso)
                                Text("Turn them on in iOS Settings to get these reminders.")
                                    .font(.snapCaption)
                                    .foregroundStyle(Color.snapWarmGray)
                            }
                        }
                    }
                }
            }

            if !isPro {
                Section {
                    toggle("Daily free scan", "alarm", $freeScanOn, .freeScan)
                    if freeScanOn {
                        DatePicker("Remind me at", selection: $freeScanTime,
                                   displayedComponents: .hourAndMinute)
                            .font(.snapBody)
                            .foregroundStyle(Color.snapEspresso)
                            .tint(Color.snapTerracotta)
                            .onChange(of: freeScanTime) { _, date in
                                let comps = Calendar.current.dateComponents([.hour, .minute], from: date)
                                NotificationManager.shared.freeScanReminderTime =
                                    (comps.hour ?? NotificationManager.defaultFreeScanHour, comps.minute ?? 0)
                                resyncFreeScan()
                            }
                    }
                } header: {
                    Text("Free scan")
                } footer: {
                    Text("Off by default. When on, one reminder at the time you pick — only on days you haven't scanned yet, and never once you're on Pro.")
                }
            }

            Section {
                toggle("Weekly portfolio", "bag", $portfolioOn, .portfolio)
                toggle("Monthly recap", "chart.bar.doc.horizontal", $recapOn, .recap)
                toggle("Ledger reminders", "tag", $ledgerOn, .ledger)
                toggle("Trial reminders", "clock", $trialOn, .trial)
            } header: {
                Text("Notifications")
            } footer: {
                Text("Occasional, functional reminders only — a monthly recap, a nudge to update your ledger, and a heads-up before a free trial ends. We never send promotional notifications.")
            }
        }
        .scrollContentBackground(.hidden)
        .background(Color.snapBackground)
        .navigationTitle("Notifications")
        .navigationBarTitleDisplayMode(.large)
        .task {
            systemDenied = await NotificationManager.shared.authorizationStatus() == .denied
        }
    }

    private func toggle(
        _ label: String,
        _ icon: String,
        _ binding: Binding<Bool>,
        _ category: NotificationManager.Category
    ) -> some View {
        Toggle(isOn: binding) {
            HStack(spacing: 14) {
                Image(systemName: icon)
                    .snapSymbol(16, weight: .medium)
                    .foregroundStyle(Color.snapTerracotta)
                    .frame(width: 24)
                Text(label)
                    .font(.snapBody)
                    .foregroundStyle(Color.snapEspresso)
            }
        }
        .tint(Color.snapTerracotta)
        .onChange(of: binding.wrappedValue) { _, isOn in
            NotificationManager.shared.setEnabled(category, isOn)
            if category == .freeScan, isOn { resyncFreeScan() }
        }
    }

    /// Turning the reminder on, or moving its time, schedules the next one
    /// right away rather than waiting for the next foreground.
    private func resyncFreeScan() {
        Task {
            await NotificationManager.shared.syncFreeScanReminder(
                isPro: isPro, scannedToday: ScanStreak.scannedToday(), streak: ScanStreak.current())
        }
    }
}
