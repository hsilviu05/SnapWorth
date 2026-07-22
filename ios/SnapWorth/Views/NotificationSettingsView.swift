import SwiftUI
import UIKit

/// Per-category opt-outs for local notifications. All ON by default; each can be
/// disabled independently and is respected at schedule time by NotificationManager.
struct NotificationSettingsView: View {
    @State private var recapOn  = NotificationManager.shared.isEnabled(.recap)
    @State private var ledgerOn = NotificationManager.shared.isEnabled(.ledger)
    @State private var trialOn  = NotificationManager.shared.isEnabled(.trial)
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
                                .font(.system(size: 16, weight: .medium))
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

            Section {
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
                    .font(.system(size: 16, weight: .medium))
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
        }
    }
}
