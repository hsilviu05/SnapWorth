"""Three original SnapWorth mascot concepts as SVG, in the app's palette (DesignSystem.swift).

A · Tag   — the app icon's price tag, alive. Same body/neck/hole proportions as AppIcon.png.
B · Snap  — a terracotta camera whose lens is its face.
C · Scout — a thrift-savvy fox in a sage neckerchief.

Construction: every part is drawn twice — an espresso "outline pass" at 2×OL stroke, then the
fill pass on top — so overlapping shapes merge into one clean silhouette with no inner lines.
"""
import math

ESP, CREAM, WHITE, BORDER = "#2B211C", "#FBF7F2", "#FFFFFF", "#EFE6DC"
TERRA, TERRA_HC, TERRA_TEXT, TERRA_D = "#D96C47", "#BE5433", "#B34D2E", "#A8482C"
SAGE, SAGE_D, AMBER, AMBER_L = "#6F8F6B", "#4F6E4B", "#EBB868", "#F5D495"
BLUSH = "#F08F6E"
OL = 12  # outline half-width → visible line of OL px at 1024

_uid = [0]
def uid(p):
    _uid[0] += 1
    return f"{p}{_uid[0]}"

def attrs(a):
    return " ".join(f'{k.rstrip("_").replace("_", "-")}="{v}"' for k, v in a.items())

def el(tag, **a):
    return (tag, a)

def draw(shape, **extra):
    tag, a = shape
    return f"<{tag} {attrs({**a, **extra})}/>"

def solid(shapes, fill, shade=None, off=(-20, -22), ol=OL, mask=None):
    """Outlined union of shapes. `shade` paints a crescent on the lower-right for volume."""
    m = f' mask="url(#{mask})"' if mask else ""
    out = f"<g{m}>" + "".join(draw(s, fill=ESP, stroke=ESP, stroke_width=2 * ol, stroke_linejoin="round") for s in shapes)
    if shade:
        cid = uid("c")
        out += f'<defs><clipPath id="{cid}">' + "".join(draw(s) for s in shapes) + "</clipPath></defs>"
        out += f'<g clip-path="url(#{cid})">' + "".join(draw(s, fill=shade) for s in shapes)
        out += f'<g transform="translate({off[0]} {off[1]})">' + "".join(draw(s, fill=fill) for s in shapes) + "</g></g>"
    else:
        out += "".join(draw(s, fill=fill) for s in shapes)
    return out + "</g>"

def capsule(x1, y1, x2, y2, w, fill, ol=OL):
    d = f"M{x1} {y1} L{x2} {y2}"
    return (f'<path d="{d}" stroke="{ESP}" stroke-width="{w + 2 * ol}" stroke-linecap="round" fill="none"/>'
            f'<path d="{d}" stroke="{fill}" stroke-width="{w}" stroke-linecap="round" fill="none"/>')

# The app icon's sparkle: gold four-point star, lighter inner star, white core.
STAR = "M50 0 L59 41 L100 50 L59 59 L50 100 L41 59 L0 50 L41 41 Z"
def spark(x, y, s, rot=0, core=True):
    g = f'<g transform="translate({x} {y}) rotate({rot}) scale({s / 100}) translate(-50 -50)">'
    g += f'<path d="{STAR}" fill="{AMBER}" stroke="{AMBER}" stroke-width="3" stroke-linejoin="round"/>'
    if core:
        g += f'<path d="{STAR}" fill="{AMBER_L}" transform="translate(50 50) scale(.58) translate(-50 -50)"/>'
        g += f'<path d="{STAR}" fill="{WHITE}" transform="translate(50 50) scale(.22) translate(-50 -50)"/>'
    return g + "</g>"

