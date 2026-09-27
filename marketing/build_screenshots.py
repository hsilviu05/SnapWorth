#!/usr/bin/env python3
"""Composite the 1.5.1 App Store screenshot set (v3) from device captures.

Implements the layout in SCREENSHOT-HANDOFF.md: 1320x2868 canvas, device at
940px wide with its baseline at y=2660, Fraunces Bold headline, DM Sans
subhead, one terracotta accent word, and a PRO tag over the caption of every
frame that shows a Pro feature (Guideline 2.3.2).

Run from the repository root:

    python3 marketing/build_screenshots.py --check    # validate only, write nothing
    python3 marketing/build_screenshots.py            # composite all eight
    python3 marketing/build_screenshots.py --skip 02  # the set without Haul (#203's fallback)

Inputs are the eight captures SCREENSHOT-HANDOFF.md §3 names, taken on a 6.9"
iPhone (1320x2868) from the submitted build, in marketing/screenshots/v3/captures/
(git-ignored) or the folder given with --captures. Outputs land in
marketing/screenshots/v3/.

Nothing is built unless every check passes: a missing, unreadable or wrongly
sized capture is named and the run fails, so a partial set cannot be mistaken
for the finished one. The device screen is always a real capture composited
in: the frame is drawn, the UI never is. Generating UI would risk inventing
controls, which is the failure this whole screenshot effort exists to prevent.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageCms, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "ios/SnapWorth/Fonts"
CAPTURES = ROOT / "marketing/screenshots/v3/captures"
OUT = ROOT / "marketing/screenshots/v3"

# ── Canvas geometry (SCREENSHOT-HANDOFF.md §2) ───────────────────────────────
W, H = 1320, 2868
CAPTURE_SIZE = (1320, 2868)  # a 6.9" iPhone's native screenshot
PRO_TAG_TOP = 96
HEADLINE_TOP = 200
SUBHEAD_TOP = 500
HEADLINE_MEASURE = W - 160   # 80px side margins
SUBHEAD_MEASURE = W - 320
SUBHEAD_SIZE = 42
DEVICE_W = 940
DEVICE_BASELINE = 2660
CORNER_RADIUS = 78          # proportional to a 6.9" device at this width
BEZEL = 12

# ── Caption limits (SCREENSHOT-HANDOFF.md §1) ────────────────────────────────
HEADLINE_MAX_WORDS = 5
SUBHEAD_MAX_WORDS = 15

# ── Palette (DesignSystem.swift, SnapDarkHex in WidgetDataStore.swift) ───────
CREAM = (251, 247, 242)
WHITE = (255, 255, 255)
DEEP_ESPRESSO = (23, 18, 15)
ESPRESSO = (43, 33, 28)
CREAM_TEXT = (240, 233, 226)
WARM_GREY = (110, 96, 85)
WARM_GREY_DARK = (176, 162, 151)
TERRACOTTA = (217, 108, 71)
TERRACOTTA_DARK = (232, 132, 95)
TERRACOTTA_FILL = (168, 72, 44)   # the in-app PRO badge's fill
SAGE = (111, 143, 107)

# Claims no caption may make. The reasons are app_store_listing.md's
# "Claims this listing does not make", which every frame follows too.
FORBIDDEN: list[tuple[str, str]] = [
    (r"\bsold listings?\b|\bcomps\b|\bmarket data\b|\brecent sales\b",
     "SnapWorth has no sold-listings, comps or market-data source"),
    (r"\baccurate\b|\bexact(ly)?\b|\bprecise(ly)?\b",
     "no accuracy figure has ever been measured"),
    (r"\bscore\b|\b\d+\s*/\s*100\b|\b\d+\s*%\s*confiden",
     "confidence is a level (High, Medium, Low), never a score or a number"),
    (r"\bguarantee",
     "the verdict is a guide, not a guarantee (ThriftFlipView's own note)"),
    (r"\bfree trial\b|\b\d+[- ]days?\b",
     "the trial length is an App Store Connect setting; no frame names it"),
    (r"\biPad\b", "the app is iPhone-only (TARGETED_DEVICE_FAMILY = 1)"),
]


def font(family: str, style: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONTS / ("Fraunces-Variable.ttf" if family == "fraunces" else "DMSans-Variable.ttf")
    f = ImageFont.truetype(str(path), size)
    f.set_variation_by_name(style)
    return f


@dataclass
class Shot:
    number: str                  # "01".."08": the gallery position
    slug: str
    raw: str                     # capture filename, as SCREENSHOT-HANDOFF.md §3 names it
    screen: str                  # what the capture shows, for error messages
    headline: list[str]          # one entry per line
    accent: str                  # the single word rendered in terracotta
    subhead: str
    pro: bool = False            # shows a Pro feature: PRO tag, and "Pro" in the subhead
    dark: bool = False
    tint: tuple | None = None    # (rgb, alpha, "lower"|"base") wash
    headline_size: int = 104

    @property
    def output_name(self) -> str:
        return f"en_69_{self.number}_{self.slug}.png"


# The order is the gallery order. The first three carry the pitch (#203).
SHOTS: list[Shot] = [
    Shot(
        number="01", slug="profit-after-fees",
        raw="raw_01_thrift-flip.png",
        screen="Thrift Flip, a Worth flipping verdict",
        headline=["Profit, after", "fees."], accent="fees.",
        subhead="Shop price in, fees out: a verdict before you buy. Free with your daily scan.",
        tint=(SAGE, 0.06, "lower"),
    ),
    Shot(
        number="02", slug="scan-a-whole-haul",
        raw="raw_02_haul.png",
        screen="Haul mode, the strip and the running total",
        headline=["Scan a", "whole haul."], accent="haul.",
        subhead="With Pro: snap item after item while each is valued, with a running total.",
        pro=True, dark=True,
    ),
    Shot(
        number="03", slug="know-before-you-buy",
        raw="raw_03_result.png",
        screen="the result sheet, range, confidence band and AI estimate",
        headline=["Know before", "you buy."], accent="buy.",
        subhead="An AI resale estimate from one photo, with its confidence level.",
    ),
    Shot(
        number="04", slug="your-listing-already-written",
        raw="raw_04_snap-sell.png",
        screen="Snap → Sell, a generated listing",
        headline=["Your listing,", "already written."], accent="written.",
        subhead="With Pro: a title and description tailored to where you sell, on nine marketplaces.",
        pro=True, headline_size=96,
    ),
    Shot(
        number="05", slug="every-flip-tracked",
        raw="raw_05_my-flips.png",
        screen="My Flips, the ledger",
        headline=["Every flip,", "tracked."], accent="tracked.",
        subhead="What you paid, what it sold for, and what you made after fees.",
        tint=(SAGE, 0.06, "lower"),
    ),
    Shot(
        number="06", slug="your-finds-at-a-glance",
        raw="raw_06_widgets.png",
        screen="a Home Screen page of SnapWorth widgets",
        headline=["Your finds,", "at a glance."], accent="glance.",
        subhead="Home Screen and Lock Screen widgets: total value, recent finds, one-tap scan.",
    ),
    Shot(
        number="07", slug="your-photos-stay-yours",
        raw="raw_07_my-finds.png",
        screen="My Finds, the grid",
        headline=["Your photos", "stay yours."], accent="yours.",
        subhead="Never stored on our servers. No account. Your scan history stays on your device.",
    ),
    Shot(
        number="08", slug="one-free-scan-every-day",
        raw="raw_08_plans.png",
        screen="the paywall's plan cards, with no trial line",
        headline=["One free scan,", "every day."], accent="free",
        subhead="Then Pro, when you want more. Cancel anytime.",
        tint=(TERRACOTTA, 0.06, "base"),
    ),
]


# ── Checks ───────────────────────────────────────────────────────────────────

def words(text: str) -> int:
    return len([w for w in re.split(r"\s+", text) if re.search(r"\w", w)])


def wrap(text: str, f: ImageFont.FreeTypeFont, measure: int) -> list[str]:
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if d.textlength(trial, font=f) > measure and current:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def check_caption(shot: Shot) -> list[str]:
    problems = []
    label = f"frame {shot.number} ({shot.slug})"
    headline = " ".join(shot.headline)

    if len(shot.headline) > 2:
        problems.append(f"{label}: headline is {len(shot.headline)} lines; the limit is 2")
    if words(headline) > HEADLINE_MAX_WORDS:
        problems.append(f"{label}: headline is {words(headline)} words; the limit is {HEADLINE_MAX_WORDS}")
    if sum(line.count(shot.accent) for line in shot.headline) != 1:
        problems.append(f"{label}: accent {shot.accent!r} must appear exactly once in the headline")
    hf = font("fraunces", "Bold", shot.headline_size)
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    for line in shot.headline:
        width = d.textlength(line, font=hf)
        if width > HEADLINE_MEASURE:
            problems.append(f"{label}: headline line {line!r} is {width:.0f}px wide; "
                            f"the measure is {HEADLINE_MEASURE}px")

    if words(shot.subhead) > SUBHEAD_MAX_WORDS:
        problems.append(f"{label}: subhead is {words(shot.subhead)} words; the limit is {SUBHEAD_MAX_WORDS}")
    lines = wrap(shot.subhead, font("dmsans", "Regular", SUBHEAD_SIZE), SUBHEAD_MEASURE)
    if len(lines) > 2:
        problems.append(f"{label}: subhead wraps to {len(lines)} lines; the limit is 2")

    if shot.pro and not re.search(r"\bPro\b", shot.subhead):
        problems.append(f"{label}: shows a Pro feature, so the subhead must say Pro (Guideline 2.3.2)")

    caption = f"{headline} {shot.subhead}"
    if re.search(r"\bunlimited\b", caption, re.IGNORECASE) and "fair use" not in caption.lower():
        problems.append(f"{label}: \"unlimited\" needs its fair-use qualifier "
                        f"(Pro scans are capped per hour on the server)")
    for pattern, reason in FORBIDDEN:
        hit = re.search(pattern, caption, re.IGNORECASE)
        if hit:
            problems.append(f"{label}: caption says {hit.group(0)!r}: {reason}")
    return problems


def check_capture(shot: Shot, captures: Path) -> list[str]:
    path = captures / shot.raw
    shown = display(path)
    where = f"frame {shot.number}: {shot.screen}"
    if not path.is_file():
        return [f"missing capture: {shown} ({where}). "
                f"SCREENSHOT-HANDOFF.md §3, frame {shot.number}, says how to take it."]
    try:
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            size = im.size
    except Exception as exc:  # noqa: BLE001 — any decode failure is the same answer
        return [f"unreadable capture: {shown} ({where}): {exc}"]
    if size != CAPTURE_SIZE:
        return [f"wrong size: {shown} is {size[0]}x{size[1]}; a 6.9\" iPhone capture is "
                f"{CAPTURE_SIZE[0]}x{CAPTURE_SIZE[1]} ({where})"]
    return []


def check_environment() -> list[str]:
    problems = []
    for name in ("Fraunces-Variable.ttf", "DMSans-Variable.ttf"):
        if not (FONTS / name).is_file():
            problems.append(f"missing font: {display(FONTS / name)}")
    return problems


def display(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


# ── Compositing ──────────────────────────────────────────────────────────────

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


def draw_pro_tag(canvas: Image.Image) -> None:
    """The in-app PRO badge's look (ResultView.proBadge), above the headline."""
    d = ImageDraw.Draw(canvas)
    f = font("dmsans", "Bold", 40)
    text = "PRO"
    left, top, right, bottom = d.textbbox((0, 0), text, font=f)
    pad_x, height = 32, 76
    width = (right - left) + pad_x * 2
    x0 = (W - width) // 2
    d.rounded_rectangle([x0, PRO_TAG_TOP, x0 + width, PRO_TAG_TOP + height],
                        radius=height // 2, fill=TERRACOTTA_FILL)
    d.text((x0 + pad_x - left, PRO_TAG_TOP + (height - (bottom - top)) / 2 - top),
           text, font=f, fill=CREAM)


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
    f = font("dmsans", "Regular", SUBHEAD_SIZE)
    ink = WARM_GREY_DARK if shot.dark else WARM_GREY
    y = max(y + 40, SUBHEAD_TOP)
    # `check_caption` has already refused a subhead longer than two lines.
    for line in wrap(shot.subhead, f, SUBHEAD_MEASURE):
        d.text(((W - d.textlength(line, font=f)) / 2, y), line, font=f, fill=ink)
        y += int(SUBHEAD_SIZE * 1.35)


def to_srgb(screen: Image.Image) -> Image.Image:
    """An iPhone capture is Display P3. Pasting its values into an sRGB file
    unconverted would shift the app's own terracotta and sage beside the
    caption's, which are sRGB by construction."""
    icc = screen.info.get("icc_profile")
    if not icc:
        return screen.convert("RGB")
    source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
    return ImageCms.profileToProfile(screen.convert("RGB"), source,
                                     ImageCms.createProfile("sRGB"), outputMode="RGB")


def place_device(canvas: Image.Image, raw_path: Path) -> None:
    """Composite the real capture into a drawn bezel."""
    with Image.open(raw_path) as im:
        screen = to_srgb(im)
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


def build(shot: Shot, captures: Path) -> Path:
    canvas = background(shot)
    if shot.pro:
        draw_pro_tag(canvas)
    bottom = draw_headline(canvas, shot)
    draw_subhead(canvas, shot, bottom)
    place_device(canvas, captures / shot.raw)

    OUT.mkdir(parents=True, exist_ok=True)
    out_path = OUT / shot.output_name
    # sRGB, 8-bit, no alpha, as SCREENSHOT-HANDOFF.md §5 exports.
    canvas.convert("RGB").save(out_path, "PNG", optimize=True)
    with Image.open(out_path) as written:
        assert written.size == (W, H) and written.mode == "RGB", out_path
    return out_path


# ── Entry point ──────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true",
                        help="validate the captures and captions; write nothing")
    parser.add_argument("--captures", type=Path, default=CAPTURES,
                        help=f"folder holding the captures (default: {display(CAPTURES)})")
    parser.add_argument("--skip", action="append", default=[], metavar="NN",
                        help="leave a frame out by number, e.g. --skip 02 (repeatable)")
    args = parser.parse_args(argv)

    numbers = {s.number for s in SHOTS}
    unknown = [n for n in args.skip if n not in numbers]
    if unknown:
        parser.error(f"--skip {', '.join(unknown)}: frames are {', '.join(sorted(numbers))}")
    shots = [s for s in SHOTS if s.number not in args.skip]
    captures = args.captures.resolve()

    print(f"{'Checking' if args.check else 'Building'} {len(shots)} of {len(SHOTS)} frames "
          f"(captures: {display(captures)}, output: {display(OUT)})")
    for s in SHOTS:
        state = "skipped" if s.number in args.skip else s.raw
        print(f"  {s.number}  {s.output_name:<44} {'PRO ' if s.pro else '    '}← {state}")

    problems = check_environment()
    if not problems:
        for shot in shots:
            problems += check_caption(shot)
            problems += check_capture(shot, captures)

    if problems:
        sys.stdout.flush()
        print(f"\nFAILED: {len(problems)} problem(s). Nothing was written.", file=sys.stderr)
        for p in problems:
            print(f"  ✗ {p}", file=sys.stderr)
        return 1

    if args.check:
        print(f"\nOK: {len(shots)} captures present at "
              f"{CAPTURE_SIZE[0]}x{CAPTURE_SIZE[1]}, captions within limits.")
        return 0

    print()
    for shot in shots:
        path = build(shot, captures)
        print(f"  ✓  {display(path)}  ({W}x{H})")
    print(f"\n{len(shots)} of {len(SHOTS)} built.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
