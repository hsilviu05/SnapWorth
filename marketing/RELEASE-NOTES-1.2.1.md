# What's New — SnapWorth 1.2.1

**Version:** 1.2.1 (build 6) · **Previous release:** 1.2.0 · **Status:** unreleased

Scope verified against `git log` for `ios/` since 1.2.0. This release contains
**exactly one** change set — `bfa42be`, the observability hotfix — and it touches
no view, no copy and no permission prompt. There is nothing a user can see.

That is worth stating plainly rather than dressing up: 1.2.1 exists so that the
next time a release misbehaves we find out from telemetry instead of from a
one-star review.

---

## Primary — paste into App Store Connect

Apple requires *something* in "What's New". Honest and short beats invented
features, and a release with no user-facing change should not claim one.

```
Reliability and diagnostics.

This update adds crash and performance reporting so we can spot problems
across the app faster, and fixes an internal metric that was counting
failed listing generations as successful ones.

No changes to how the app looks or works. If something has felt off since
the last update, this is the release that helps us find it.
```

**Character count:** well within the 4,000 limit.

### Alternative, if you prefer the shortest possible

```
Adds crash and performance diagnostics so we can find and fix problems
faster. No changes to how the app looks or works.
```

---

## What actually changed

| Change | User-visible? | Why it exists |
|---|---|---|
| MetricKit crash, hang and launch-time reporting | No | The app had **no crash reporting of any kind**. A crashing release was invisible until App Store Connect counts arrived days later, and those never say *why*. |
| Persistent-store fallback detection | No | If SwiftData cannot open the on-disk store the app falls back to an in-memory one. The user sees their entire history vanish; previously this was logged nowhere and was indistinguishable from a normal launch. |
| `listingGenerated` metric fired on success, not on attempt | No | It previously fired *before* the network call, so every timeout counted as a generated listing — inflating the headline adoption metric for a brand-new feature with exactly the cases where it did not work. |

Deliberately **not** forwarded off-device: call stacks (they belong in Xcode
Organizer, which already receives them), and raw termination reasons (free-form
OS text that can embed process names and paths). Both are bucketed into a small,
low-cardinality vocabulary instead.

No new `Info.plist` purpose string. No new permission prompt. No third-party SDK.

---

## ⚠️ Required before submitting — App Privacy labels

**This release will be rejected if this step is skipped.** `PrivacyInfo.xcprivacy`
now declares three data types that 1.2.0 did not, and the App Store Connect
nutrition labels must match the manifest.

In **App Store Connect → App Privacy**, add under **Diagnostics**:

| Data type | Linked to user | Used for tracking | Purpose |
|---|---|---|---|
| Crash Data | **No** | **No** | App Functionality |
| Performance Data | **No** | **No** | App Functionality |
| Other Diagnostic Data | **No** | **No** | App Functionality |

All three are unlinked and non-tracking — the manifest declares them that way,
and the events carry no identifier that could tie a crash to a person.

---

## Release checklist

- [x] Version bumped to 1.2.1, build 6 — verified in the built product, app and
      widget extension both report `1.2.1 / 6`
- [x] 143 iOS tests passing
- [ ] **App Privacy labels updated** (see above) — blocks submission
- [ ] Backend healthy — `api.snapworth.eu` was returning 502 on every route at
      the time of writing. Do not ship a client against a dead API.
- [ ] Backend security fixes deployed (dependency upgrades + `/metrics` guard)
- [ ] `METRICS_TOKEN` set in the Railway environment — the `/metrics` guard fails
      closed and returns 404 without it
- [ ] App Store screenshots — 3 of 8 built; frames 2, 3, 4, 7 and 8 outstanding.
      Not a hard blocker for a hotfix that reuses the 1.2.0 listing, but frame 2
      (Thrift Flip) remains the highest-value missing asset.
