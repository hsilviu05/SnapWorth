# Tag, the SnapWorth mascot

`mascots.py` draws Tag (and the two unused concepts) as SVG, from the same
geometry as the app icon's tag. `export_app_assets.py` renders the app's
imagesets into `ios/SnapWorth/Assets.xcassets/Mascot/`:

- `TagHappy`, `TagJoy`, `TagWow`, `TagBlink`, each 137.5 × 160 pt at @2x and @3x
- Any appearance: espresso outline. Dark appearance: the same drawing with a
  cream edge, because the outline disappears on the dark ground.

To change the artwork, edit `mascots.py`, run `python3 export_app_assets.py`
(needs Playwright's Chromium and Pillow), and copy `app_assets/Mascot/` over the
catalog folder. `TagMascot` in `DesignSystem.swift` is the view that shows it.