def eyes(cx, cy, dx, rx=27, ry=35, mood="happy", ink=ESP, shine=WHITE):
    out = ""
    for sx in (-1, 1):
        x = cx + sx * dx
        if mood == "joy":
            out += (f'<path d="M{x - rx * 1.05} {cy + 8} Q{x} {cy - ry * 1.05} {x + rx * 1.05} {cy + 8}" fill="none" '
                    f'stroke="{ink}" stroke-width="{rx * 0.46:.1f}" stroke-linecap="round"/>')
        elif mood == "blink":   # lids shut, mid-blink
            out += (f'<path d="M{x - rx * 0.95} {cy + 2} Q{x} {cy + ry * 0.32} {x + rx * 0.95} {cy + 2}" fill="none" '
                    f'stroke="{ink}" stroke-width="{rx * 0.42:.1f}" stroke-linecap="round"/>')
        elif mood == "wink" and sx == 1:
            out += (f'<path d="M{x - rx} {cy - 2} Q{x} {cy + ry * 0.55} {x + rx} {cy - 2}" fill="none" '
                    f'stroke="{ink}" stroke-width="{rx * 0.46:.1f}" stroke-linecap="round"/>')
        else:
            if mood == "wow" and shine is None:   # star eyes
                out += spark(x, cy, ry * 2.6, rot=0)
                continue
            k = 1.14 if mood == "wow" else 1
            out += f'<ellipse cx="{x}" cy="{cy}" rx="{rx * k:.1f}" ry="{ry * k:.1f}" fill="{ink}"/>'
            if shine is None:
                continue
            if mood == "wow":
                out += spark(x - rx * 0.28, cy - ry * 0.3, rx * 1.15, core=False).replace(AMBER, shine)
                out += f'<circle cx="{x + rx * 0.4:.1f}" cy="{cy + ry * 0.42:.1f}" r="{rx * 0.16:.1f}" fill="{shine}" opacity=".85"/>'
            else:
                out += f'<circle cx="{x - rx * 0.3:.1f}" cy="{cy - ry * 0.36:.1f}" r="{rx * 0.38:.1f}" fill="{shine}"/>'
                out += f'<circle cx="{x + rx * 0.34:.1f}" cy="{cy + ry * 0.36:.1f}" r="{rx * 0.16:.1f}" fill="{shine}" opacity=".85"/>'
    return out

def mouth(cx, cy, w, mood="happy", ink=ESP, tongue=TERRA):
    if mood == "wow":
        return (f'<ellipse cx="{cx}" cy="{cy + w * 0.3:.1f}" rx="{w * 0.48:.1f}" ry="{w * 0.6:.1f}" fill="{ink}"/>'
                f'<ellipse cx="{cx}" cy="{cy + w * 0.62:.1f}" rx="{w * 0.28:.1f}" ry="{w * 0.18:.1f}" fill="{tongue}"/>')
    if mood == "joy":
        cid = uid("m")
        d = f"M{cx - w} {cy - 6} Q{cx - w} {cy + w * 1.25} {cx} {cy + w * 1.25} Q{cx + w} {cy + w * 1.25} {cx + w} {cy - 6} Z"
        return (f'<defs><clipPath id="{cid}"><path d="{d}"/></clipPath></defs><path d="{d}" fill="{ink}" stroke="{ink}" stroke-width="6" stroke-linejoin="round"/>'
                f'<ellipse cx="{cx}" cy="{cy + w * 1.2:.1f}" rx="{w * 0.62:.1f}" ry="{w * 0.5:.1f}" fill="{tongue}" clip-path="url(#{cid})"/>')
    return (f'<path d="M{cx - w} {cy} Q{cx} {cy + w * 0.95} {cx + w} {cy}" fill="none" stroke="{ink}" '
            f'stroke-width="{max(9, w * 0.26):.1f}" stroke-linecap="round"/>')

def cheeks(cx, cy, dx, r=30, color=BLUSH, op=0.55):
    return "".join(f'<ellipse cx="{cx + sx * dx}" cy="{cy}" rx="{r}" ry="{r * 0.6:.1f}" fill="{color}" opacity="{op}"/>' for sx in (-1, 1))

SHADOW = True  # ground shadow; off for sticker exports

def shadow(cx, cy, rx):
    if not SHADOW:
        return ""
    return f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{rx * 0.12:.1f}" fill="{ESP}" opacity=".13"/>'

def tangent_pts(p, c, r):
    """Tangent points on circle (c, r) seen from external point p: (left, right)."""
    dx, dy = c[0] - p[0], c[1] - p[1]
    d = math.hypot(dx, dy)
    a = math.atan2(dy, dx)
    b = math.asin(r / d)
    t = math.sqrt(d * d - r * r)
    return [(p[0] + t * math.cos(a + s * b), p[1] + t * math.sin(a + s * b)) for s in (-1, 1)]

# ── A · Tag ─────────────────────────────────────────────────────────────────
# AppIcon.png: body 288..737 × 353..797 (r≈56), neck narrowing to a ring at (512, 271).
TAG_BODY = el("rect", x=288, y=353, width=448, height=444, rx=60)
TAG_C, TAG_R, TAG_HOLE = (512, 280), 52, 25
_bl, _br = (428, 362), (596, 362)
_tl = tangent_pts(_bl, TAG_C, TAG_R)[0]
_tr = tangent_pts(_br, TAG_C, TAG_R)[1]
TAG_NECK = [el("polygon", points=f"{_bl[0]},{_bl[1]} {_tl[0]:.1f},{_tl[1]:.1f} {_tr[0]:.1f},{_tr[1]:.1f} {_br[0]},{_br[1]}"),
            el("circle", cx=TAG_C[0], cy=TAG_C[1], r=TAG_R)]

