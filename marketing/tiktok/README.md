# TikTok slideshows

Photo-mode carousels for `tiktok.com/@snapworthapp`: 1080×1920 PNGs plus a
caption, rendered from a spec. A spec has the structure and copy and lives in
git. The photos and numbers come from a real find and stay out of git.

```sh
python3 marketing/tiktok/build_slideshows.py --sheet            # all specs
python3 marketing/tiktok/build_slideshows.py thrift-haul        # one spec
```

Needs Playwright's Chromium, as `marketing/mascot/` does. Pass
`--chromium /path/to/chrome` if Playwright's own browser isn't installed.
`--sheet` also writes `sheet.jpg` with every slide side by side for a quick
check, and needs Pillow.

## Filling one in

1. Do the find and the scan for real. Write down what you paid, the range and
   the confidence the app showed.
2. Put the photos in `fill/<spec>/`, each named after its slot
   (`item1.jpg`, `result.png`, …). JPEG, PNG or WebP. Chromium can't read HEIC,
   so export iPhone photos as JPEG. A screenshot slot wants a real SnapWorth
   screenshot of that scan.
3. Put the numbers in `fill/<spec>/values.json`, using the keys from the spec,
   e.g. `{"ITEM_1": "Barbour wax jacket", "PAID_1": "£12", "LOW_1": "$80", ...}`.
   The app's estimates are in US dollars. What you paid is in the currency you
   paid in. Don't convert either one.
4. Run the build. Anything still missing renders to `out/<spec>/DRAFT/` with a
   red banner naming it. A complete one goes to `out/<spec>/` with
   `caption.txt`.

The rules in `../shorts-scripts.md` apply here too: use real finds and real
numbers, call it an *AI estimate* (never "sold listings" or "accurate"), say
it's your app, and label Pro features.

## The three specs

| Spec | Format | Needs |
|---|---|---|
| `thrift-haul` | Paid X for the lot → each find with its AI estimate → total → app | A real haul, 3 scans |
| `before-you-buy` | 5-tip checklist, with the app as tip 5 | 4 photos, no numbers |
| `halloween-clearout` | Keep / sell / donate the Halloween box. Post Oct 31 to early Nov | 3 scans |
| `outfit-check` | What's my outfit worth: shoes, jacket, total, app | 2 scans (first filled from real ones, 2026-10-28) |

`before-you-buy` is the quickest one to post because it needs no scans.
`thrift-haul` is the one most likely to travel, because the hook is a number.

A `hook` photo fills the whole frame. Add `"inline": true` to put it in a card instead, for a photo too small or too wide to fill 9:16.

To add a format, copy a spec and rearrange its slides. The slide types are
`hook`, `item`, `tip`, `compare` and `app`, and the three specs between them
use every field. Write `*words*` to get the brand's terracotta italic, and
`{KEY}` for a value from `values.json`.

## Before writing a new spec: find a format that already works

Search TikTok for *thrift haul worth*, *garage sale finds*, *what's it worth*
and *resell flip*. Save slideshows whose views are many times the account's
follower count. Only trust a structure once you've seen it work on four or five
accounts. Copy the slide order (hook, middle, where the product shows up, the
ask), never the content.

## Posting

- Post in **photo mode**, with a trending sound at low volume.
- Copy is kept clear of TikTok's tabs (top 190 px), the button rail on the
  right (150 px) and the caption (below 1540 px). These are measured off
  current feeds, not taken from any TikTok spec, so check one slide in the app
  before a batch.
- Put an **App Store Connect campaign link** in the bio (App Analytics →
  Acquisition → Campaigns → generate a link with campaign `tiktok`). Without
  it you can't tell TikTok installs apart from everything else.
- Post one a day for about three weeks before judging the format. Make more of
  whatever beats the account's average.
