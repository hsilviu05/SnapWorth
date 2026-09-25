"""Tag's iMessage sticker pack: 16 stills and 4 APNG loops, plus the Messages icon set.

Everything is drawn from mascots.py, the app artwork's source, so Tag in Messages is the Tag in
the app and on the site. mascots.tag() is not used or changed: stickers need poses and faces the
app never shows, so this file composes mascots.py's helpers itself.

Apple's limits (Messages framework, MSSticker): PNG, APNG, GIF or JPEG; under 500 KB; 100–206 pt,
always @3x, so 300–618 px. These are 618 × 618 (206 pt), the sharpest Messages accepts.

The die-cut edge is the app's round one (bf64309): blur the alpha, keep what is above 5%, so the
border grows by 1.645 sigma with no stepping around the sparkles' points. It is computed in numpy
rather than as an SVG filter so the figure can be supersampled and the shadow composited
premultiplied.

    python3 export_stickers.py   # → stickers_out/Stickers.xcassets/…, preview.png, report.json

Needs Playwright's Chromium, Pillow and numpy. Copy `stickers_out/Stickers.xcassets` over
`ios/SnapWorthStickers/Stickers.xcassets`: the script owns every file in that catalog.
"""
import base64, functools, io, json, math, pathlib, shutil

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

import mascots as M

HERE = pathlib.Path(__file__).resolve().parent
FONTS = HERE.parents[1] / "website" / "fonts"   # the site's self-hosted Fraunces and DM Sans
OUT = HERE / "stickers_out"
PX, SS = 618, 2                     # final size; supersampling for the edge maths
R = PX * SS
EDGE = 15 * SS                      # die-cut border, px at R
SIGMA = EDGE / 1.645
TEAR, TEAR_INK = "#9CC9E6", M.ESP

# ───────────────────────────── Tag, posable ─────────────────────────────
def _loop():
    d = f"M512 {M.TAG_C[1]} C 470 222, 446 150, 486 122 C 528 94, 572 158, 512 {M.TAG_C[1]}"
    t = f'fill="none" stroke="{M.ESP}" stroke-width="11" stroke-linecap="round" stroke-linejoin="round"'
    cid = M.uid("n")
    back = f'<path d="{d}" {t}/>'
    front = (f'<defs><clipPath id="{cid}">' + "".join(M.draw(x) for x in M.TAG_NECK) + "</clipPath></defs>"
             f'<path d="M512 {M.TAG_C[1]} C 470 222, 446 150, 486 122" {t} clip-path="url(#{cid})"/>')
    return back, front


def _heart(x, y, s, fill=M.TERRA):
    p = "M0 30 C-44 0 -40 -44 0 -22 C40 -44 44 0 0 30 Z"
    return (f'<g transform="translate({x} {y}) scale({s / 60})"><path d="{p}" fill="{fill}" stroke="{M.ESP}" '
            f'stroke-width="7" stroke-linejoin="round"/><ellipse cx="-14" cy="-14" rx="7" ry="5" fill="#FFFFFF" opacity=".75"/></g>')


def _drop(x, y, s, fill=TEAR):
    return (f'<g transform="translate({x} {y}) scale({s / 40})"><path d="M0 -26 C12 -8 18 2 18 10 C18 22 10 30 0 30 '
            f'C-10 30 -18 22 -18 10 C-18 2 -12 -8 0 -26 Z" fill="{fill}" stroke="{TEAR_INK}" stroke-width="5" '
            f'stroke-linejoin="round"/><ellipse cx="-6" cy="8" rx="4" ry="6" fill="#FFFFFF" opacity=".8"/></g>')


