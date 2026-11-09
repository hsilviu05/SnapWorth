import Foundation
import os.log
import RevenueCat
import StoreKit

/// RevenueCat as an observer of purchases, for subscription analytics only.
/// This is the **only** file that imports the RevenueCat SDK.
///
/// `StoreKitPurchaseService` still makes, verifies and finishes every
/// transaction, and Pro is still decided by StoreKit and our own server.
/// Nothing here is read back: no `CustomerInfo`, no entitlements, no offerings.
/// `purchasesAreCompletedBy: .myApp` is what keeps the SDK from finishing
/// transactions itself.
///
/// What the SDK sends, at the pinned 5.102.3 (`Package.resolved`):
/// * every request carries the raw `identifierForVendor`
///   (`HTTPClient.swift`, `X-Apple-Device-Identifier`), plus device model,
///   OS and app version, bundle ID, preferred locales and the App Store
///   country. There is no flag for the IDFV header.
/// * a random anonymous app user ID the SDK creates and stores in
///   `UserDefaults`.
/// * for a purchase, Apple's signed transaction.
///
/// `automaticDeviceIdentifierCollectionEnabled: false` stops the *other*
/// collection path (IDFA/IDFV/IP as subscriber attributes, which only runs
/// when an attribution network ID is set — we set none). The privacy policy,
/// `PrivacyInfo.xcprivacy` and the App Store label describe this list; check
/// it when the pin moves.
///
/// Gated on the same analytics opt-out as TelemetryDeck. The SDK cannot be
/// switched off once configured, so turning analytics off stops it from the
/// next launch; until then `record` and `sync` below already do nothing.
enum RevenueCatObserver {
    private static let log = Logger(subsystem: "eu.snapworth.app", category: "revenuecat")

    /// Configures the SDK once at launch. A no-op while `Config.revenueCatAPIKey`
    /// is empty or analytics is off, so debug builds and forks send nothing.
    @MainActor
    static func start() {
        let apiKey = Config.revenueCatAPIKey.trimmingCharacters(in: .whitespaces)
        guard !apiKey.isEmpty, Analytics.shared.isEnabled, !Purchases.isConfigured else { return }
        #if DEBUG
        Purchases.logLevel = .warn
        #else
        Purchases.logLevel = .error
        #endif
        Purchases.configure(
            with: Configuration.Builder(withAPIKey: apiKey)
                .with(purchasesAreCompletedBy: .myApp, storeKitVersion: .storeKit2)
                .with(automaticDeviceIdentifierCollectionEnabled: false)
                .build())
    }

    /// Hands RevenueCat the result of a purchase the app made itself.
    ///
    /// Best-effort and never awaited by the purchase flow: a failure costs a
    /// chart entry, not a sale, and the SDK also picks up unrecorded StoreKit 2
    /// transactions by itself whenever the app becomes active.
    static func record(_ result: Product.PurchaseResult) async {
        guard Purchases.isConfigured, Analytics.shared.isEnabled else { return }
        do {
            _ = try await Purchases.shared.recordPurchase(result)
        } catch {
            log.error("recordPurchase failed: \(error.localizedDescription, privacy: .public)")
        }
    }

    /// After a restore: sends what is on the device without a sign-in prompt
    /// (`restorePurchases()` can show one; `syncPurchases()` cannot).
    static func sync() async {
        guard Purchases.isConfigured, Analytics.shared.isEnabled else { return }
        do {
            _ = try await Purchases.shared.syncPurchases()
        } catch {
            log.error("syncPurchases failed: \(error.localizedDescription, privacy: .public)")
        }
    }
}
