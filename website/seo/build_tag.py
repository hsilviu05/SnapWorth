#!/usr/bin/env python3
"""Tag, the mascot, as inline SVG for the website: index.html and 404.html.

    python3 website/seo/build_tag.py           # rewrite the blocks
    python3 website/seo/build_tag.py --check   # exit 1 if a committed block is stale

The drawing comes from marketing/mascot/mascots.py, the same source as the
app's imagesets; nothing here redraws Tag, it only takes him apart. The app
needs flat PNGs; the site needs parts that move, so this composes the same
helpers (`solid`, `capsule`, `tag_body`, `eyes`, `mouth`, `cheeks`, `spark`)
into separately addressable groups. `mascots.tag()` is not touched.

Layers. Every silhouette part appears twice: once in `.tag-edge`, stroked in
cream 18 units outside its outermost line, and once in `.tag-ink`, drawn as
usual. All edges sit under all ink, so the body's edge can never paint a cream
stripe across an arm. It is how the app's dark artwork reads (one die-cut
outline) without `feMorphology`, which steps around sharp points (bf64309), or
a blur on an animating figure. A moving part is the same class in both layers
and takes its transform from a CSS variable on the root, so one write moves
both copies.

Ids. `mascots.py` numbers its defs ids with a module-wide counter; the build
swaps in a `tag-`-prefixed counter so nothing collides with page ids.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "marketing" / "mascot"))
import mascots as M  # noqa: E402

BEGIN, END = "<!-- BEGIN TAG SVG -->", "<!-- END TAG SVG -->"
VIEWBOX = "182 85 690 803"            # the app's canvas (export_app_assets.py)
EDGE = 18                             # cream edge outside the outermost line
CREAM = M.CREAM
SHOULDERS = {"l": (306, 614), "r": (718, 614)}
HIPS = {"l": (457, 770), "r": (567, 770)}
ARM_W, ARM_L = 96, 90
MOODS = ("happy", "joy", "wow", "blink")


def _ids():
    n = [0]

    def uid(p):
        n[0] += 1
        return f"tag-{p}{n[0]}"
    return uid


def _num(svg: str) -> str:
    """Shorter numbers: 263.2000 → 263.2, 12.0 → 12. Keeps the block small."""
    return re.sub(r"(\d+)\.(\d*?)0+\b", lambda m: m.group(1) + ("." + m.group(2) if m.group(2) else ""), svg)


def _stroke(shape: str, width: float, fill: str | None) -> str:
    """One shape as a cream edge: `width` wide, round joins and caps."""
    f = f' fill="{fill}"' if fill else ' fill="none"'
    return shape[:-2].rstrip() + f'{f} stroke="{CREAM}" stroke-width="{width:g}" stroke-linejoin="round" stroke-linecap="round"/>'


def edges() -> dict[str, str]:
    """Each part's cream edge, sitting EDGE units outside its outermost line.

    Built per part rather than derived from the ink, because each part's
    outermost line is a different thing: the outline pass (2 × OL wide) for
    body and arms, the fill for the legs, the thread for the loop, and a
    3-unit stroke on a *scaled* star for the sparkles, where the width must be
    divided by the scale or the small sparkle gets a thinner edge."""
    ol, g = M.OL, EDGE
    out = {}
    for side, (x, y) in SHOULDERS.items():
        out[f"arm-{side}"] = _stroke(f'<path d="M{x} {y} L{x} {y + ARM_L}"/>', ARM_W + 2 * (ol + g), None)
    for side, (x, _) in HIPS.items():
        out[f"leg-{side}"] = _stroke(f'<rect x="{x - 37}" y="752" width="74" height="112" rx="37"/>', 2 * g, CREAM)
    out["loop"] = _stroke(f'<path d="{LOOP_D}"/>', 11 + 2 * g, None)
    out["body"] = "".join(_stroke(M.draw(s), 2 * (ol + g), CREAM) for s in [M.TAG_BODY] + M.TAG_NECK)
    star = ""
    for x, y, s, rot in ((642, 214, 104, 8), (700, 150, 44, -6)):
        k = s / 100
        star += (f'<g transform="translate({x} {y}) rotate({rot}) scale({k:g}) translate(-50 -50)">'
                 + _stroke(f'<path d="{M.STAR}"/>', 3 + 2 * g / k, CREAM) + "</g>")
    out["twinkle"] = star
    return out


def arm(side: str) -> str:
    x, y = SHOULDERS[side]
    return M.capsule(x, y, x, y + ARM_L, ARM_W, M.WHITE)


def leg(side: str) -> str:
    x = HIPS[side][0] - 37
    return f'<rect x="{x}" y="752" width="74" height="112" rx="37" fill="{M.ESP}"/>'


LOOP_D = "M512 280 C 470 222, 446 150, 486 122 C 528 94, 572 158, 512 280"
THREAD = f'fill="none" stroke="{M.ESP}" stroke-width="11" stroke-linecap="round" stroke-linejoin="round"'


def figure(default_mood: str) -> str:
    M.uid = _ids()
    M.SHADOW = True
    parts = {
        "leg-l": leg("l"), "leg-r": leg("r"),
        "arm-l": arm("l"), "arm-r": arm("r"),
        "loop": f'<path d="{LOOP_D}" {THREAD}/>',
        "body": M.tag_body(),
        "twinkle": M.spark(642, 214, 104, rot=8) + M.spark(700, 150, 44, rot=-6),
    }
    nid = M.uid("n")
    neck = "".join(M.draw(x) for x in M.TAG_NECK)
    loop_front = (f'<defs><clipPath id="{nid}">{neck}</clipPath></defs>'
                  f'<path d="M512 280 C 470 222, 446 150, 486 122" {THREAD} clip-path="url(#{nid})"/>')

    def wrap(cls, inner):
        return f'<g class="tag-{cls}">{inner}</g>'

    e = edges()
    edge = "".join([
        wrap("leg-l", e["leg-l"]), wrap("leg-r", e["leg-r"]),
        wrap("arm-l", e["arm-l"]), wrap("arm-r", e["arm-r"]),
        e["loop"], e["body"], wrap("twinkle", e["twinkle"]),
    ])

    faces = ""
    for mood in MOODS:
        face = M.eyes(512, 566, 86, mood=mood) + M.mouth(512, 632, 40, mood=mood)
        on = " is-on" if mood == default_mood else ""
        faces += f'<g class="tag-f tag-f-{mood}{on}">{face}</g>'
    look = f'<g class="tag-look">{M.cheeks(512, 636, 158)}{faces}</g>'
    back = ('<g class="tag-back" transform="translate(1024 0) scale(-1 1)">'
            '<text class="tag-price" x="512" y="585" text-anchor="middle"></text>'
            '<text class="tag-label" y="652" text-anchor="middle"><tspan x="512"></tspan>'
            '<tspan x="512" dy="1.1em"></tspan></text></g>')
    ink = "".join([
        wrap("leg-l", parts["leg-l"]), wrap("leg-r", parts["leg-r"]),
        wrap("arm-l", parts["arm-l"]), wrap("arm-r", parts["arm-r"]),
        f'<g class="tag-loop">{parts["loop"]}</g>', wrap("body", parts["body"] + loop_front),
        look, back, wrap("twinkle", parts["twinkle"]),
    ])
    shadow = f'<ellipse class="tag-shadow" cx="512" cy="864" rx="190" ry="23" fill="{M.ESP}" opacity=".13"/>'
    return _num(
        f'<svg class="tag-svg" viewBox="{VIEWBOX}" aria-hidden="true" focusable="false">'
        f'{STYLE.replace(chr(10), "")}{shadow}<g class="tag-fig"><g class="tag-edge">{edge}</g><g class="tag-ink">{ink}</g></g></svg>')


# Shared by both pages, so it lives with the drawing. Every moving part reads
# its transform from a variable on the <svg>, which tag.js writes once per
# frame; the edge and ink copies of a part share a class, so both follow.
# Pivots: shoulders, hips, and 512,797 (the feet) for squash, lean and flip.
STYLE = """<style>
.tag-svg{overflow:visible}
.tag-svg g{transform-box:view-box}
.tag-fig{transform-origin:512px 797px;transform:translate(0,var(--hop,0px)) rotate(var(--lean,0deg)) scale(var(--sx,1),var(--sy,1))}
.tag-arm-l{transform-origin:306px 614px;transform:rotate(var(--al,24deg))}
.tag-arm-r{transform-origin:718px 614px;transform:rotate(var(--ar,-24deg))}
.tag-leg-l{transform-origin:457px 770px;transform:rotate(var(--ll,0deg))}
.tag-leg-r{transform-origin:567px 770px;transform:rotate(var(--lr,0deg))}
.tag-look{transform:translate(var(--lx,0px),var(--ly,0px))}
.tag-twinkle{transform-origin:660px 190px;transform:scale(var(--tw,1))}
.tag-shadow{transform-box:view-box;transform-origin:512px 864px;transform:scale(var(--sh,1),1)}
.tag-f{opacity:0}.tag-f.is-on{opacity:1}
.tag-back{opacity:0}.tag-svg.is-back .tag-back{opacity:1}.tag-svg.is-back .tag-look{opacity:0}
.tag-price{font:900 124px Fraunces,Georgia,serif;fill:#2B211C;letter-spacing:-3px}
.tag-label{font:700 60px 'DM Sans',system-ui,sans-serif;fill:#6E625B;letter-spacing:3px}
</style>"""


def block(page: str) -> str:
    mood = "wow" if page == "404.html" else "happy"
    return f"{BEGIN}\n{figure(mood)}\n{END}"


PAGES = ("index.html", "404.html")


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale = []
    for name in PAGES:
        path = ROOT / "website" / name
        text = path.read_text(encoding="utf-8")
        pattern = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)
        if not pattern.search(text):
            print(f"{name}: no {BEGIN} … {END} block", file=sys.stderr)
            return 1
        new = pattern.sub(lambda _: block(name), text, count=1)
        if new != text:
            stale.append(name)
            if not check:
                path.write_text(new, encoding="utf-8")
    if check and stale:
        print(f"Tag SVG is stale in {', '.join(stale)} — run website/seo/build_tag.py and commit.", file=sys.stderr)
        return 1
    print("Tag SVG " + ("up to date" if check else f"written ({', '.join(stale) or 'no changes'})"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
