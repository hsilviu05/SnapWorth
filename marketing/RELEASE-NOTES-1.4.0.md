# What's New — SnapWorth 1.4.0

## Scope

Widgets. One pull request, five phases: #149.

1.3.7 was bug fixes and shipped separately — the price-tag reader, condition
grading, and four surfaces disagreeing about an item's value. Nothing from that
release is repeated here.

Two widget surfaces became eight:

| Surface | Families | Who sees it |
|---|---|---|
| Haul *(existing)* | small, medium | everyone |
| Quick Scan *(existing)* | small | everyone |
| **Haul on the Lock Screen** | inline, circular, rectangular | everyone |
| **Recent finds** | medium, large | everyone |
| **Scans left** | small, circular | free shows a count, Pro shows the streak |
| **Profit this month** | small | Pro; reads "Pro" otherwise |
| **Thrift run** | Live Activity + Dynamic Island | everyone |
| **Scan** | Control Centre, Action Button | iOS 18+ |

---

## Primary — paste into App Store Connect

```
Widgets, and a lot of them.

Your haul total on the Lock Screen — as a circle, a panel, or a single line beside the clock.

Recent finds on the Home Screen: the last few things you scanned, and what each one is worth.

Free scans left today, at a glance.

Profit this month from the flips you've sold, for Pro subscribers.

And thrift runs. Start one when you walk into a shop and your running total for that trip stays on the Lock Screen and in the Dynamic Island while you scan — so you always know what the haul is worth before you decide on the next thing.

On iPhone 15 Pro and later you can put Scan on the Action Button, or add it to Control Centre, and go straight to the camera.
```

### Shorter alternative

```
Widgets: your haul total on the Lock Screen, recent finds and free scans left on the Home Screen, and monthly profit for Pro. Start a thrift run and the running total for that trip stays in the Dynamic Island while you scan. Scan can also go on the Action Button or in Control Centre.
```

---

## Testing on a device — required, and more than usual

**None of this can be verified by the test suite or the simulator.** The suite
covers the data layer — the decoder, the money abbreviation, the empty-haul
string — and nothing about whether a widget renders, a Live Activity appears,
or a Control Centre button lands in the right place.

### The one test that matters most

- [ ] **Install 1.3.7 first. Add two or three widgets. Then install 1.4.0 over
      it without opening the app.** Every widget must still show its data.

  This is the upgrade path every existing user takes, and the failure it guards
  is specific: Swift's synthesised `Codable` initialiser throws on a missing
  key rather than using a property's default, so the six new fields would have
  made every stored blob undecodable. The widget extension is replaced by the
  update while the app may not run for days — so a strict decoder would have
  blanked every installed widget until its owner's next scan. There is a
  hand-written decoder for exactly this; confirm it works on a real upgrade
  rather than a fresh install.

### Fresh install, empty library

- [ ] Add all six widgets with **no scans at all**. Each shows a sensible empty
      state — "No finds yet", "Nothing scanned yet", "—" — and none shows
      `$0 – $0`.

### Each surface

- [ ] **Lock Screen haul**, all three: inline (beside the clock), circular,
      rectangular. Check on a busy wallpaper and in a light one.
- [ ] **Recent finds**: medium shows 2 rows, large shows 4. A long item name
      truncates rather than pushing the price off.
- [ ] **Scans left** as a free user: the count, and the "Back tomorrow, or go
      Pro" state at zero.
- [ ] **Scans left** as Pro: shows the streak, not "unlimited".
- [ ] **Profit this month** as a free user: reads "Pro", not "$0".
- [ ] **Profit this month** as Pro with a flip sold this month: the figure
      matches what the Flips screen says. They are computed from the same
      array by the same rule, so a mismatch is a real bug.
- [ ] **Tap each widget** and confirm it opens the right screen — history,
      camera, and the profit ledger.

### Thrift run

- [ ] Start a run, scan two things, watch the total rise on the Lock Screen and
      in the Dynamic Island. **Only items scanned since you started should
      count.**
- [ ] End the run from the scan screen; the Activity disappears.
- [ ] **Force-quit the app mid-run, reopen it, and end the run.** The button
      must still work. The activity is read from `Activity.activities` rather
      than a stored reference precisely so this does not strand an Activity
      nothing can dismiss.
- [ ] Turn **Live Activities off** for SnapWorth in Settings. The control
      disappears entirely rather than becoming a button that does nothing.

### Control Centre / Action Button *(iPhone 15 Pro and later, iOS 18+)*

- [ ] Add **Scan** to Control Centre; press it with the app closed. The camera
      opens.
- [ ] Assign it to the **Action Button**; press with the phone locked.
- [ ] Press it while the app is **already open on another tab** — this path
      never fires `.task`, so it is handled on `scenePhase` instead and is the
      one most likely to be missed.
- [ ] Press once, then background and foreground the app twice. The camera must
      **not** reopen — the request clears as it is read.

---

## Pre-submit checklist

- [ ] `MARKETING_VERSION` 1.3.7 → **1.4.0**, `CURRENT_PROJECT_VERSION` 14 → 15,
      all four slots — done in this branch
- [ ] 1.3.7 **released** first, or this replaces it in review
- [ ] Device testing above
- [ ] What's New pasted from this file
- [ ] **Widget screenshots.** Not required by App Store Connect, but a release
      whose entire content is widgets and whose screenshots show none of them
      is selling something invisible.
- [ ] App description: the corrected copy from `marketing/app_store_listing.md`
      if it has not gone in yet, and the ⚠️ block deleted once it has

## Not in this release

Apple Watch. It has no camera, so it cannot do the app's primary action, and a
watch app would need its own screenshots before App Store Connect accepted a
submission. Lock Screen widgets carry the same glanceable numbers a
complication would, at a fraction of the cost — see the discussion on #149.
