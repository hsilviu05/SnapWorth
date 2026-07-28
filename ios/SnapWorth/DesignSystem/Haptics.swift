import UIKit

/// Haptic feedback, named by *meaning* rather than by intensity.
///
/// Two problems this fixes, both of which are felt rather than seen.
///
/// **The Taptic Engine needs warming.** Every call site previously constructed a
/// generator and fired it in the same statement:
///
///     UIImpactFeedbackGenerator(style: .medium).impactOccurred()
///
/// Apple's guidance is to `prepare()` ahead of the interaction. Without it the
/// engine may still be idle when the event arrives, so the tap lands late — or,
/// on a busy frame, not at all. The shutter button is the worst case: it is the
/// one haptic in the app a user would notice missing, and it fires at exactly
/// the moment the main thread is busiest.
///
/// **Naming by intensity leaks implementation into call sites.** `.medium` says
/// nothing about why. Naming by intent (`.capture`, `.selection`, `.success`)
/// means the *feel* of the app can be tuned in one place, which is what keeps a
/// haptic language consistent as screens are added.
///
/// Deliberately a plain enum with static methods rather than an injected
/// service: there is nothing here worth mocking, and a protocol would be
/// abstraction for its own sake.
enum Haptics {

    /// Warm the engine ahead of an imminent interaction.
    ///
    /// Call on `.onAppear` of a screen whose primary action is haptic, not
    /// before every event — `prepare()` keeps the engine powered for a few
    /// seconds, and calling it continuously wastes energy for no benefit.
    static func prepare() {
        guard isEnabled else { return }
        _impact.prepare()
        _selection.prepare()
    }

    /// Shutter press. The signature interaction of the app.
    static func capture() {
        guard isEnabled else { return }
        _impact.impactOccurred()
        // Re-prime immediately: a user who takes one photo very often takes
        // another, and the second tap should feel identical to the first.
        _impact.prepare()
    }

    /// A discrete choice changed — segmented control, plan card, picker.
    static func selection() {
        guard isEnabled else { return }
        _selection.selectionChanged()
        _selection.prepare()
    }

    /// A meaningful task completed: scan returned, purchase confirmed.
    static func success() {
        guard isEnabled else { return }
        _notification.notificationOccurred(.success)
    }

    /// A task failed in a way the user must notice.
    static func failure() {
        guard isEnabled else { return }
        _notification.notificationOccurred(.error)
    }

    /// A soft confirmation for a secondary action — copy, toggle, dismiss.
    static func light() {
        guard isEnabled else { return }
        _light.impactOccurred()
        _light.prepare()
    }

    // MARK: - Preference

    static let preferenceKey = "snapworth_haptics_enabled"

    /// Honours the user's setting, defaulting to on.
    ///
    /// Some people find haptics distracting, and a premium app lets them turn
    /// them off rather than treating the preference as unimaginable. Read on
    /// each call: `UserDefaults` reads are cheap next to firing the engine, and
    /// caching would mean the toggle needed an app restart to take effect.
    static var isEnabled: Bool {
        UserDefaults.standard.object(forKey: preferenceKey) as? Bool ?? true
    }

    static func setEnabled(_ enabled: Bool) {
        UserDefaults.standard.set(enabled, forKey: preferenceKey)
    }

    // MARK: - Generators

    // Retained rather than constructed per call. Building a generator is cheap
    // but `prepare()` on a fresh instance is wasted — the warm-up belongs to the
    // instance, so a new one each time is always cold.
    private static let _impact = UIImpactFeedbackGenerator(style: .medium)
    private static let _light = UIImpactFeedbackGenerator(style: .light)
    private static let _selection = UISelectionFeedbackGenerator()
    private static let _notification = UINotificationFeedbackGenerator()
}
