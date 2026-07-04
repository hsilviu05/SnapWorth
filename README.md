# SnapWorth

> Photograph secondhand items — get AI-powered identification and resale value estimates.

```
SnapWorth/
├── ios/                          ← Swift / SwiftUI iOS app
│   ├── SnapWorth.xcodeproj/
│   └── SnapWorth/
│       ├── SnapWorthApp.swift    ← App entry point + SwiftData container
│       ├── Config.swift          ← API URL, mock flag, product IDs
│       ├── Info.plist            ← Permissions, UIAppFonts, orientation
│       ├── Assets.xcassets/
│       ├── DesignSystem/         ← Colors, fonts, shared components
│       ├── Models/               ← ScanResult (SwiftData @Model)
│       ├── Services/             ← PurchaseService protocol + implementations
│       ├── Camera/               ← AVFoundation camera manager
│       ├── ViewModels/           ← @Observable view models (iOS 17 Observation)
│       └── Views/                ← SwiftUI screens
└── backend/                      ← Python FastAPI + Anthropic
    ├── main.py
    ├── requirements.txt
    ├── Dockerfile
    ├── .env.example
    └── README.md
```

---

## Manual steps checklist

### 1 — Apple Developer account

- [ ] Enroll at [developer.apple.com](https://developer.apple.com) ($99/year)
- [ ] In Xcode → Signing & Capabilities, set your **Team** and confirm the bundle ID `com.snapworth.app` is unique (or change it to something you own)

### 2 — Add font files

Download from Google Fonts (free):
- **Fraunces**: fonts.google.com/specimen/Fraunces — download the variable font, export the static weights Regular, SemiBold, Bold as `.ttf`
- **DM Sans**: fonts.google.com/specimen/DM+Sans — export Regular, Medium, SemiBold, Bold

Steps:
1. Create `ios/SnapWorth/Fonts/` directory
2. Copy the 7 `.ttf` files listed in `Info.plist → UIAppFonts` into that folder
3. In Xcode, drag the `Fonts/` folder into the project navigator under the `SnapWorth` group — make sure **"Copy items if needed"** and **"Add to target: SnapWorth"** are both checked
4. In the project's Resources build phase, confirm all 7 font files appear

### 3 — App icon

Design a 1024×1024px PNG: terracotta rounded square (`#D96C47`) with a minimal cream camera-viewfinder mark and small price-tag shape.

In Xcode: drag the PNG into `Assets.xcassets → AppIcon`. Xcode 15+ generates all required sizes from a single 1024px image with a Universal slot.

### 4 — RevenueCat dashboard

1. Create a free account at [app.revenuecat.com](https://app.revenuecat.com)
2. Create a new project → add your iOS app → paste your **bundle ID**
3. Copy the **iOS Public SDK key** → paste into `Config.revenueCatAPIKey` in `Config.swift`
4. In App Store Connect (step 5), create the two in-app purchase products. Come back and add them as **entitlements** in RevenueCat under the `premium` entitlement

### 5 — App Store Connect — in-app purchases

1. Go to [appstoreconnect.apple.com](https://appstoreconnect.apple.com) → your app → In-App Purchases
2. Create **Auto-Renewable Subscription** group `"SnapWorth Premium"`
3. Add two products:
   - Product ID: `com.snapworth.weekly` — $4.99/week, 3-day free trial
   - Product ID: `com.snapworth.yearly` — $39.99/year, 3-day free trial
4. Fill in display names and descriptions, submit for review (can happen alongside app review)

### 6 — Deploy the backend

See `backend/README.md` for full Railway and Fly.io instructions.

Quick path (Railway):
```bash
# From repo root
railway login
railway init          # name: snapworth-backend
railway up --root backend
railway variables set ANTHROPIC_API_KEY=sk-ant-...
```

Copy the generated `*.up.railway.app` URL.

### 7 — Wire up the backend URL

In `ios/SnapWorth/Config.swift`:
```swift
static let baseURL = URL(string: "https://YOUR-APP.up.railway.app")!
static let mockMode = false   // ← flip this once backend is deployed
```

### 8 — Enable RevenueCat SDK

1. In Xcode → File → Add Package Dependencies → paste `https://github.com/RevenueCat/purchases-ios`
2. In `SnapWorthApp.swift`, swap `MockPurchaseService` for `RevenueCatPurchaseService`
3. Delete `MockPurchaseService.swift` or keep it for testing with a build flag

### 9 — Camera entitlement (if archiving fails)

If you get a provisioning error about camera: Xcode → Target → Signing & Capabilities → **+** → add **Background Modes** only if needed. Camera usage permission is declared in Info.plist and does not require an explicit capability toggle.

### 10 — TestFlight & submission

1. Archive the app (Product → Archive)
2. Upload via Xcode Organizer
3. In App Store Connect, create a new version, attach the build, fill metadata, submit

---

## Local development

```bash
# Backend
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add your ANTHROPIC_API_KEY
uvicorn main:app --reload

# iOS — open in Xcode
open ios/SnapWorth.xcodeproj
# Build & run on simulator or device (⌘R)
# Config.mockMode = true means no backend needed to test UI
```