def face(mood):
    ex, ey, dx = 512, 566, 86
    ck = M.cheeks(512, 636, 158)
    if mood in ("happy", "joy", "wow", "blink", "wink"):
        m = "happy" if mood in ("blink", "wink") else mood
        return M.eyes(ex, ey, dx, mood=mood) + ck + M.mouth(512, 632, 40, mood=m)
    if mood == "love":
        return (_heart(ex - dx, ey, 70) + _heart(ex + dx, ey, 70) + M.cheeks(512, 640, 158, r=36, op=0.7)
                + M.mouth(512, 628, 42, mood="joy"))
    if mood == "lol":
        return (M.eyes(ex, ey, dx, mood="joy") + ck + M.mouth(512, 626, 50, mood="joy")
                + _drop(ex - dx - 44, ey + 26, 30) + _drop(ex + dx + 44, ey + 26, 30))
    if mood == "sad":
        brows = "".join(f'<path d="M{ex + s * dx - 32} {ey - 60 + (14 if s < 0 else 0)} L{ex + s * dx + 32} {ey - 60 + (0 if s < 0 else 14)}" '
                        f'stroke="{M.ESP}" stroke-width="11" stroke-linecap="round"/>' for s in (-1, 1))
        frown = f'<path d="M472 660 Q512 630 552 660" fill="none" stroke="{M.ESP}" stroke-width="11" stroke-linecap="round"/>'
        return M.eyes(ex, ey + 6, dx, rx=24, ry=30) + brows + ck + frown + _drop(ex + dx + 6, ey + 58, 34)
    if mood == "think":
        eyes = M.eyes(ex - 12, ey - 10, dx, rx=25, ry=33)
        brows = (f'<path d="M{ex - dx - 34} {ey - 64} Q{ex - dx} {ey - 80} {ex - dx + 30} {ey - 66}" fill="none" stroke="{M.ESP}" stroke-width="10" stroke-linecap="round"/>'
                 f'<path d="M{ex + dx - 34} {ey - 58} L{ex + dx + 30} {ey - 58}" stroke="{M.ESP}" stroke-width="10" stroke-linecap="round"/>')
        mouth = f'<path d="M486 646 Q506 638 520 646 T552 644" fill="none" stroke="{M.ESP}" stroke-width="10" stroke-linecap="round"/>'
        return eyes + brows + ck + mouth
    if mood == "oops":
        return (M.eyes(ex, ey, dx, mood="wow") + M.cheeks(512, 640, 158, r=34, op=0.65)
                + f'<ellipse cx="512" cy="650" rx="16" ry="18" fill="{M.ESP}"/>' + _drop(700, 430, 44))
    if mood == "sleep":
        return (M.eyes(ex, ey + 4, dx, mood="blink") + ck
                + f'<ellipse cx="512" cy="648" rx="14" ry="11" fill="{M.ESP}"/>')
    raise ValueError(mood)


POSES = {"rest": (24, -24), "wave": (24, -140), "cheer": (128, -128), "out": (70, -70),
         "point": (24, -96), "shrug": (112, -112), "hold": (60, -60)}


