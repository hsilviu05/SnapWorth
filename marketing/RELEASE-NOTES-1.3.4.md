# What's New — SnapWorth 1.3.4

**Version:** 1.3.4 (build 11) · **Previous release:** 1.3.3 (build 10) · **Status:** ready to archive

## Scope

Three changes touch `ios/` since 1.3.3. One is user-visible, two are quiet.

| Change | Visible? |
|---|---|
| Scan errors say what actually went wrong (#55) | **yes** |
| Device identity survives reinstall | no — but it fixes a real subscriber-facing bug class |
| Contact address is her.silviu.i@gmail.com everywhere | only if someone emails |

The backend halves are already deployed: the server has accepted `device_id`
on `/auth/entitlement` since the same PR, and older clients that omit it keep
working exactly as before.

---

## Primary — paste into App Store Connect

```
Clearer scan errors.

When a scan can't be priced, SnapWorth now tells you why — a photo the AI
couldn't read, an item it couldn't value, or a genuine hiccup on our side —
instead of always saying the service is unavailable. So you know whether to
try a different photo or just try again in a moment.

Also under the hood: Pro subscriptions now recognise your device across
reinstalls, so deleting and reinstalling the app never affects your plan.
```

### Shorter alternative

```
Scan errors now say what actually went wrong, so you know whether to retry
or try a different photo. Plus: reinstalling the app no longer affects your
Pro subscription.
```

---

## Copy constraints this text respects

**Nothing about valuation quality.** No pricing change in this build.

**The device-identity line is phrased as a benefit, not a fix.** The failure it
prevents — a subscriber reinstalling repeatedly and being read as free — was
fixed server-side in PR #66 for everyone already. This build is what makes it
structurally impossible rather than patched around: the server now counts
*phones*, not installs.

**No claim about a "test message" or alerts.** The Telegram notifier is an
operator tool and does not concern users.

---

## What changed, for the record

### Scan errors (#55)

`AppError.from` collapsed every 502 into "Our AI is temporarily unavailable."
The backend raises 502 for four different reasons with distinct, user-safe copy,
and for one of them — "The AI couldn't price this item." — nothing is down: the
user photographed something unpriceable. Telling them the service was down
invited them to retry the identical photo and fail identically. 502 now
surfaces the server's detail (a new `.aiFailed(String)` case); empty detail
still falls back to the fixed string; 503 still means outage.

### Device identity

`ScanAPIClient` and `ListingService` each minted a UUID into `UserDefaults`,
which is deleted with the app. `DeviceIdentity` (in `TokenStore.swift`) now
keeps it in the Keychain with `ThisDeviceOnly`, so it survives deletion but
never travels to another device via backup. An upgrading install adopts the id
it already had, so nothing resets. The id is sent with the signed transaction
on `/auth/entitlement`; the server binds the subscription to it instead of to
the per-install App Attest key.

Privacy copy updated in-app and at `api.snapworth.eu/privacy`: the identifier
is used "for rate limiting and to limit how many devices can use one
subscription." Still anonymous, still not linked to identity — the App Privacy
labels are unchanged (Device ID was already declared for app functionality).

### Contact address

PR #59 moved the website and backend to her.silviu.i@gmail.com but the app's
Settings → Feedback, the feedback sheet, and both legal pages still said
silh6767@gmail.com. Aligned, along with `marketing/app_store_listing.md`.

---

## Before submitting

- [ ] Xcode: confirm **1.3.4 (11)** in the target's General tab (the pbxproj is
      already bumped — this is a sanity check).
- [ ] Archive from `main` after the release PR merges — the backend that
      accepts `device_id` must be live first (it is, if `/health` reports a
      commit at or after the release PR's merge).
- [ ] Run the test target once (`⌘U`): `DeviceIdentityTests` and the #55
      acceptance tests in `ProductionHardeningTests` are new.
- [ ] Paste the What's New text above. The store **description** needs no
      change — nothing user-facing in the listing is affected.
- [ ] After approval: your own first cold launch of 1.3.4 re-syncs the
      subscription with a device id; nothing should appear in Telegram
      (same transaction, already announced), and the binding record for your
      subscription will shrink to one entry over the next 30 days as the old
      per-install subjects age out.

No new data collection. App Privacy labels unchanged.