def tag_body(fill=WHITE, shade=BORDER, hole=True):
    mid = uid("h")
    mask = (f'<defs><mask id="{mid}" maskUnits="userSpaceOnUse" x="0" y="0" width="1024" height="1024">'
            f'<rect width="1024" height="1024" fill="#fff"/><circle cx="{TAG_C[0]}" cy="{TAG_C[1]}" r="{TAG_HOLE}" fill="#000"/></mask></defs>') if hole else ""
    ring = f'<circle cx="{TAG_C[0]}" cy="{TAG_C[1]}" r="{TAG_HOLE + OL / 2}" fill="none" stroke="{ESP}" stroke-width="{OL}"/>' if hole else ""
    return mask + solid([TAG_BODY] + TAG_NECK, fill, shade=shade, mask=mid if hole else None) + ring

def tag(mood="happy", pose="wave"):
    legs = "".join(f'<rect x="{x}" y="752" width="74" height="112" rx="37" fill="{ESP}"/>' for x in (420, 530))
    if pose == "cheer":
        arms = capsule(300, 584, 238, 500, 96, WHITE) + capsule(724, 584, 786, 500, 96, WHITE)
    elif pose == "wave":
        arms = capsule(302, 606, 266, 686, 96, WHITE) + capsule(722, 596, 788, 518, 96, WHITE)
    else:
        arms = capsule(302, 606, 266, 686, 96, WHITE) + capsule(722, 606, 758, 686, 96, WHITE)
    # the thread loops through the hole: whole loop behind the tag (shows through the hole),
    # then the front strand again, clipped to the neck, so only one strand crosses the front
    loop_d = f"M512 {TAG_C[1]} C 470 222, 446 150, 486 122 C 528 94, 572 158, 512 {TAG_C[1]}"
    thread = f'fill="none" stroke="{ESP}" stroke-width="11" stroke-linecap="round" stroke-linejoin="round"'
    nid = uid("n")
    loop_back = f'<path d="{loop_d}" {thread}/>'
    loop_front = (f'<defs><clipPath id="{nid}">' + "".join(draw(x) for x in TAG_NECK) + '</clipPath></defs>'
                  f'<path d="M512 {TAG_C[1]} C 470 222, 446 150, 486 122" {thread} clip-path="url(#{nid})"/>')
    face = eyes(512, 566, 86, mood=mood) + cheeks(512, 636, 158) + mouth(512, 632, 40, mood=mood)
    twinkle = spark(642, 214, 104, rot=8) + spark(700, 150, 44, rot=-6)
    return shadow(512, 880, 214) + legs + arms + loop_back + tag_body() + loop_front + face + twinkle

# ── B · Snap ────────────────────────────────────────────────────────────────
def snap(mood="happy"):
    legs = "".join(f'<rect x="{x}" y="760" width="78" height="112" rx="39" fill="{ESP}"/>' for x in (396, 550))
    shutter = solid([el("rect", x=646, y=344, width=92, height=80, rx=22)], AMBER, shade="#D9A04F", off=(-8, -10))
    body = solid([el("rect", x=222, y=398, width=580, height=400, rx=112),
                  el("rect", x=396, y=326, width=232, height=120, rx=40)], TERRA, shade=TERRA_HC)
    band_clip = uid("b")
    band = (f'<defs><clipPath id="{band_clip}"><rect x="222" y="398" width="580" height="400" rx="112"/></clipPath></defs>'
            f'<rect x="200" y="566" width="620" height="164" fill="{TERRA_D}" opacity=".55" clip-path="url(#{band_clip})"/>')
    finder = f'<rect x="468" y="354" width="88" height="44" rx="14" fill="{ESP}"/><rect x="480" y="362" width="26" height="12" rx="6" fill="{WHITE}" opacity=".55"/>'
    flash = solid([el("rect", x=262, y=438, width=92, height=56, rx=16)], CREAM, ol=OL * 0.75)
    # lens = face: cream ring → espresso barrel → dark glass with a cream face on it
    lens = (solid([el("circle", cx=512, cy=606, r=170)], CREAM, shade=BORDER, off=(-10, -12))
            + f'<circle cx="512" cy="606" r="138" fill="{ESP}"/>'
            + f'<circle cx="512" cy="606" r="118" fill="#3A2D26"/>'
            + f'<path d="M430 548 A 108 108 0 0 1 546 506" fill="none" stroke="{WHITE}" stroke-width="12" stroke-linecap="round" opacity=".22"/>')
    face = eyes(512, 598, 50, rx=20, ry=27, mood=mood, ink=CREAM, shine=None) \
        + cheeks(512, 646, 80, r=18, op=0.8) + mouth(512, 644, 26, mood=mood, ink=CREAM, tongue=TERRA)
    strap = (f'<path d="M800 470 C 872 492, 884 560, 866 628" fill="none" stroke="{ESP}" stroke-width="14" stroke-linecap="round"/>'
             f'<g transform="translate(872 700) rotate(-12) scale(.27) translate(-512 -575)">{tag_body(hole=True)}{spark(512, 575, 250)}</g>')
    pop = spark(236, 360, 86, rot=10) if mood == "wow" else spark(250, 372, 58, rot=10)
    return shadow(512, 884, 236) + legs + shutter + body + band + finder + flash + lens + face + strap + pop

