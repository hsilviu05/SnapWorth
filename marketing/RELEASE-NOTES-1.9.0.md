# What's New — SnapWorth 1.9.0

## Scope

**Submit 1.9.0 (37).** Everything merged since 1.8.3 (36): #483, #484, #485.

`chore` caveat as always: the bump is a lower bound for 37, not the build.
Record the archive commit and Organizer's date below.

### Ships in the binary (users get it only with 37)

| PR | What | Visible? |
|----|------|----------|
| #484 | Camera: tap to focus with a focus ring, pinch to zoom up to 4x (Scan, Haul, tag camera, Legit Check; not Rack) | Yes |
| #484 | "Scan Failed" offers Try again for network, timeout and server outages; the photo is kept and only a successful scan is charged | Yes, on failure |
| #484 | Analyzing overlay stops on "Almost there…", and after 12 s says it is taking longer than usual | Yes, slow scans |
| #484 | Camera label scrim 0.5 → 0.65, so captions over a white shelf meet 4.5:1 | Yes |
| #484 | Thumbnails decoded at cell size and cached: My Finds, ledger and search scroll without re-decoding | Yes |
| #483 | Siri and Spotlight phrases in all eleven languages (were English only) | Yes, non-English |
| #483 | `snapworth://` routes `pro`, `haul`, `rack` for custom product pages and in-app events; `cpp_*`/`event_*` sources counted | No (store links) |
| #485 | Daily guess and Year in Finds count in Gregorian, so the Japanese and Buddhist calendars stop breaking them | Yes, those calendars |
| #485 | Local-currency line hidden when the ECB table is over six days old | Rarely |
| #485 | Share extension reads the image through Image I/O (less memory); quiet-delivery wording | Partly |
| #485 | eBay sold-prices search sends `+` as `%2B` (C++ no longer searches "C") | Yes |

Backend changes in #485 (Ask Tag's free question handed back when no answer
came, address cap stays a 429, limiter pruning) are already live for every
build.

### TestFlight — What to Test

```
Please test on this build:
• Camera: tap to focus (a ring appears) and pinch to zoom on Scan, Haul and Legit Check.
• Turn on Airplane Mode and scan: "Scan Failed" offers Try again; turn it off and retry.
• My Finds: scroll down and back up quickly; thumbnails don't flicker.
• Phone in another language: ask Siri the SnapWorth phrase in that language.
• Scans, purchases, restore and Redeem a code work as before.
```

### Device checks (once, before submitting)

- [ ] Focus ring draws where you tap, and zoom resets after leaving the camera.
- [ ] Rack scan has no pinch zoom (it needs the whole rail).
- [ ] Try again after a dropped scan does not lower the free scans left.
- [ ] iOS Settings → General → Language & Region → Calendar → Japanese:
      Daily guess shows today's round, not day 1.
- [ ] Siri in Romanian: "Cât valorează asta în SnapWorth" opens the camera.

---

## What's New and listing copy

Every store language, with counts, is in the paste sheet
`launch/APP-STORE-PASTE-1.9.0.md` in the project files. English:

```
• Tap to focus and pinch to zoom on the camera, for sharper scans.
• Scan dropped by a bad connection? Tap Try again. Your photo is kept and no scan is used.
• My Finds scrolls faster, and camera labels are easier to read.
• Fixes and polish.
```

The other ten languages add a line for Siri and Spotlight in that language.

**Listing changes that ride this version (#486):** add the English (U.K.)
localization from `app_store_listing.en-GB.md`, and paste the zh-Hans keyword
line from `app_store_listing.zh-Hans.md`. Promotional text is unchanged.

---

## Release checklist (owner)

### 1. Before archiving
- [ ] `main` includes the bump:
      `grep -m1 "CURRENT_PROJECT_VERSION = 37" ios/SnapWorth.xcodeproj/project.pbxproj`.
- [ ] CI green on that commit.

### 2. Archive
- [ ] Xcode 27 (`.xcode-version`), scheme **SnapWorth**, Release. Organizer
      shows **1.9.0 (37)**.
- [ ] Upload once. A re-archive needs 38.

### 3. TestFlight
- [ ] 37 has no *Missing Compliance* badge.
- [ ] Paste *What to Test*, then run the device checks above.

### 4. Version page
- [ ] Create version `1.9.0`, select build **37**.
- [ ] What's New in every store language; English (U.K.) added; zh-Hans keywords.
- [ ] App Privacy: no change.

### 5. Release
- [ ] Phased Release for Automatic Updates: on.
- [ ] Submit for review (owner only).

### Build 37 — the archive

- **Archive commit:** `53618d4` on `claude/project-thread-pmcqza` (#487): `main` at `19fa9af` plus the bump and a test-only fix, so the binary is `main`'s code.
- **Organizer creation date:** 2026-11-26 14:29:26 EET (`SnapWorth-1.9.0-37.xcarchive`), Xcode 27. The app and its Share, Stickers and Widgets extensions all read 1.9.0 (37).
- **Upload:** _to fill in_
