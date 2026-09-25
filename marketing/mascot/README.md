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

## Stickers

`export_stickers.py` draws Tag's iMessage sticker pack from the same helpers:
16 stills and 4 APNG loops, each 618 × 618 px (206 pt @3x, the largest Messages
accepts) and under Apple's 500 KB limit, plus the `iMessage App Icon` set. It
writes `stickers_out/Stickers.xcassets`; copy that over
`ios/SnapWorthStickers/Stickers.xcassets`. The script owns every file in the
catalog, so change the script rather than the PNGs or their `Contents.json`.
Needs Playwright's Chromium, Pillow and numpy.

The words on seven stickers, and every VoiceOver label, are English in all five
languages: a sticker pack has no code, so it can't pick artwork by language.