def tag(mood="happy", pose="rest", back=None, back_color=M.SAGE_D, back_size=110, lean=0, hop=0, sq=0,
        flip=1.0, arms=None, legs=(0, 0), twinkle=True, arm_len=None):
    """Tag in the 1024 mascot space. `flip` is cos(phi): below 0 shows the back (price side)."""
    aL, aR = arms or POSES[pose]
    # raised arms hide behind the body at the resting length, so a cheer gets longer ones
    arm_len = arm_len or (150 if pose == "cheer" else 90)
    lb, lf = _loop()
    leg = f'<rect x="-37" y="-18" width="74" height="112" rx="37" fill="{M.ESP}"/>'
    legs_svg = (f'<g transform="translate(457 770) translate(0 {legs[0]}) rotate({-legs[0] * 0.6})">{leg}</g>'
                f'<g transform="translate(567 770) translate(0 {legs[1]}) rotate({legs[1] * 0.6})">{leg}</g>')
    arm = M.capsule(0, 0, 0, arm_len, 96, M.WHITE)
    arms_svg = (f'<g transform="translate(306 614) rotate({aL})">{arm}</g>'
                f'<g transform="translate(718 614) rotate({aR})">{arm}</g>')
    if flip < 0 and back is not None:
        lines = back.split("\n")
        n = len(lines)
        txt = "".join(f'<text x="512" y="{600 - (n - 1) * back_size * 0.5 + i * back_size * 0.98 + back_size * 0.34:.0f}" '
                      f'text-anchor="middle" font-family="Fraunces" font-weight="800" font-size="{back_size}" '
                      f'letter-spacing="-2" fill="{back_color}">{ln}</text>' for i, ln in enumerate(lines))
        face_svg = (f'<g transform="translate(1024 0) scale(-1 1)">{M.spark(512, 452 - (n - 1) * back_size * 0.5, 64)}{txt}</g>')
    else:
        face_svg = face(mood)
    tw = (M.spark(642, 214, 104, rot=8) + M.spark(700, 150, 44, rot=-6)) if twinkle else ""
    fc = flip if abs(flip) > 0.02 else 0.02
    sx, sy = 1 + sq * 0.8, 1 - sq
    inner = (f'<g transform="translate(512 0) scale({fc} 1) translate(-512 0)">{legs_svg}'
             f'<g transform="translate(512 797) scale({sx} {sy}) translate(-512 -797)">'
             f'{arms_svg}{lb}{M.tag_body()}{lf}{face_svg}{tw}</g></g>')
    return f'<g transform="translate(0 {-hop}) rotate({lean} 512 864)">{inner}</g>'


def place(svg, s=1.0, x=0, y=0):
    """Put a 1024-space drawing into the 1024 sticker canvas."""
    return f'<g transform="translate({x} {y}) scale({s})">{svg}</g>'


# the figure spans roughly x 176–878, y 79–918 in its own space; this centres it on the canvas
def centred(svg, s=0.92, dy=0):
    return place(svg, s, 512 - 527 * s, 512 - 498 * s + dy)


def bubble(text, x, y, w, h, tail_to, size=86, fill=M.WHITE):
    tx, ty = tail_to
    bx = x + w * 0.28
    return (f'<path d="M{bx - 30} {y + h - 8} L{tx} {ty} L{bx + 34} {y + h - 8} Z" fill="{fill}" stroke="{M.ESP}" stroke-width="12" stroke-linejoin="round"/>'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{h * 0.42:.0f}" fill="{fill}" stroke="{M.ESP}" stroke-width="12"/>'
            f'<path d="M{bx - 22} {y + h - 16} L{bx + 26} {y + h - 16}" stroke="{fill}" stroke-width="16"/>'
            f'<text x="{x + w / 2}" y="{y + h / 2 + size * 0.35:.0f}" text-anchor="middle" font-family="DM Sans" '
            f'font-weight="800" font-size="{size}" fill="{M.ESP}">{text}</text>')


def zzz(x, y, k=1.0, phase=0.0):
    out = ""
    for i, (dx, dy, fs) in enumerate(((0, 0, 70), (58, -70, 88), (128, -156, 108))):
        a = 1.0
        if phase:
            u = (phase + i / 3) % 1.0
            dy -= 60 * u
            a = math.sin(math.pi * u)
        out += (f'<text x="{x + dx}" y="{y + dy}" font-family="DM Sans" font-weight="800" '
                f'font-size="{fs * k:.0f}" fill="{M.TERRA_TEXT}" opacity="{a:.2f}">Z</text>')
    return out


