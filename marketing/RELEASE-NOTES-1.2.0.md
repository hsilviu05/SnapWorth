# What's New — SnapWorth 1.2.0

**Version:** 1.2.0 (build 5) · **Previous release:** 1.1.2 · **Status:** unreleased

Scope verified against `git log 1.1.2..HEAD`. Only user-visible changes are
listed — backend hardening, the metrics layer, the comps engine and the
evaluation platform ship in this build but change nothing a user can see, so
they do not belong in release notes.

---

## Primary — paste into App Store Connect

App Store Connect allows 4,000 characters. Roughly the **first three lines**
show before "more", so the two new features lead.

```
Thrift Flip — know your profit before you buy

Add the shop price and SnapWorth shows what you'd clear after marketplace
fees, with a straight Worth flipping or Skip it. Fee rates for eBay, Vinted,
Facebook Marketplace and OLX are built in.

Snap → Sell — your listing, already written

Get a title and description tailored to where you're selling. Copy it, paste
it, post it. SnapWorth never posts for you.

My Flips — track what you actually made

Mark items paid, listed and sold. See real profit and return, plus a monthly
recap of how your flips performed.

Also in this update

• Scans are noticeably faster — photos are now optimised before upload
• Subscription prices show correctly in your local currency
• Clearer messages when a scan can't be completed
• Deleting a find now asks first
• Full VoiceOver, Dynamic Type and Reduce Motion support
• Dark and tinted app icons for iOS 18 and later
• Haptics can be turned off in Settings

Thanks for using SnapWorth. Every estimate is an AI estimate, and the app
tells you how confident it is — we'd rather be honest than impressive.
```

**Character count:** ~1,050 / 4,000.

---

## Short variant — for a phased or expedited release

```
Thrift Flip: add the shop price and see your profit after marketplace fees
before you buy. Snap → Sell writes your listing for eBay, Vinted, Facebook
Marketplace or OLX. My Flips tracks what you actually made.

Faster scans, correct local pricing, clearer errors, and full VoiceOver and
Dynamic Type support.
```

**Character count:** ~330.

---

## Change inventory

Every line above traced to a commit. Anything not user-visible is excluded.

### New features

| Feature | Evidence |
|---|---|
| **Thrift Flip** — profit after marketplace fees, `Worth flipping` / `Skip it` verdict | `3b4d7c5`, `ThriftFlipView.swift`, `MarketplaceFees.swift` |
| **Snap → Sell** — marketplace-tailored listing drafts (Pro) | `ddab719`, `ListingService.swift`, backend `/listing` |
| **My Flips** — paid → listed → sold ledger, profit and ROI, monthly recap | `FlipsView.swift`, `FlipsViewModel.swift` |

### Improvements

| Change | Evidence | User-visible as |
|---|---|---|
| Photos downscaled to 1568px before upload | `01d94fe` | "Scans are noticeably faster" |
| Storage images downscaled to 1024px | `1dbf7f2` | Less storage used (not stated — too minor) |
| Paywall reads live StoreKit prices | `1f13424` | "Prices show correctly in your local currency" |
| Error copy rewritten; 402 routed to paywall | `01d94fe` | "Clearer messages when a scan can't be completed" |
| Delete confirmation on single finds | `1dbf7f2` | "Deleting a find now asks first" |
| Dynamic Type, dark mode, VoiceOver, Reduce Motion | `4823a66`, `0fdb203`, `1dbf7f2` | Accessibility line |
| Dark + tinted app icon variants | `e28c1f6` | Icon line |
| Haptics preference | `1dbf7f2` | Settings line |
| Haptics warmed via `prepare()` | `1dbf7f2` | Felt, not stated |

### Deliberately excluded

Not user-visible, so not in release notes:

- Backend security fixes (Sandbox StoreKit gate, entitlement device cap, Redis
  fail-closed, DeviceCheck reinstall defence)
- AI pipeline v2 (generation config, computed confidence, prompt v2)
- Evaluation platform, comps engine foundations
- Metrics, health probes, graceful shutdown, container hardening
- Certificate pinning (report-only — no behavioural change)

---

## Pre-submission checklist

- [ ] **Build number** — currently 5. If a 1.2.0 build was ever uploaded to App
      Store Connect, bump to 6; App Store Connect rejects a duplicate
      `(version, build)` pair
- [ ] **Screenshots replaced** — the current set is blocked, see
      `SCREENSHOT-COMPLIANCE.md`. This release cannot ship without new assets
- [ ] **iPad screenshots or drop iPad support** — `TARGETED_DEVICE_FAMILY = "1,2"`
      makes iPad screenshots mandatory. See `SCREENSHOT-HANDOFF.md` §2
- [ ] **Description updated** — Depop and Poshmark removed; Snap → Sell supports
      eBay, Vinted, Facebook Marketplace, OLX only
- [ ] **Keywords updated** — `poshmark`, `depop` removed
- [ ] Backend deployed with `ALLOWED_STOREKIT_ENVIRONMENTS=Production`,
      `REDIS_URL`, `TOKEN_KEYS`, `ENVIRONMENT=production`
- [ ] Railway health-check path set to `/health/ready`
- [ ] Verify a real purchase in TestFlight — note that TestFlight issues
      **Sandbox** transactions, which production now rejects by design. Test
      against a staging deployment with `ALLOWED_STOREKIT_ENVIRONMENTS=Sandbox`
- [ ] Privacy labels still accurate — no change this release

---

## Notes on tone

The closing line — *"Every estimate is an AI estimate, and the app tells you how
confident it is — we'd rather be honest than impressive"* — is deliberate.

Release notes are read almost exclusively by existing users, who are the people
most likely to have been burned by an estimate. Naming the limitation there
costs nothing with prospects (who never see it) and buys credibility with the
users who decide whether to keep paying. It also aligns the notes with the
in-app `AI estimate` label and the confidence badge, so the product says the
same thing in every place a user encounters it.

Avoided throughout: "revolutionary", "powered by AI", "smart", "seamless",
"game-changing". The audience is transactional — they want to know what changed
and whether it makes them money.
