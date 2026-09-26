import SwiftUI
import UIKit
import UserNotifications

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
    @State private var banner: PermissionBanner = .none

    var body: some View {
        List {
            switch banner {
            case .none:
                EmptyView()
            case .ask:
                Section {
                    Button {
                        Task {
                            let allowed = await NotificationManager.shared.requestAuthorizationIfNeeded()
                            await refreshSystemState()
                            if allowed, freeScanOn { resyncFreeScan() }
                        }
                    } label: {
                        bannerLabel(icon: "bell.badge",
                                    title: "Allow notifications",
                                    detail: "None of these reminders can arrive until you do.")
                    }
                }
            case .openSettings:
                Section {
                    Button {
                        if let url = URL(string: UIApplication.openSettingsURLString) {
                            UIApplication.shared.open(url)
                        }
                    } label: {
                        bannerLabel(icon: "bell.slash",
                                    title: "Notifications are off",
                                    detail: "Turn them on in iOS Settings to get these reminders.")
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
                    // Not "on days you haven't scanned": the allowance comes
                    // back at UTC midnight, so east of UTC it can be back the
                    // same evening as a morning scan, and the reminder says so.
                    Text("Off by default. When on, one reminder at the time you pick — only once your free scan is back, and never once you're on Pro.")
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
        .task { await refreshSystemState() }
    }

    private func bannerLabel(icon: String, title: LocalizedStringKey,
                             detail: LocalizedStringKey) -> some View {
        HStack(spacing: 14) {
            Image(systemName: icon)
                .snapSymbol(16, weight: .medium)
                .foregroundStyle(Color.snapTerracottaText)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.snapBody)
                    .foregroundStyle(Color.snapEspresso)
                Text(detail)
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)
            }
        }
    }

    private func toggle(
        _ label: LocalizedStringKey,
        _ icon: String,
        _ binding: Binding<Bool>,
        _ category: NotificationManager.Category
    ) -> some View {
        Toggle(isOn: binding) {
            HStack(spacing: 14) {
                Image(systemName: icon)
                    .snapSymbol(16, weight: .medium)
                    .foregroundStyle(Color.snapTerracottaText)
                    .frame(width: 24)
                Text(label)
                    .font(.snapBody)
                    .foregroundStyle(Color.snapEspresso)
            }
        }
        .tint(Color.snapTerracotta)
        .onChange(of: binding.wrappedValue) { _, isOn in
            NotificationManager.shared.setEnabled(category, isOn)
            guard isOn else { return }
            // Switching a reminder on is a request for notifications, so it is
            // the right moment to ask iOS if we never have. Without this, a
            // user who tapped "Not now" on the priming alert could never be
            // asked again by anything in the app: every toggle here wrote a
            // preference that `add()` then ignored, silently, forever.
            Task {
                let allowed = await NotificationManager.shared.requestAuthorizationIfNeeded()
                await refreshSystemState()
                if allowed, category == .freeScan { resyncFreeScan() }
            }
        }
    }

    @MainActor
    private func refreshSystemState() async {
        banner = PermissionBanner(status: await NotificationManager.shared.authorizationStatus())
    }

    /// Turning the reminder on, or moving its time, schedules the next one
    /// right away rather than waiting for the next foreground.
    private func resyncFreeScan() {
        Task {
            await NotificationManager.shared.syncFreeScanReminder(
                isPro: isPro, lastScan: ScanStreak.lastScan, streak: ScanStreak.current())
        }
    }
}

extension NotificationSettingsView {
    /// What the top of the screen offers when notifications cannot arrive.
    ///
    /// Two different fixes for two different statuses. A user who tapped "Not
    /// now" on the in-app priming alert was never shown the system one, so
    /// the status stays `.notDetermined` for good — and iOS lists no
    /// Notifications switch at all for an app that has never asked. The
    /// banner sent exactly that user to Settings, to a page with nothing to
    /// turn on, while every toggle here already read ON and so never fired the
    /// request either. Only a declined *system* alert gives `.denied`, and
    /// only then is there a switch in Settings to send anyone to.
    enum PermissionBanner: Equatable {
        case none
        /// iOS has never been asked: ask it, here.
        case ask
        /// iOS was asked and said no: only Settings can change that.
        case openSettings

        init(status: UNAuthorizationStatus) {
            switch status {
            case .notDetermined: self = .ask
            case .denied:        self = .openSettings
            case .authorized, .provisional, .ephemeral: self = .none
            @unknown default:    self = .none
            }
        }
    }
}