# ───────────────────────────── the set ─────────────────────────────
STILLS = [
    ("hi", "Tag waving hello", lambda: centred(tag("happy", "wave"), 1.04)),
    ("yay", "Tag cheering with both arms up",
     lambda: centred(tag("joy", "cheer"), 0.96, 30) + M.spark(110, 300, 76, 10) + M.spark(920, 560, 60, -8)),
    ("wow", "Tag amazed, with sparkles in his eyes",
     lambda: centred(tag("wow", "out"), 0.98, 14) + M.spark(96, 230, 66) + M.spark(930, 300, 50, 20)),
    ("love", "Tag with heart eyes",
     lambda: centred(tag("love", "hold"), 0.98, 20) + _heart(120, 230, 80) + _heart(915, 360, 60)),
    ("wink", "Tag winking and pointing", lambda: centred(tag("wink", "point"), 1.04)),
    ("lol", "Tag laughing so hard he cries", lambda: centred(tag("lol", "out", lean=-5), 1.0, 16)),
    ("sad", "Tag sad, with a tear", lambda: centred(tag("sad", "rest", twinkle=False, lean=3), 1.06, 10)),
    ("sleepy", "Tag asleep, snoring zzz",
     lambda: centred(tag("sleep", "rest", twinkle=False, lean=-6), 0.98, 44) + zzz(690, 300)),
    ("hmm", "Tag thinking, with a question mark",
     lambda: centred(tag("think", "rest", twinkle=False), 1.0, 40)
     + f'<text x="790" y="270" font-family="Fraunces" font-weight="900" font-size="180" fill="{M.TERRA_TEXT}" transform="rotate(12 840 210)">?</text>'),
    ("oops", "Tag caught out, with a sweat drop", lambda: centred(tag("oops", "shrug"), 1.0, 16)),
    ("worth-it", "Price tag reading worth it",
     lambda: centred(tag(back="WORTH\nIT", flip=-1, pose="out", lean=-4), 1.04, 8)),
    ("steal", "Price tag reading steal",
     lambda: centred(tag(back="STEAL!", back_color=M.TERRA_TEXT, back_size=104, flip=-1, pose="cheer", lean=5), 0.98, 30)),
    ("sold", "Price tag reading sold",
     lambda: centred(tag(back="SOLD!", back_color=M.ESP, back_size=118, flip=-1, pose="cheer", lean=-3), 0.98, 30)
     + M.spark(96, 300, 66) + M.spark(930, 280, 52, 12)),
    ("priceless", "Price tag reading priceless",
     lambda: centred(tag(back="PRICE\nLESS", back_color=M.SAGE_D, back_size=104, flip=-1, pose="rest", lean=4), 1.06, 8)),
    ("tagged", "Tag saying tagged",
     lambda: place(tag("joy", "wave"), 0.82, 40, 186) + bubble("Tagged!", 370, 56, 570, 196, (500, 290), 96)),
    ("found-it", "Tag saying found it",
     lambda: place(tag("wow", "point"), 0.82, 24, 186) + bubble("Found it!", 320, 56, 620, 196, (480, 290), 96)),
]


def _ease(x):
    return x * x * (3 - 2 * x)


def anim_wave(n):
    frames = []
    for i in range(n):
        u = i / n
        a = -140 + 24 * math.sin(2 * math.pi * 2 * u)
        mood = "blink" if 0.55 <= u < 0.62 else "happy"
        bob = 8 * math.sin(2 * math.pi * u)
        frames.append(centred(tag(mood, arms=(24, a), hop=bob), 1.0))
    return frames


def anim_flip(n):
    frames = []
    for i in range(n):
        u = i / n
        # 0–.12 crouch · .12–.3 hop + flip · .3–.62 hold on the price side · .62–.8 flip back · .8–1 land
        hop = 110 * math.sin(math.pi * min(1, max(0, (u - 0.12) / 0.68)))
        f1 = _ease(min(1, max(0, (u - 0.14) / 0.16)))
        f2 = _ease(min(1, max(0, (u - 0.62) / 0.16)))
        phi = math.pi * (f1 + f2)
        sq = 0.12 * math.sin(math.pi * min(1, u / 0.12)) if u < 0.12 else (0.1 * math.sin(math.pi * (u - 0.86) / 0.14) if u > 0.86 else 0)
        mood = "wow" if u < 0.3 else "joy"
        frames.append(centred(tag(mood, "out" if 0.12 < u < 0.86 else "rest", back="WORTH\nIT", flip=math.cos(phi),
                                  hop=hop, sq=sq), 0.94, 40))
    return frames


