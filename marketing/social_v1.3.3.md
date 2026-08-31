# Social — SnapWorth 1.3.3

One Instagram post plus a TikTok script. Graphic: `marketing/ig/out/post1_133_freescan.png`
(1080×1350). Regenerate: `python3 marketing/ig/build_1_3_3.py`

Source of truth for the artwork is `marketing/ig/dc-1.3.3/FreeScan.dc.html`, same
pipeline as 1.3 — the build strips the Design Component wrapper and swaps the
Google Fonts link for embedded woff2, so canvas and PNG cannot drift.

Brand: Fraunces + DM Sans · terracotta `#D96C47` · sage `#7A9E7E` · cream `#FAF7F4` · dark `#1C1410`.

> **Hold until 1.3.3 is approved.** These name a version that is not live yet.
>
> **The free tier is one scan a day.** Every asset here says so. Do not post
> anything that still promises three — the App Store description and the website
> both need correcting before this goes out, or the ads contradict the listing.

---

## Instagram — "One free scan, every day"

**Image:** `out/post1_133_freescan.png`

```
One free scan a day. Every day. 📸

No account. No card. Point your camera at anything in the bins and get
a resale estimate back in seconds.

And the counter now tells you the truth — your scans left comes straight
from SnapWorth, so what you see is what you can actually do next.

Want more than one? Pro is unlimited, and adds the Thrift Flip profit
calculator and marketplace-ready listings.

Free on the App Store 👉 link in bio

#reselling #thriftflip #resellercommunity #sidehustle #depop #vinted
#ebayreseller #thrifting #thriftstorefinds #thrifthaul #appupdate
```

**Why it's worded this way:** it leads with the limit rather than burying it.
People find out either way the first time they scan twice; saying it first reads
as confidence, saying it late reads as a catch.

---

## TikTok — 22s script

Vertical 1080×1920. One voice, fast cuts. The hook has to land in the first
1.5 seconds or the rest does not matter.

| # | Time | On screen | Voiceover / caption |
|---|---|---|---|
| 1 | 0.0–2.0s | Rail of jackets in a thrift store, hand pulling one out | **"You have no idea what this is worth."** |
| 2 | 2.0–5.0s | Phone raised, camera framing the jacket on the rail | "So stop guessing." |
| 3 | 5.0–8.5s | App screen recording: shutter tap → "Analyzing the item…" | "One photo. About four seconds." |
| 4 | 8.5–13.0s | Screen recording: result card, value range and confidence | "That's a resale estimate — a range, and how sure it is." |
| 5 | 13.0–16.5s | Screen recording: scrolling to the written listing draft | "It writes the listing for you too." |
| 6 | 16.5–19.5s | Back to the aisle, item going into the basket | "Now you know before you buy it." |
| 7 | 19.5–22.0s | End card: logo, "One free scan a day", App Store badge | **"One free scan a day. Free. Link in bio."** |

**On-screen text** (burn in, don't rely on TikTok captions):
`0:00 "you have no idea what this is worth"` → `0:05 one photo` →
`0:08 4 seconds` → `0:13 it writes the listing` → `0:19 1 free scan / day`

**Audio:** trending sound at 20–30% under a clean voiceover. Do not let the
music carry it alone — shots 3–5 are screen recordings and need narration to
read.

### Shots 3, 4 and 5 must be real screen recordings

Do not generate the app UI. Record it. Those three shots are the entire proof
of the video, and a rendered mock-up of a screen that doesn't match the real app
is both obvious and an App Store metadata problem. Everything else — the store,
the rail, the hands, the end card — can be generated.

---

## Nano Banana prompts

For the b-roll shots only (1, 2, 6, 7). Generate 9:16, then cut in the real
screen recordings for 3–5.

Keep the palette consistent across all four or the cuts will feel like four
different videos: warm neutrals, terracotta accents, soft daylight.

**Shot 1 — the hook**

```
Vertical 9:16 photograph. A dense rail of second-hand clothing in a thrift
store, packed hangers receding out of focus. A hand pulls one beige jacket
forward into the light. Soft overcast daylight through a shop window, warm
neutral palette — cream, oatmeal, faded terracotta. Shallow depth of field,
35mm, natural grain. No text, no logos, no readable brand labels.
```

**Shot 2 — raising the phone**

```
Vertical 9:16 photograph, over-the-shoulder. A person holds up a smartphone
to photograph a jacket hanging on a thrift store rail. The phone screen is
blank and dark — no interface. Focus on the hands and the garment; the aisle
falls away behind. Warm daylight, cream and terracotta tones, shallow depth
of field, 35mm, natural grain. No text, no logos, no readable brand labels.
```

**Shot 6 — the decision**

```
Vertical 9:16 photograph. A person's hands lower a folded beige jacket into
a shopping basket in a thrift store aisle. Slight motion in the hands, calm
and unhurried. Warm overcast daylight, cream and faded terracotta palette,
shallow depth of field, 35mm, natural grain. No text, no logos, no faces.
```

**Shot 7 — end card background**

```
Vertical 9:16. A clean, empty warm-cream surface with soft directional
daylight from the upper right and a gentle shadow falling across the lower
third. Subtle paper texture. Nothing else in frame — this is a background
plate for text. Palette: cream #FAF7F4 warming to soft terracotta in the
shadow. No objects, no text, no logos.
```

**Composite the end card yourself** — set "One free scan a day" in Fraunces
over shot 7, with the App Store badge. Don't ask the model for the wordmark or
the badge; it will approximate both and get them wrong.

### Prompt rules that matter here

- **"No text"** on every prompt. Generated lettering is the fastest way to look
  fake, and any invented SnapWorth wordmark is worse than none.
- **No faces**, or you inherit a likeness you don't have rights to.
- **No readable brand labels** on the garments — a hallucinated Nike swoosh on a
  jacket in an ad is a trademark problem.
- Keep "warm overcast daylight" and the cream/terracotta palette in all four, so
  the b-roll and the app's own UI feel like one piece.

---

## Claims check

Everything above is limited to what the app actually does:

- ✅ One free scan a day — matches `FREE_SCANS_PER_DAY=1`
- ✅ Resale estimate as a range with a confidence band — that is the result screen
- ✅ Writes a listing draft — free tier; **Snap → Sell** marketplace listings are Pro
- ✅ Roughly four seconds — measured scan latency
- ❌ No sold listings, comps, or market data. SnapWorth has no such source;
  `backend/comps/` is built but disabled. Do not add it to the caption.
- ❌ No accuracy percentage. There is no measured figure to quote.
