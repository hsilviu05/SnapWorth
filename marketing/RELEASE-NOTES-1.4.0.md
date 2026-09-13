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

## Audit fixes that landed on this branch — worth a device pass

Everything below is covered by the test suite where a test could reach it. These
are the parts a test could not reach, in rough order of how badly a regression
would show.

### Notifications

- [ ] **Tap a notification with the app not running.** It must open the screen
      it names — the recap goes to My Flips, the trial warning to Settings, the
      free-scan reminder straight to the camera. The delegate used to be
      installed from a SwiftUI `.task`, which runs *after* launch finishes, so
      every deep link was dead on a cold start and worked perfectly from the
      background. Only a real cold launch tests this.
- [ ] Same tap with the app **in the background**, to confirm nothing regressed
      on the path that already worked.

### Purchases

- [ ] **Buy Pro on the Scan tab, then switch to Settings.** The card must read
      SnapWorth Pro, not "Free Plan · Upgrade". Settings had no dependency on
      the entitlement and only re-rendered when its own state changed.
- [ ] **Tap Restore purchases in Settings and dismiss Apple's sign-in sheet.**
      No alert, no red text — declining is not a failure. Then tap it again on
      a slow connection: the row shows a spinner and cannot be tapped twice.
- [ ] **Open the paywall on a cold launch on poor signal.** Either prices or the
      retry — never both, and never "Couldn't load every plan" above prices
      that loaded.

### Widgets

- [ ] **Profit this month, as Pro, in a month where something sold but no paid
      price was entered.** It must read "N sold · add what you paid", not "No
      flips sold yet this month" — which is what it said while My Flips showed
      the sale. *This changes what the widget prints; check it before the
      launch photographs.*
- [ ] **Press the Control Centre / Action Button Scan control during
      onboarding**, finish onboarding, and confirm the camera opens. The press
      used to be consumed and posted to nobody.
- [ ] Press it, then leave the phone for **more than five minutes** before
      opening the app. The camera must **not** open: a tap is an instruction
      about now, and one that old is dropped.

### Thrift run

- [ ] Start a run and leave the app for a while, then foreground it. Nothing
      visible should change — but an Activity older than eight hours is now
      dismissed on foreground rather than lingering on the Lock Screen.
- [ ] With VoiceOver on, focus the Live Activity. It should say when the run
      **started**; the elapsed timer is a system-drawn count that cannot be
      spoken accurately from a frozen string.

### My Flips

- [ ] **Export CSV and open it in Numbers or Excel.** Profit must carry two
      decimals like the other money columns, ROI must read like `42.55%`, and
      an item named `=1+1` must appear as text rather than evaluating.
- [ ] With VoiceOver on, focus the **⋯ menu**: it must announce "Flip options"
      and its current sort, not "ellipsis circle".

### My Finds

- [ ] Search for something, **delete every find**, then scan one. The new find
      must appear — it used to be filtered against the query that survived the
      wipe, under a banner saying one item was scanned.

### Things that changed how something looks

Each of these is its own commit, so any one can be reverted on its own if it
photographs badly. Four of them are provably identical at the default text
size; the two marked **moves pixels** are not.

- [ ] **Profit this month widget, signed out of Pro.** It must show the real
      figure, not "Pro" / "Track profit with Pro". The app has always given
      free users that number on the Flips tab — the widget was upselling a
      feature they already had, with a deep link to the screen showing it.
      *Moves pixels, and it is a widget: check before the launch photographs.*
- [ ] **My Flips summary card.** The first stat now reads "Invested
      (all-time)" rather than "Invested", because it always was — beside a
      header saying "Profit this month". No number changed; check the longer
      label still sits on one line on the narrowest device you have.
- [ ] **Recent finds widget.** The figure in the header now reads
      "Haul $3,480 – $6,200" instead of the bare range. It is the whole
      library, and it sat unlabelled directly above two rows that visibly do
      not sum to it. *Moves pixels — check before the launch photographs.*
- [ ] **Circular Lock Screen complication.** It now shows the *middle* of the
      haul range, not its top, so it matches the figure under "Your finds are
      worth" in the app. A library of three $100-$200 items read $600 there
      and $450 in the app.
- [ ] **Thrift Flip: "Scan tag" and "Choose from library".** Both were ~17pt
      tap targets against Apple's 44pt minimum and now carry the standard hit
      target, which makes their rows ~27pt taller. *Moves pixels.*
- [ ] **The paywall's unselected plan card.** Its radio ring and outline were
      1.2:1 against the card — no visible boundary at all, and Increase
      Contrast did not help. Both are now readable in either theme. Check the
      Monthly card looks like a choice and not like a mistake.
- [ ] **Dark mode, any card.** Cards had no boundary: the shadow was a *warm
      brown* lighter than the dark ground, so it lightened rather than
      darkened. There is now a hairline edge in dark mode. Light mode is
      untouched — worth confirming with a side-by-side.
- [ ] **Every widget at an accessibility text size.** Nothing in the extension
      scaled before; all 51 sizes now do. At the *default* size every widget
      should be unchanged — that is the thing to verify first. Then set Larger
      Text to AX3 and check nothing clips on the small and accessory families.
- [ ] **My Finds at an accessibility text size.** The grid drops to one
      full-width column at AX1 and above; below that it is unchanged.

### Thrift Flip

- [ ] **Scan an item and then just leave** — no ledger save, any verdict.
      Open My Finds: the item must be there. Scans were charged against the
      daily allowance and then discarded unless the flip was Pro, profitable
      and saved, so a free user could spend their whole allowance here and
      find nothing.
- [ ] Then scan another, fill both prices and **Save to My Flips**. My Finds
      must still show **one** row for it, promoted to Owned — not two.

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
