# SnapWorth — Launch Checklist

Everything in this file requires your Apple / RevenueCat / App Store Connect accounts.
All code is ready and waiting.

---

## Step 1 — Apple Developer Membership
*Blocked until enrollment status changes from Pending → Active*

1. Watch for an approval email to silh6767@gmail.com
2. Go to [developer.apple.com](https://developer.apple.com) → Account → Membership
3. Copy your **Team ID** (10-char string, e.g. `A1B2C3D4E5`)
4. Tell Claude: *"My Team ID is XXXXXXXXXX"* → one-line code fix, committed in seconds

---

## Step 2 — RevenueCat + In-App Purchases
*Can be done now, before membership is active*

### 2a. App Store Connect — create products first
1. [appstoreconnect.apple.com](https://appstoreconnect.apple.com) → Your App → Subscriptions
2. Create subscription group: **SnapWorth Pro**
3. Add two products (see `APP_STORE.md` for details):

| Product ID | Type | Price | Trial |
|---|---|---|---|
| `snapworth_weekly` | Auto-Renewable | $2.99/week | None |
| `snapworth_yearly` | Auto-Renewable | $39.99/year | 3 days |

4. Submit both for review (they review IAP separately from the app)

### 2b. RevenueCat dashboard
1. Create account at [app.revenuecat.com](https://app.revenuecat.com)
2. New project → Add iOS app → Bundle ID: `com.snapworth.app`
3. Products tab → add `snapworth_weekly` and `snapworth_yearly`
4. Entitlements tab → create entitlement key: `premium` → attach both products
5. API Keys tab → copy the **iOS SDK key** (starts with `appl_`)
6. Tell Claude: *"My RevenueCat key is appl_XXXX"* → two-line code fix + commit

---

## Step 3 — Xcode Signing (after Team ID)

Open `ios/SnapWorth.xcodeproj` in Xcode:

1. Select the **SnapWorth** target → Signing & Capabilities
2. Set **Team** to your account
3. Let Xcode create the provisioning profile automatically
4. Build to device or simulator to confirm it compiles clean

---

## Step 4 — App Store Connect Metadata
*See `APP_STORE.md` for all copy, ready to paste*

In App Store Connect → Your App → App Information + Prepare for Submission:

- [ ] App name: `SnapWorth`
- [ ] Subtitle: `Resale Value in Seconds`
- [ ] Promotional text (copy from `APP_STORE.md`)
- [ ] Description (copy from `APP_STORE.md`)
- [ ] Keywords (copy from `APP_STORE.md`)
- [ ] Category: Shopping / Utilities
- [ ] Age rating: 4+
- [ ] Support URL: `https://snapworth-backend-production.up.railway.app/privacy`
- [ ] Privacy Policy URL: `https://snapworth-backend-production.up.railway.app/privacy`
- [ ] Screenshots — required sizes:
  - iPhone 6.7" (16 Pro Max simulator) — 5 screens minimum
  - iPhone 6.5" (11 Pro Max simulator) — 5 screens minimum
  - Suggested screens: Onboarding, Scan view, Result sheet, History, Paywall

---

## Step 5 — Archive & Upload

Once Team ID is set and signing works:

```
Xcode → Product → Archive → Distribute App → App Store Connect → Upload
```

Then in App Store Connect → TestFlight:
- Internal testing first (your own device)
- External beta (optional, up to 10,000 testers)

---

## Step 6 — Submit for Review

In App Store Connect → Prepare for Submission:
- [ ] All metadata filled in
- [ ] Screenshots uploaded
- [ ] IAP products approved
- [ ] Build uploaded via Xcode
- [ ] Click **Submit for Review**

Apple review typically takes **24–48 hours** for a new app.

---

## Ongoing after launch

| Task | When |
|---|---|
| Add `RAILWAY_TOKEN` secret to GitHub repo settings | Before next deploy |
| Enable branch protection (see CI setup instructions) | Anytime |
| Monitor Railway logs for errors | Weekly |
| Respond to App Store review feedback | As needed |
| Update `MARKETING_VERSION` in Xcode for each release | Each update |
