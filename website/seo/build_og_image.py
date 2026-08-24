#!/usr/bin/env python3
"""Generate the social preview image (og:image) for snapworth.eu.

Why this exists
---------------
No page on the site had an og:image, so every link shared to iMessage,
WhatsApp, Slack, Facebook or X rendered as a bare URL with no preview card.
For a consumer app whose growth depends on people sending links to each other,
that is a silent conversion loss on every share.

Run:  python3 website/seo/build_og_image.py

1200x630 is the size Facebook, X, LinkedIn and iMessage all crop from cleanly.
Colours and typefaces are taken from the app's design system so a shared link
looks like the product, not like a generic link.
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent.parent
FONTS = ROOT / "ios/SnapWorth/Fonts"
OUT = ROOT / "website/og-image.png"

W, H = 1200, 630

# DesignSystem palette.
CREAM = (250, 247, 244)
ESPRESSO = (28, 20, 16)
TERRACOTTA = (217, 108, 71)
MUTED = (122, 113, 104)
SAGE = (122, 158, 126)


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def main() -> None:
    img = Image.new("RGB", (W, H), CREAM)
    d = ImageDraw.Draw(img)

    # Deliberately flat. An earlier version drew a concentric-ellipse wash in
    # the lower right; at this size it read as an accident rather than a
    # design choice, and it crowded the App Store chip. The card carries the
    # brand through type and the accent rule instead.

    # A terracotta rule, the same accent the site uses under its headings.
    d.rounded_rectangle([80, 96, 152, 108], radius=6, fill=TERRACOTTA)

    headline = font("Fraunces-Variable.ttf", 92)
    sub = font("DMSans-Variable.ttf", 38)
    small = font("DMSans-Variable.ttf", 30)

    d.text((80, 150), "Know what your", font=headline, fill=ESPRESSO)
    d.text((80, 252), "thrift finds are worth", font=headline, fill=ESPRESSO)

    d.text((80, 386),
           "Snap a photo. Get an AI resale estimate in seconds —",
           font=sub, fill=MUTED)
    d.text((80, 434), "a price range, a confidence score, a listing draft.",
           font=sub, fill=MUTED)

    # Wordmark, bottom left.
    d.text((80, 524), "SnapWorth", font=font("Fraunces-Variable.ttf", 40),
           fill=ESPRESSO)

    # Free-to-try chip, bottom right — the only "offer" on the card.
    chip = "Free on the App Store"
    box = d.textbbox((0, 0), chip, font=small)
    cw, ch = box[2] - box[0], box[3] - box[1]
    x0, y0 = W - 80 - cw - 44, 520
    d.rounded_rectangle([x0, y0, x0 + cw + 44, y0 + ch + 28], radius=999,
                        fill=(240, 234, 228))
    d.text((x0 + 22, y0 + 12), chip, font=small, fill=SAGE)

    img.save(OUT, "PNG", optimize=True)
    kb = OUT.stat().st_size / 1024
    print(f"wrote {OUT.relative_to(ROOT)}  {W}x{H}  {kb:.0f} KB")


if __name__ == "__main__":
    main()
