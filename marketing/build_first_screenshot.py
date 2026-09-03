#!/usr/bin/env python3
"""The first App Store screenshot: the payoff, not the process.

Search results show the first screenshot inline, and six weeks of data say the
card is where SnapWorth loses people (2.1% tap-through against 18.6% page
conversion). The old first panel explained how scanning works; this one says
what you get, in the words a thrifter thinks in.

It is built from the shipped `store_2_know-before-you-buy.jpg` — the artwork
with the real result screen for the Off-White sneakers the app priced at
$220–$420 — by replacing only the headline and subhead above the devices. The
device screens are untouched: nothing on them is generated, so nothing can be
invented. Type follows SCREENSHOT-HANDOFF.md (Fraunces Bold headline, one
terracotta accent, DM Sans subhead).

    python marketing/build_first_screenshot.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "ios/SnapWorth/Fonts"
SRC = ROOT / "marketing/screenshots/store_2_know-before-you-buy.jpg"
OUT = ROOT / "marketing/screenshots/store_0_that-pair.jpg"

W, H = 1320, 2868
ESPRESSO = (43, 33, 28)
TERRACOTTA = (217, 108, 71)
WARM_GREY = (110, 96, 85)
WHITE = (255, 255, 255)

# The original text block sits alone on white above the devices; the
# terracotta corner triangle occupies the top-right ~120px only.
TEXT_TOP, TEXT_BOTTOM = 56, 352
TRIANGLE_W, TRIANGLE_H = 1180, 135

HEADLINE = [("That $40 pair", None), ("might be ", "$420.")]   # (plain, accent)
SUBHEAD = "Know before you buy. One photo, four seconds."


def font(family: str, style: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONTS / ("Fraunces-Variable.ttf" if family == "fraunces" else "DMSans-Variable.ttf")
    f = ImageFont.truetype(str(path), size)
    f.set_variation_by_name(style)
    return f


def text_width(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont) -> int:
    left, _, right, _ = draw.textbbox((0, 0), text, font=f)
    return right - left


def main() -> None:
    canvas = Image.open(SRC).convert("RGB")
    draw = ImageDraw.Draw(canvas)

    # Clear the old headline and subhead, leaving the corner triangle alone.
    draw.rectangle([0, TEXT_TOP, TRIANGLE_W, TEXT_BOTTOM], fill=WHITE)
    draw.rectangle([0, TRIANGLE_H, W, TEXT_BOTTOM], fill=WHITE)

    head = font("fraunces", "Bold", 100)
    y = TEXT_TOP + 4
    for plain, accent in HEADLINE:
        total = text_width(draw, plain + (accent or ""), head)
        x = (W - total) // 2
        draw.text((x, y), plain, font=head, fill=ESPRESSO)
        if accent:
            draw.text((x + text_width(draw, plain, head), y), accent, font=head, fill=TERRACOTTA)
        y += 108

    sub = font("dmsans", "Regular", 42)
    y += 14
    draw.text(((W - text_width(draw, SUBHEAD, sub)) // 2, y), SUBHEAD, font=sub, fill=WARM_GREY)

    # sRGB JPEG, no alpha — what App Store Connect accepts, matching the set.
    canvas.save(OUT, "JPEG", quality=92, optimize=True, subsampling=0)
    print(f"✓ {OUT.relative_to(ROOT)} ({canvas.width}x{canvas.height})")


if __name__ == "__main__":
    main()
