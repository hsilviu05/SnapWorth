# Simulator walkthroughs, 2026-10-07/08

iPhone 17 Simulator, newest runtime (CLAUDE.md's UDID recipe). Debug builds
launched with `-mock-scans`, so scans return canned results with no server
or App Attest. Driven with AXe (`brew install cameroncooke/axe/axe`): taps
by accessibility label, with `xcrun simctl io … screenshot`. Share cards were
saved to the Simulator's Photos library and copied out of
`~/Library/Developer/CoreSimulator/Devices/<udid>/data/Media/DCIM`.

## #229: ResultView before and after the split, free path

Each pair puts the build just before #229 (`7bdabd4`, `ResultView.swift` at
2,177 lines) on the **left** and `main` at `c10561f` (667 lines) on the
**right**. Both ran the same scripted steps in `en_US`: a fresh scan from
the photo library, guess and reveal, the sheet scrolled in fixed steps, paid
20 and sold 60 on the reopened find, and the result share card.

- `229-before-after-1.jpg`: the guess cover ("$ ? ? ?"), the reveal and
  verdict, then Why this price (locked), Sharpen this estimate, Condition,
  What did you pay? and Flip status.
- `229-before-after-2.jpg`: Flip status, Condition, Listing Draft, Snap →
  Sell (locked), and the mid-sheet cards.
- `229-before-after-3.jpg`: a reopened find (no cover, as intended), Sold
  with "Profit +$40", and the result share card.

**No layout differences.** The differences that do show are not the split:
- **Item and confidence:** the canned result rotates. The older build drew
  Nike Air Max 90 at $55–$110 (Medium); `main` drew Levi's 501 at $28–$55
  (High).
- **The "2x find" badge:** it follows from that ($55 ÷ $20 earns a badge,
  $28 ÷ $20 does not).
- **The "Currency" row and the sale-sharing card under Profit:** these
  arrived with #224, after the split.

**Not covered:** the Pro states (Why this price unlocked, Add the tag through
camera and library, Snap → Sell generate/copy/share, listing photo cleanup).
Purchases in the Simulator need the scheme's StoreKit configuration, which
only Xcode's Run applies. Run the `SnapWorth (Mock scans)` scheme from Xcode,
buy Pro with the test configuration, and walk those cards.

## #320: a phone set to Romania

`320-currency-romania.jpg`, from `main` at `c10561f` launched with
`-AppleLanguages (ro) -AppleLocale ro_RO`, after marking a find sold for 100
having paid 20:
- **Result sheet:** paid "RON 20", currency RON, sold "RON 100", profit
  "+80 RON". VoiceOver reads "Profit de +80 RON".
- **My Flips:** "Profitul lunii +80 RON", "Investit 20 RON", "Cel mai bun flip
  +80 RON", and the bars in RON.
- **The month share card:** "+80 RON", best flip "+80 RON".
- **The Profit widget on the home screen:** "RON 80 · from 1 flip". The widget
  process follows the Simulator's own `en_US` settings, so it orders the
  currency the English way; on a phone set to Romania both use `ro_RO`.

Before #320 each of these printed dollars ("+$80", "$20").
