import XCTest
import UIKit
@testable import SnapWorth

// The app icon picker (1.7.0): every choice can actually be switched to, and
// the one-time announcement reaches only people who had the old icon.

final class AppIconChoiceTests: XCTestCase {

    /// iOS refuses `setAlternateIconName` for a name the build did not
    /// declare. Xcode declares exactly what
    /// `ASSETCATALOG_COMPILER_ALTERNATE_APPICON_NAMES` lists, under
    /// CFBundleIcons → CFBundleAlternateIcons, so a choice missing from that
    /// setting fails here instead of in a user's hand.
    func test_everyAlternateIsDeclaredInTheBuiltApp() throws {
        let icons = try XCTUnwrap(Bundle.main.infoDictionary?["CFBundleIcons"] as? [String: Any])
        let alternates = try XCTUnwrap(icons["CFBundleAlternateIcons"] as? [String: Any])
        for choice in AppIconChoice.allCases {
            guard let name = choice.iconName else { continue }
            XCTAssertNotNil(alternates[name], "\(name) is not an alternate icon in the build")
        }
    }

    func test_everyChoiceHasAPreview() {
        for choice in AppIconChoice.allCases {
            XCTAssertNotNil(UIImage(named: choice.previewImage), choice.previewImage)
        }
    }

    func test_exactlyOneChoiceIsThePrimaryIcon() {
        XCTAssertEqual(AppIconChoice.allCases.filter { $0.iconName == nil }, [.tag])
    }

    func test_theCurrentIconIsReadBackFromItsName() {
        for choice in AppIconChoice.allCases {
            XCTAssertEqual(AppIconChoice(iconName: choice.iconName), choice)
        }
        // A name a later version no longer ships reads as what iOS shows: the primary.
        XCTAssertEqual(AppIconChoice(iconName: "AppIcon-Retired"), .tag)
    }

    func test_theEventCarriesTheChoiceAndWhereItWasMade() {
        let event = AnalyticsEvent.appIconChanged(icon: AppIconChoice.night.rawValue, source: "prompt")
        XCTAssertEqual(event.name, "app_icon_changed")
        XCTAssertEqual(event.parameters, ["icon": "night", "source": "prompt"])
    }
}

final class AppIconAnnouncementTests: XCTestCase {
    private var defaults: UserDefaults!
    private let suite = "AppIconAnnouncementTests"

    override func setUp() {
        super.setUp()
        defaults = UserDefaults(suiteName: suite)
        defaults.removePersistentDomain(forName: suite)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suite)
        super.tearDown()
    }

    func test_someoneWithFindsIsAskedOnce() {
        XCTAssertTrue(AppIconAnnouncement.shouldShow(hasSavedFinds: true, defaults: defaults, supportsAlternateIcons: true))
        XCTAssertFalse(AppIconAnnouncement.shouldShow(hasSavedFinds: true, defaults: defaults, supportsAlternateIcons: true))
    }

    /// A new install never saw the old icon. Its first scan must not make it
    /// eligible on a later launch, so the decision is stored either way.
    func test_aNewInstallIsNeverAsked() {
        XCTAssertFalse(AppIconAnnouncement.shouldShow(hasSavedFinds: false, defaults: defaults, supportsAlternateIcons: true))
        XCTAssertFalse(AppIconAnnouncement.shouldShow(hasSavedFinds: true, defaults: defaults, supportsAlternateIcons: true))
    }

    func test_notAskedWhereIconsCannotChange() {
        XCTAssertFalse(AppIconAnnouncement.shouldShow(hasSavedFinds: true, defaults: defaults, supportsAlternateIcons: false))
    }
}