def anim_cheer(n):
    frames = []
    for i in range(n):
        u = i / n
        s = math.sin(2 * math.pi * 2 * u)
        hop = 36 * abs(math.sin(2 * math.pi * u))
        spk = (M.spark(150, 330, 60 + 18 * s, 10 + 40 * u) + M.spark(880, 480, 50 - 14 * s, -8 - 40 * u))
        frames.append(centred(tag("joy", arms=(128 + 12 * s, -128 - 12 * s), hop=hop, arm_len=150), 0.94, 36) + spk)
    return frames


def anim_snooze(n):
    frames = []
    for i in range(n):
        u = i / n
        bob = 6 * math.sin(2 * math.pi * u)
        frames.append(centred(tag("sleep", "rest", twinkle=False, lean=-6, sq=0.02 * math.sin(2 * math.pi * u), hop=bob), 0.96, 44)
                      + zzz(680, 380, phase=u))
    return frames


ANIMS = [
    ("hi-wave", "Tag waving hello, animated", anim_wave, 16, 70),
    ("worth-it-flip", "Tag flipping over to show worth it, animated", anim_flip, 24, 80),
    ("yay-bounce", "Tag bouncing and cheering, animated", anim_cheer, 14, 70),
    ("snooze", "Tag snoozing, animated", anim_snooze, 16, 110),
]


# ───────────────────────────── rendering ─────────────────────────────
def _b64(p):
    return base64.b64encode(p.read_bytes()).decode()


class Renderer:
    def __init__(self):
        self.pw = sync_playwright().start()
        self.b = self.pw.chromium.launch(args=["--force-color-profile=srgb"])
        self.pg = self.b.new_page(viewport={"width": R, "height": R}, device_scale_factor=1)
        css = (f'@font-face{{font-family:"Fraunces";src:url(data:font/woff2;base64,{_b64(FONTS / "fraunces-latin.woff2")}) format("woff2");font-weight:100 900}}'
               f'@font-face{{font-family:"Fraunces";font-style:italic;src:url(data:font/woff2;base64,{_b64(FONTS / "fraunces-italic-latin.woff2")}) format("woff2");font-weight:100 900}}'
               f'@font-face{{font-family:"DM Sans";src:url(data:font/woff2;base64,{_b64(FONTS / "dmsans-latin.woff2")}) format("woff2");font-weight:100 1000}}'
               'html,body{margin:0;background:transparent}')
        self.pg.set_content(f'<html><head><style>{css}</style></head><body><div id="s"></div></body></html>')
        self.pg.evaluate("document.fonts.load('800 40px Fraunces').then(()=>document.fonts.load('italic 800 40px Fraunces')).then(()=>document.fonts.load('800 40px \"DM Sans\"'))")
        self.pg.wait_for_timeout(300)

    def raw(self, inner):
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="{R}" height="{R}" '
               f'style="display:block">{inner}</svg>')
        self.pg.evaluate("s => { document.getElementById('s').innerHTML = s; }", svg)
        png = self.pg.screenshot(omit_background=True, clip={"x": 0, "y": 0, "width": R, "height": R})
        return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"), np.float32) / 255.0

    def close(self):
        self.b.close()
        self.pw.stop()


CREAM = np.array([0xFB, 0xF7, 0xF2], np.float32) / 255
ESP_RGB = np.array([0x2B, 0x21, 0x1C], np.float32) / 255


@functools.lru_cache(maxsize=4)
def _band(n, sigma):
    """n × (n + 2r) matrix whose rows are the Gaussian kernel, each shifted one sample along."""
    r = int(4.0 * sigma + 0.5)
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    band = np.zeros((n, n + 2 * r))
    for i in range(n):
        band[i, i:i + 2 * r + 1] = k
    return band, r


def _blur(a, sigma):
    """Gaussian blur of a 2-D array; the same result as scipy.ndimage.gaussian_filter's defaults
    (kernel cut at 4 sigma, edges mirrored), as two matrix products, so scipy isn't a dependency."""
    out = a.astype(np.float64)
    for _ in range(2):                      # rows, then columns via the transpose
        band, r = _band(out.shape[0], sigma)
        out = (band @ np.pad(out, ((r, r), (0, 0)), mode="symmetric")).T
    return out.astype(a.dtype)


