import SwiftUI
import SwiftData
import UIKit

/// The icons a user can pick in Settings → App icon.
///
/// The artwork is drawn by `marketing/mascot/export_app_icons.py`. Each
/// alternate's `iconName` must also be listed in the app target's
/// `ASSETCATALOG_COMPILER_ALTERNATE_APPICON_NAMES`, or iOS refuses the switch
/// with an error and the picker shows its failure alert.
enum AppIconChoice: String, CaseIterable, Identifiable {
    case tag, night, wow, classic

    var id: String { rawValue }

    /// The name handed to `setAlternateIconName`; `nil` is the primary icon.
    var iconName: String? {
        switch self {
        case .tag:     return nil
        case .night:   return "AppIcon-TagNight"
        case .wow:     return "AppIcon-TagWow"
        case .classic: return "AppIcon-Classic"
        }
    }

    /// The choice iOS is showing. An unknown name (an icon a later version
    /// removed) reads as the primary, which is what iOS falls back to.
    init(iconName: String?) {
        self = Self.allCases.first { $0.iconName == iconName } ?? .tag
    }

    var title: String {
        switch self {
        case .tag:     return String(localized: "Tag")
        case .night:   return String(localized: "Night")
        case .wow:     return String(localized: "Wow")
        case .classic: return String(localized: "Classic")
        }
    }

    /// An .appiconset cannot be loaded as an image, so the picker has copies.
    var previewImage: String {
        switch self {
        case .tag:     return "AppIconPreview/Tag"
        case .night:   return "AppIconPreview/TagNight"
        case .wow:     return "AppIconPreview/TagWow"
        case .classic: return "AppIconPreview/Classic"
        }
    }
}

/// The one-time "choose your app icon" alert for people who used SnapWorth
/// before 1.6.3 changed its icon.
///
/// Decided once, on the first launch that runs this code: someone with saved
/// finds by then had the old icon, and is asked; a new install has none, and
/// never is — it has only ever seen the new icon, so "SnapWorth has a new
/// icon" would be untrue. The decision is stored either way, so a new user's
/// first scan does not make them eligible later.
enum AppIconAnnouncement {
    static let decidedKey = "appIconAnnouncementDecided"

    static func shouldShow(hasSavedFinds: Bool, defaults: UserDefaults = .standard,
                           supportsAlternateIcons: Bool) -> Bool {
        guard !defaults.bool(forKey: decidedKey) else { return false }
        defaults.set(true, forKey: decidedKey)
        return hasSavedFinds && supportsAlternateIcons
    }
}

struct AppIconPickerView: View {
    /// Analytics only: `settings` or `prompt`.
    let source: String
    @State private var current = AppIconChoice(iconName: UIApplication.shared.alternateIconName)
    @State private var isChanging = false
    @State private var failed = false

    var body: some View {
        List {
            Section {
                ForEach(AppIconChoice.allCases) { choice in
                    Button {
                        select(choice)
                    } label: {
                        HStack(spacing: 16) {
                            Image(choice.previewImage)
                                .resizable()
                                .frame(width: 60, height: 60)
                                // The Home Screen's corner shape at 60 pt.
                                .clipShape(RoundedRectangle(cornerRadius: 13.5, style: .continuous))
                                .accessibilityHidden(true)
                            Text(choice.title)
                                .font(.snapBody)
                                .foregroundStyle(Color.snapEspresso)
                            Spacer()
                            if choice == current {
                                Image(systemName: "checkmark")
                                    .snapSymbol(16, weight: .semibold)
                                    .foregroundStyle(Color.snapTerracottaText)
                                    .accessibilityHidden(true)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                    .disabled(isChanging)
                    .accessibilityLabel(choice.title)
                    .accessibilityValue(choice == current ? String(localized: "Selected") : "")
                    .accessibilityAddTraits(choice == current ? [.isButton, .isSelected] : .isButton)
                }
            } footer: {
                Text("iOS shows a short message when the icon changes.")
            }
        }
        .scrollContentBackground(.hidden)
        .background(Color.snapBackground)
        .navigationTitle("App icon")
        .navigationBarTitleDisplayMode(.inline)
        .alert("Couldn't change the icon", isPresented: $failed) {
            Button("OK", role: .cancel) {}
        } message: {
            Text("Try again in a moment.")
        }
    }

    private func select(_ choice: AppIconChoice) {
        guard choice != current, !isChanging else { return }
        isChanging = true
        Task { @MainActor in
            defer { isChanging = false }
            do {
                try await UIApplication.shared.setAlternateIconName(choice.iconName)
                current = choice
                Haptics.selection()
                Analytics.shared.track(.appIconChanged(icon: choice.rawValue, source: source))
            } catch {
                // Re-read rather than assume: a failed switch leaves whatever
                // iOS was already showing.
                current = AppIconChoice(iconName: UIApplication.shared.alternateIconName)
                failed = true
            }
        }
    }
}

/// The picker presented from the one-time announcement, with its own Done.
struct AppIconPickerSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            AppIconPickerView(source: "prompt")
                .toolbar {
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Done") { dismiss() }
                    }
                }
        }
        .presentationDetents([.medium, .large])
    }
}
