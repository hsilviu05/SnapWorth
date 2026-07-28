#!/usr/bin/env python3
"""Composite App Store marketing screenshots from raw simulator captures.

Implements the layout in SCREENSHOT-HANDOFF.md exactly: 1320x2868 canvas,
device at 940px wide with its baseline at y=2660, Fraunces Bold headline at
96-112pt, DM Sans subhead at 40-44pt, one terracotta accent word.

Run from the repository root:

    python marketing/build_screenshots.py

Inputs are raw 1320x2868 captures in /tmp/snapshots. Outputs land in
marketing/screenshots/v2/.

The device screen is always a real capture composited in — the frame is drawn,
the UI never is. Generating UI would risk inventing controls, which is the
failure this whole screenshot effort exists to prevent.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "ios/SnapWorth/Fonts"
RAW = Path("/tmp/snapshots")
OUT = ROOT / "marketing/screenshots/v2"

# ── Canvas geometry (SCREENSHOT-HANDOFF.md §2) ───────────────────────────────
W, H = 1320, 2868
HEADLINE_TOP = 200
SUBHEAD_TOP = 500
DEVICE_W = 940
DEVICE_BASELINE = 2660
CORNER_RADIUS = 78          # proportional to a 6.9" device at this width
BEZEL = 12

# ── Palette (DesignSystem.swift) ─────────────────────────────────────────────
CREAM = (251, 247, 242)
WHITE = (255, 255, 255)
DEEP_ESPRESSO = (23, 18, 15)
ESPRESSO = (43, 33, 28)
CREAM_TEXT = (240, 233, 226)
WARM_GREY = (110, 96, 85)
WARM_GREY_DARK = (176, 162, 151)
TERRACOTTA = (217, 108, 71)
TERRACOTTA_DARK = (232, 132, 95)
SAGE = (111, 143, 107)


def font(family: str, style: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONTS / ("Fraunces-Variable.ttf" if family == "fraunces" else "DMSans-Variable.ttf")
    f = ImageFont.truetype(str(path), size)
    f.set_variation_by_name(style)
    return f


@dataclass
class Shot:
    slug: str
    raw: str
    headline: list[str]          # one entry per line
    accent: str                  # the single word rendered in terracotta
    subhead: str
    dark: bool = False
    tint: tuple | None = None    # (rgb, alpha, "lower"|"base") wash
    headline_size: int = 104
    notes: str = ""


SHOTS: list[Shot] = [
    Shot(
        slug="01_know-before-you-buy",
        raw="raw_01_result.png",
        headline=["Know before", "you buy."],
        accent="buy.",
        subhead="An AI resale estimate from one photo.",
    ),
    Shot(
        slug="05_your-listing-already-written",
        raw="raw_05_listing.png",
        headline=["Your listing,", "already written."],
        accent="already written.",
        subhead="A title and description tailored to where you sell.",
        headline_size=96,
    ),
    Shot(
        slug="06_every-find-tracked",
        raw="raw_06_finds.png",
        headline=["Every find,", "tracked."],
        accent="tracked.",
        subhead="What you scanned, what it's worth, all in one place.",
        tint=(SAGE, 0.06, "lower"),
    ),
]


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1],
                                           radius=radius, fill=255)
    return mask


def background(shot: Shot) -> Image.Image:
    """Flat ground plus a single soft radial lift behind the device centre."""
    base = DEEP_ESPRESSO if shot.dark else CREAM
    canvas = Image.new("RGB", (W, H), base)

    if not shot.dark:
        # Radial lift toward white. Built at 1/8 scale and upsampled — a
        # per-pixel gradient at full size is slow and produces identical output
        # once blurred.
        small = Image.new("L", (W // 8, H // 8), 0)
        d = ImageDraw.Draw(small)
        cx, cy = (W // 8) // 2, int((H // 8) * 0.62)
        for i in range(28, 0, -1):
            r = i * 11
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=int(255 * (1 - i / 28) * 0.85))
        small = small.filter(ImageFilter.GaussianBlur(14))
        lift = small.resize((W, H), Image.BICUBIC)
        canvas = Image.composite(Image.new("RGB", (W, H), WHITE), canvas, lift)

    if shot.tint:
        rgb, alpha, where = shot.tint
        wash = Image.new("RGB", (W, H), rgb)
        mask = Image.new("L", (W, H), 0)
        d = ImageDraw.Draw(mask)
        top = int(H * (0.55 if where == "lower" else 0.78))
        for y in range(top, H):
            progress = (y - top) / max(1, H - top)
            d.line([(0, y), (W, y)], fill=int(255 * alpha * progress))
        canvas = Image.composite(wash, canvas, mask)

    return canvas


def draw_headline(canvas: Image.Image, shot: Shot) -> int:
    """Render the headline, colouring only the accent word. Returns bottom y."""
    d = ImageDraw.Draw(canvas)
    f = font("fraunces", "Bold", shot.headline_size)
    ink = CREAM_TEXT if shot.dark else ESPRESSO
    accent_ink = TERRACOTTA_DARK if shot.dark else TERRACOTTA
    line_height = int(shot.headline_size * 1.05)

    y = HEADLINE_TOP
    for line in shot.headline:
        # Split so the accent phrase can take a different colour mid-line.
        if shot.accent and shot.accent in line:
            head, _, tail = line.partition(shot.accent)
            segments = [(head, ink), (shot.accent, accent_ink), (tail, ink)]
        else:
            segments = [(line, ink)]

        total = sum(d.textlength(text, font=f) for text, _ in segments)
        x = (W - total) / 2
        for text, colour in segments:
            if not text:
                continue
            d.text((x, y), text, font=f, fill=colour)
            x += d.textlength(text, font=f)
        y += line_height
    return y


def draw_subhead(canvas: Image.Image, shot: Shot, y: int) -> None:
    d = ImageDraw.Draw(canvas)
    f = font("dmsans", "Regular", 42)
    ink = WARM_GREY_DARK if shot.dark else WARM_GREY

    # Wrap to a comfortable measure rather than the full canvas width.
    words, lines, current = shot.subhead.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if d.textlength(trial, font=f) > W - 320 and current:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)

    y = max(y + 40, SUBHEAD_TOP)
    for line in lines[:2]:
        d.text(((W - d.textlength(line, font=f)) / 2, y), line, font=f, fill=ink)
        y += int(42 * 1.35)


def place_device(canvas: Image.Image, raw_path: Path) -> None:
    """Composite the real capture into a drawn bezel."""
    screen = Image.open(raw_path).convert("RGB")
    screen_w = DEVICE_W - BEZEL * 2
    screen_h = int(screen.height * (screen_w / screen.width))
    screen = screen.resize((screen_w, screen_h), Image.LANCZOS)
    screen.putalpha(rounded_mask(screen.size, CORNER_RADIUS - BEZEL))

    device_h = screen_h + BEZEL * 2
    x = (W - DEVICE_W) // 2
    y = DEVICE_BASELINE - device_h

    # Bezel: near-black with a hairline highlight, standing in for a titanium
    # rail. Deliberately understated — a fake chrome gradient reads worse than
    # a clean flat frame.
    bezel = Image.new("RGBA", (DEVICE_W, device_h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bezel)
    bd.rounded_rectangle([0, 0, DEVICE_W - 1, device_h - 1],
                         radius=CORNER_RADIUS, fill=(26, 26, 28, 255))
    bd.rounded_rectangle([0, 0, DEVICE_W - 1, device_h - 1],
                         radius=CORNER_RADIUS, outline=(72, 72, 76, 255), width=2)

    # Soft contact shadow so the device sits on the ground rather than floating.
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [x + 20, y + 34, x + DEVICE_W - 20, y + device_h + 10],
        radius=CORNER_RADIUS, fill=(120, 80, 50, 46))
    shadow = shadow.filter(ImageFilter.GaussianBlur(30))
    canvas.paste(Image.alpha_composite(canvas.convert("RGBA"), shadow).convert("RGB"), (0, 0))

    bezel.paste(screen, (BEZEL, BEZEL), screen)
    canvas.paste(bezel, (x, y), bezel)


def build(shot: Shot) -> Path | None:
    raw_path = RAW / shot.raw
    if not raw_path.exists():
        print(f"  ⏭  {shot.slug}: missing {raw_path.name}")
        return None

    canvas = background(shot)
    bottom = draw_headline(canvas, shot)
    draw_subhead(canvas, shot, bottom)
    place_device(canvas, raw_path)

    OUT.mkdir(parents=True, exist_ok=True)
    out_path = OUT / f"en_69_{shot.slug}.png"
    # sRGB, 8-bit, no alpha — App Store Connect rejects Display P3 and alpha.
    canvas.convert("RGB").save(out_path, "PNG", optimize=True)
    print(f"  ✓  {out_path.name}  ({canvas.width}x{canvas.height})")
    return out_path


def main() -> int:
    print(f"Building App Store screenshots → {OUT.relative_to(ROOT)}")
    built = [p for p in (build(s) for s in SHOTS) if p]
    print(f"\n{len(built)} of {len(SHOTS)} built.")
    return 0 if built else 1


if __name__ == "__main__":
    raise SystemExit(main())