def die_cut(fig, shadow=True):
    """Figure RGBA (0–1, straight alpha) → sticker with a round cream edge and a soft shadow, downsampled."""
    a = fig[..., 3]
    edge = np.clip(40 * _blur(a, SIGMA) - 1.5, 0, 1)
    out_a = edge.copy()
    out_rgb = CREAM[None, None, :] * edge[..., None]
    if shadow:
        sh = _blur(edge, 7 * SS)
        sh = np.roll(sh, 5 * SS, axis=0) * 0.22
        comb = out_a + sh * (1 - out_a)
        out_rgb = out_rgb + ESP_RGB[None, None, :] * (sh * (1 - out_a))[..., None]
        out_a = comb
    # figure over (premultiplied)
    out_rgb = fig[..., :3] * a[..., None] + out_rgb * (1 - a[..., None])
    out_a = a + out_a * (1 - a)
    prem = np.dstack([out_rgb, out_a])
    img = Image.fromarray(np.clip(prem * 255 + 0.5, 0, 255).astype(np.uint8), "RGBa")
    img = img.resize((PX, PX), Image.LANCZOS).convert("RGBA")
    return img


def check_margin(img, name):
    a = np.asarray(img)[..., 3]
    border = np.concatenate([a[:3].ravel(), a[-3:].ravel(), a[:, :3].ravel(), a[:, -3:].ravel()])
    if border.max() > 8:
        raise SystemExit(f"{name}: artwork touches the edge of the canvas")