# ── C · Scout ───────────────────────────────────────────────────────────────
def scout(mood="happy"):
    tail = solid([el("path", d="M600 812 C 800 846, 900 700, 870 560 C 852 486, 776 470, 756 532 C 786 600, 756 700, 610 724 Z")], TERRA, shade=TERRA_HC, off=(-14, -14))
    tail_tip = f'<path d="M870 560 C 852 486, 776 470, 756 532 C 770 566, 776 598, 772 628 C 820 612, 862 590, 870 560 Z" fill="{CREAM}"/>'
    body = solid([el("path", d="M394 866 C 376 736, 410 640, 512 640 C 614 640, 648 736, 630 866 Z")], TERRA, shade=TERRA_HC, off=(-14, -12))
    belly = f'<path d="M462 866 C 452 790, 470 724, 512 724 C 554 724, 572 790, 562 866 Z" fill="{CREAM}"/>'
    feet = "".join(f'<ellipse cx="{x}" cy="866" rx="56" ry="28" fill="{ESP}"/>' for x in (446, 578))
    ears, inner = [], ""
    for sx in (-1, 1):
        bx = 512 + sx * 150
        ears.append(el("path", d=f"M{bx - sx * 96} 356 Q{bx - sx * 10} 170 {bx + sx * 40} 142 Q{bx + sx * 90} 250 {bx + sx * 102} 384 Z"))
        inner += f'<path d="M{bx - sx * 46} 336 Q{bx + sx * 6} 220 {bx + sx * 36} 196 Q{bx + sx * 66} 270 {bx + sx * 70} 350 Z" fill="{TERRA_D}"/>'
    head = el("path", d="M272 430 C 272 312, 380 262, 512 262 C 644 262, 752 312, 752 430 C 752 560, 650 648, 512 650 C 374 648, 272 560, 272 430 Z")
    head_draw = solid(ears, TERRA) + inner + solid([head], TERRA, shade=TERRA_HC, off=(-16, -16))
    muzzle = f'<path d="M300 486 C 380 470, 452 506, 512 556 C 572 506, 644 470, 724 486 C 706 590, 618 646, 512 648 C 406 646, 318 590, 300 486 Z" fill="{CREAM}"/>'
    nose = f'<path d="M484 552 Q512 536 540 552 Q536 580 512 586 Q488 580 484 552 Z" fill="{ESP}"/><ellipse cx="503" cy="552" rx="9" ry="5" fill="{WHITE}" opacity=".6"/>'
    if mood == "happy":
        m = f'<path d="M482 600 Q497 618 512 600 Q527 618 542 600" fill="none" stroke="{ESP}" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>'
    else:
        m = mouth(512, 604, 28, mood=mood)
    face = eyes(512, 456, 104, rx=30, ry=38, mood=mood) + cheeks(512, 540, 168, r=26) + nose + m
    kerchief = solid([el("path", d="M404 628 Q512 668 620 628 L 560 728 Q512 770 464 728 Z")], SAGE, shade=SAGE_D, off=(-8, -12))
    charm = f'<g transform="translate(512 792) rotate(-6) scale(.2) translate(-512 -575)">{tag_body()}{spark(512, 575, 250)}</g>'
    string = f'<path d="M512 742 L512 748" stroke="{ESP}" stroke-width="10" stroke-linecap="round"/>'
    return shadow(512, 890, 250) + tail + tail_tip + body + belly + feet + head_draw + muzzle + face + kerchief + string + charm

def svg(inner, bg=None, size=1024, vb="0 60 1024 904"):
    b = f'<rect x="-10" y="-10" width="1044" height="1044" fill="{bg}"/>' if bg else ""
    x, y, w, h = map(float, vb.split())
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" width="{size}" height="{int(size * h / w)}">{b}{inner}</svg>'

if __name__ == "__main__":
    import pathlib
    out = pathlib.Path(__file__).parent / "svg"
    out.mkdir(exist_ok=True)
    for name, fn in (("tag", tag), ("snap", snap), ("scout", scout)):
        for mood in ("happy", "joy", "wow"):
            (out / f"{name}_{mood}.svg").write_text(svg(fn(mood=mood)))
    print("ok")