def shared_palette(frames):
    """Quantise every frame to ONE 256-colour palette with alpha: APNG has a single PLTE, and full RGBA
    frames at 618 px run ~60 KB each, twice Apple's 500 KB budget for a 16-frame loop. Quantising the
    frames as one tiled image gives them the same palette by construction."""
    cols = math.ceil(math.sqrt(len(frames)))
    rows = math.ceil(len(frames) / cols)
    mosaic = Image.new("RGBA", (cols * PX, rows * PX), (0, 0, 0, 0))
    for i, f in enumerate(frames):
        mosaic.paste(f, ((i % cols) * PX, (i // cols) * PX))
    q = mosaic.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    return [q.crop(((i % cols) * PX, (i // cols) * PX, (i % cols + 1) * PX, (i // cols + 1) * PX))
            for i in range(len(frames))]


def apng(frames, path, ms):
    frames = shared_palette(frames)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=ms, loop=0, disposal=0, blend=0,
                   optimize=True)


# ───────────────────────────── the Messages icon ─────────────────────────────
ICON_SLOTS = [  # (size in pt, idiom, scale, platform or None)
    ("29x29", "iphone", 2, None), ("29x29", "iphone", 3, None),
    ("60x45", "iphone", 2, None), ("60x45", "iphone", 3, None),
    ("29x29", "ipad", 2, None), ("67x50", "ipad", 2, None), ("74x55", "ipad", 2, None),
    ("27x20", "universal", 2, "ios"), ("27x20", "universal", 3, "ios"),
    ("32x24", "universal", 2, "ios"), ("32x24", "universal", 3, "ios"),
    ("1024x768", "ios-marketing", 1, "ios"),
]


def icon_master(r, w, h):
    """Tag waving on the app icon's terracotta, as a w × h image (no transparency: App Store icons are opaque)."""
    bg = (f'<defs><linearGradient id="ig" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#E07A55"/>'
          f'<stop offset="1" stop-color="#C95B38"/></linearGradient></defs><rect width="{w}" height="{h}" fill="url(#ig)"/>')
    s = h / 1024 * 0.9
    fig = f'<g transform="translate({w / 2 - 527 * s} {h / 2 - 470 * s}) scale({s})">{tag("happy", "wave")}</g>'
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">{bg}{fig}</svg>')
    r.pg.set_viewport_size({"width": w, "height": h})
    r.pg.evaluate("s => { document.getElementById('s').innerHTML = s; }", svg)
    png = r.pg.screenshot(clip={"x": 0, "y": 0, "width": w, "height": h})
    r.pg.set_viewport_size({"width": R, "height": R})
    return Image.open(io.BytesIO(png)).convert("RGB")


# ───────────────────────────── main ─────────────────────────────
def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    cat = OUT / "Stickers.xcassets"            # the name Xcode 27's Sticker Pack Extension template uses
    pack = cat / "Sticker Pack.stickerpack"
    pack.mkdir(parents=True)
    info = {"author": "xcode", "version": 1}
    (cat / "Contents.json").write_text(json.dumps({"info": info}, indent=2) + "\n")
    entries, report = [], []
    r = Renderer()

    def write(name, label, img_or_path):
        folder = pack / f"{name}.sticker"
        folder.mkdir()
        fn = f"tag-{name}.png"
        if isinstance(img_or_path, Image.Image):
            img_or_path.save(folder / fn, optimize=True)
        else:
            shutil.move(str(img_or_path), folder / fn)
        (folder / "Contents.json").write_text(json.dumps(
            {"info": info, "properties": {"accessibility-label": label, "filename": fn}}, indent=2) + "\n")
        entries.append({"filename": f"{name}.sticker"})
        kb = (folder / fn).stat().st_size / 1024
        report.append((name, label, round(kb, 1)))
        assert kb < 500, f"{name} is {kb:.0f} KB, over Apple's 500 KB limit"
        assert len(label) <= 150

    previews = []
    for name, label, build in STILLS:
        img = die_cut(r.raw(build()))
        check_margin(img, name)
        write(name, label, img)
        previews.append(img)
    for name, label, fn, n, ms in ANIMS:
        frames = [die_cut(r.raw(svg), shadow=False) for svg in fn(n)]
        for k, f in enumerate(frames):
            check_margin(f, f"{name}#{k}")
        tmp = OUT / f"{name}.png"
        apng(frames, tmp, ms)
        write(name, label, tmp)
        previews.append(frames[len(frames) // 3])
    (pack / "Contents.json").write_text(json.dumps(
        {"info": info, "properties": {"grid-size": "regular"}, "stickers": entries}, indent=2) + "\n")

    # Messages icon set
    iset = cat / "iMessage App Icon.stickersiconset"
    iset.mkdir()
    images = []
    m43, m11 = icon_master(r, 1024, 768), icon_master(r, 1024, 1024)
    for size, idiom, scale, platform in ICON_SLOTS:
        w, h = (int(v) * scale for v in size.split("x"))
        fn = f"imessage-icon-{w}x{h}.png"
        if not (iset / fn).exists():
            (m11 if w == h else m43).resize((w, h), Image.LANCZOS).save(iset / fn, optimize=True)
        entry = {"filename": fn, "idiom": idiom, "scale": f"{scale}x", "size": size}
        if platform:
            entry["platform"] = platform
        images.append(entry)
    (iset / "Contents.json").write_text(json.dumps({"images": images, "info": info}, indent=2) + "\n")
    r.close()

    # preview sheet on three chat backgrounds
    cols, cell = 5, 220
    rows = math.ceil(len(previews) / cols)
    sheet = Image.new("RGB", (cols * cell * 3 + 40, rows * cell + 20), "#888888")
    for bi, bg in enumerate(("#FFFFFF", "#1C1C1E", "#0A84FF")):
        panel = Image.new("RGB", (cols * cell, rows * cell), bg)
        for i, im in enumerate(previews):
            t = im.resize((cell - 20, cell - 20), Image.LANCZOS)
            panel.paste(t, ((i % cols) * cell + 10, (i // cols) * cell + 10), t)
        sheet.paste(panel, (bi * (cols * cell + 20), 10))
    sheet.save(OUT / "preview.png")
    (OUT / "report.json").write_text(json.dumps(report, indent=1))
    for row in report:
        print(f"{row[0]:<16}{row[2]:>7} KB  {row[1]}")


if __name__ == "__main__":
    main()
