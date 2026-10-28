#!/usr/bin/env python3
"""Render TikTok photo-mode slideshows (1080x1920) from the specs in
`slideshows/*.json`, using the site's self-hosted Fraunces and DM Sans.

A spec holds the structure and the copy. The numbers and photos come from a
real find, so they live outside git in `fill/<name>/`:

    fill/thrift-haul/values.json   {"PAID_1": "$4", "LOW_1": "$35", ...}
    fill/thrift-haul/item1.jpg     one file per photo slot, named by its slot

A slideshow with any value or photo missing renders to `out/<name>/DRAFT/`
with a red banner listing what is missing, so a half-filled one cannot be
posted by mistake. Complete ones render to `out/<name>/` with `caption.txt`.

    python3 marketing/tiktok/build_slideshows.py                 # every spec
    python3 marketing/tiktok/build_slideshows.py thrift-haul     # one spec
    python3 marketing/tiktok/build_slideshows.py --sheet         # + overview
    python3 marketing/tiktok/build_slideshows.py --chromium PATH # own browser

Needs Playwright's Chromium (as `marketing/mascot/` does) and, for --sheet,
Pillow.
"""
import argparse, base64, html, json, mimetypes, pathlib, re, shutil, sys

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
FONTS = HERE.parents[1] / "website" / "fonts"
SPECS, FILL, OUT = HERE / "slideshows", HERE / "fill", HERE / "out"
W, H = 1080, 1920
PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".webp")

DARK = "#1C1410"; DARK2 = "#2B211C"; CREAM = "#FAF7F4"; CARD = "#F3EDE8"
TERRA = "#D96C47"; SAGE = "#7A9E7E"; SAGE_T = "#4F7355"; MUTED = "#726860"
BORDER = "#E8DDD7"


def _b64(path): return base64.b64encode(path.read_bytes()).decode()


# TikTok draws its tabs over the top ~170px, the like/comment rail down the
# right edge and the caption over the bottom ~380px, so copy stays inside
# 190..1540 vertically and 90..930 horizontally.
CSS = f"""
@font-face{{font-family:'Fraunces';font-weight:100 900;src:url(data:font/woff2;base64,{_b64(FONTS / "fraunces-latin.woff2")}) format('woff2');}}
@font-face{{font-family:'Fraunces';font-weight:100 900;font-style:italic;src:url(data:font/woff2;base64,{_b64(FONTS / "fraunces-italic-latin.woff2")}) format('woff2');}}
@font-face{{font-family:'DM Sans';font-weight:100 1000;src:url(data:font/woff2;base64,{_b64(FONTS / "dmsans-latin.woff2")}) format('woff2');}}
*{{margin:0;padding:0;box-sizing:border-box;-webkit-font-smoothing:antialiased;}}
html,body{{width:{W}px;height:{H}px;overflow:hidden;}}
.slide{{width:{W}px;height:{H}px;position:relative;overflow:hidden;}}
.dark{{background:radial-gradient(120% 70% at 84% 6%,{DARK2} 0%,{DARK} 62%);color:#fff;}}
.cream{{background:{CREAM};color:{DARK};}}
.safe{{position:absolute;top:190px;left:90px;width:840px;height:1350px;display:flex;flex-direction:column;}}
.top{{display:flex;justify-content:space-between;align-items:center;font-family:'DM Sans';font-weight:700;font-size:24px;letter-spacing:6px;text-transform:uppercase;opacity:.55;margin-bottom:38px;}}
.pill{{align-self:flex-start;display:inline-flex;align-items:center;gap:14px;padding:14px 26px;border-radius:100px;font-family:'DM Sans';font-weight:700;font-size:28px;background:{TERRA}26;color:{TERRA};}}
.pill:before{{content:'';width:13px;height:13px;border-radius:50%;background:{TERRA};}}
h1{{font-family:'Fraunces';font-weight:600;letter-spacing:-2px;line-height:1.02;font-size:104px;margin-top:30px;}}
h2{{font-family:'Fraunces';font-weight:600;letter-spacing:-1.5px;line-height:1.05;font-size:74px;margin-top:26px;}}
.i{{font-style:italic;color:{TERRA};}}
.sub{{font-family:'DM Sans';font-size:40px;line-height:1.38;margin-top:30px;opacity:.82;}}
.spacer{{flex:1;}}
.photo{{border-radius:34px;background-size:cover;background-position:center;width:100%;}}
.slot{{border:5px dashed {TERRA};border-radius:34px;display:flex;align-items:center;justify-content:center;text-align:center;padding:40px;font-family:'DM Sans';font-weight:700;font-size:34px;color:{TERRA};background:{TERRA}10;width:100%;}}
.bleed{{position:absolute;inset:0;background-size:cover;background-position:center;}}
.bleed:after{{content:'';position:absolute;inset:0;background:linear-gradient(180deg,{DARK}D9 0%,{DARK}59 38%,{DARK}33 60%,{DARK}E6 100%);}}
.bleed.slot{{border-radius:0;border-width:0;align-items:flex-end;padding-bottom:520px;}}
.bleed.slot:after{{display:none;}}
.stats{{display:flex;gap:22px;margin-top:30px;}}
.stat{{flex:1;border-radius:28px;padding:28px 30px;}}
.dark .stat{{background:#ffffff10;border:1px solid #ffffff1f;}}
.cream .stat{{background:{CARD};border:1px solid {BORDER};}}
.stat .l{{font-family:'DM Sans';font-weight:700;font-size:25px;letter-spacing:3px;text-transform:uppercase;opacity:.6;}}
.stat .v{{font-family:'Fraunces';font-weight:600;font-size:66px;letter-spacing:-1px;margin-top:8px;}}
.stat.est .v{{color:{TERRA};}}
.chip{{align-self:flex-start;margin-top:22px;padding:12px 24px;border-radius:100px;font-family:'DM Sans';font-weight:700;font-size:27px;background:{SAGE}2E;color:{SAGE_T};}}
.dark .chip{{color:#B9D3BB;}}
.verdict{{font-family:'Fraunces';font-style:italic;font-weight:600;font-size:56px;margin-top:26px;color:{TERRA};}}
.num{{font-family:'Fraunces';font-style:italic;font-weight:600;font-size:150px;line-height:1;color:{TERRA};}}
.phone{{align-self:center;width:470px;border-radius:66px;padding:18px;background:#0d0a08;box-shadow:0 40px 100px rgba(0,0,0,.35);}}
.phone .photo,.phone .slot{{border-radius:50px;height:948px;}}
.cta{{display:flex;justify-content:space-between;align-items:center;background:{TERRA};border-radius:22px;padding:28px 34px;font-family:'DM Sans';font-weight:700;font-size:33px;color:#fff;margin-top:34px;}}
.cta b{{color:#3a1c0f;}}
.ph{{background:repeating-linear-gradient(45deg,#ffd54a,#ffd54a 10px,#ffe58a 10px,#ffe58a 20px);color:#3a1c0f;border-radius:10px;padding:0 10px;font-style:normal;}}
.draft{{position:absolute;left:0;right:0;top:0;padding:22px 40px;background:#C62828;color:#fff;font-family:'DM Sans';font-weight:700;font-size:28px;line-height:1.3;z-index:9;}}
"""

SLIDE_TYPES = ("hook", "item", "tip", "compare", "app")
PLACEHOLDER = re.compile(r"\{([A-Z0-9_]+)\}")
ITALIC = re.compile(r"\*([^*]+)\*")


class Show:
    def __init__(self, name):
        self.name = name
        self.spec = json.loads((SPECS / f"{name}.json").read_text())
        fill_dir = FILL / name
        values_file = fill_dir / "values.json"
        self.values = json.loads(values_file.read_text()) if values_file.exists() else {}
        self.fill_dir = fill_dir
        self.missing = []          # in first-seen order, no repeats

    def _miss(self, what):
        if what not in self.missing:
            self.missing.append(what)

    def text(self, raw, plain=False):
        """Escape, fill {KEYS} and turn *words* into the brand italic."""
        if raw is None:
            return ""
        def sub(m):
            key = m.group(1)
            val = self.values.get(key)
            if val in (None, ""):
                self._miss(key)
                return "{" + key + "}" if plain else f"<span class='ph'>{{{key}}}</span>"
            return str(val) if plain else html.escape(str(val))
        if plain:
            return PLACEHOLDER.sub(sub, raw)
        out = PLACEHOLDER.sub(sub, html.escape(raw))
        return ITALIC.sub(r"<span class='i'>\1</span>", out)

    def photo(self, slot, label, cls="photo", style=""):
        """A photo slot: the file `fill/<show>/<slot>.*`, or a dashed box naming it."""
        for ext in PHOTO_EXTS:
            p = self.fill_dir / f"{slot}{ext}"
            if p.exists():
                mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
                return (f"<div class='{cls}' style=\"{style}background-image:"
                        f"url(data:{mime};base64,{_b64(p)})\"></div>")
        self._miss(f"photo {slot}")
        return (f"<div class='{cls} slot' style='{style}'>PHOTO: {html.escape(label or slot)}"
                f"<br>fill/{self.name}/{slot}.jpg</div>")

    # ── slide types ──
    def hook(self, s):
        bg = self.photo(s["photo"], s.get("photo_label"), cls="bleed") if s.get("photo") else ""
        return bg, f"""
          {f"<span class='pill'>{self.text(s['kicker'])}</span>" if s.get('kicker') else ''}
          <h1>{self.text(s['headline'])}</h1>
          {f"<p class='sub'>{self.text(s['sub'])}</p>" if s.get('sub') else ''}
          <div class='spacer'></div>"""

    def item(self, s):
        return "", f"""
          {f"<span class='pill'>{self.text(s['kicker'])}</span>" if s.get('kicker') else ''}
          {self.photo(s['photo'], s.get('photo_label'), style='height:720px;margin-top:30px;')}
          <h2 style='font-size:62px'>{self.text(s['name'])}</h2>
          <div class='stats'>
            {f"<div class='stat'><div class='l'>Paid</div><div class='v'>{self.text(s['paid'])}</div></div>" if s.get('paid') else ''}
            <div class='stat est'><div class='l'>AI estimate</div><div class='v'>{self.text(s['estimate'])}</div></div>
          </div>
          {f"<span class='chip'>{self.text(s['confidence'])}</span>" if s.get('confidence') else ''}
          {f"<div class='verdict'>{self.text(s['verdict'])}</div>" if s.get('verdict') else ''}"""

    def tip(self, s):
        pic = self.photo(s["photo"], s.get("photo_label"), style="height:640px;margin-top:40px;") if s.get("photo") else ""
        # Without a photo the copy sits in the middle rather than under an empty half.
        lead = "" if pic else "<div class='spacer'></div>"
        return "", f"""{lead}
          <div class='num'>{self.text(s['number'])}</div>
          <h2>{self.text(s['headline'])}</h2>
          <p class='sub'>{self.text(s['body'])}</p>
          {pic}<div class='spacer'></div>"""

    def compare(self, s):
        stats = "".join(
            f"<div class='stat{' est' if i else ''}' style='margin-top:{22 if i else 0}px'>"
            f"<div class='l'>{self.text(a['label'])}</div>"
            f"<div class='v' style='font-size:110px'>{self.text(a['value'])}</div></div>"
            for i, a in enumerate(s["stats"]))
        return "", f"""<div class='spacer'></div>
          <h2>{self.text(s['headline'])}</h2>
          <div style='display:flex;flex-direction:column;margin-top:50px'>{stats}</div>
          {f"<p class='sub'>{self.text(s['note'])}</p>" if s.get('note') else ''}
          <div class='spacer'></div>"""

    def app(self, s):
        return "", f"""
          <h2 style='margin-top:0'>{self.text(s['headline'])}</h2>
          {f"<p class='sub' style='font-size:34px;margin-top:20px'>{self.text(s['sub'])}</p>" if s.get('sub') else ''}
          <div class='spacer'></div>
          <div class='phone'>{self.photo(s['screenshot'], s.get('screenshot_label'))}</div>
          <div class='cta'><span>Free on the App Store</span><b>link in bio →</b></div>"""

    def slides_html(self):
        slides, total = self.spec["slides"], len(self.spec["slides"])
        pages = []
        for n, s in enumerate(slides, 1):
            if s.get("type") not in SLIDE_TYPES:
                raise SystemExit(f"{self.name}: slide {n} has unknown type {s.get('type')!r}")
            bg, body = getattr(self, s["type"])(s)
            theme = s.get("theme", self.spec.get("theme", "dark"))
            pages.append(f"""<div class='slide {theme}'>{bg}<div class='safe'>
              <div class='top'><span>SnapWorth</span><span>{n}/{total}</span></div>{body}</div></div>""")
        return pages


def render(show, page, sheet):
    pages = show.slides_html()
    caption = show.text(show.spec.get("caption", ""), plain=True)
    tags = " ".join(show.spec.get("hashtags", []))
    dest = OUT / show.name
    shutil.rmtree(dest, ignore_errors=True)
    if show.missing:
        dest = dest / "DRAFT"
        banner = ("<div class='draft'>DRAFT, not for posting. Missing: "
                  + html.escape(", ".join(show.missing)) + "</div>")
    else:
        banner = ""
    dest.mkdir(parents=True, exist_ok=True)
    files = []
    for n, body in enumerate(pages, 1):
        body = body.replace("<div class='safe'>", banner + "<div class='safe'>", 1)
        page.set_content("<!doctype html><html><head><meta charset='utf-8'><style>" + CSS
                         + "</style></head><body>" + body + "</body></html>")
        page.evaluate("document.fonts.ready")
        f = dest / f"{n:02d}.png"
        page.screenshot(path=str(f), clip={"x": 0, "y": 0, "width": W, "height": H})
        files.append(f)
    (dest / "caption.txt").write_text(caption.strip() + ("\n\n" + tags if tags else "") + "\n")
    if sheet:
        from PIL import Image
        thumbs = [Image.open(f).convert("RGB").resize((360, 640)) for f in files]
        grid = Image.new("RGB", (len(thumbs) * 376 + 16, 672), "#d9d2cc")
        for i, t in enumerate(thumbs):
            grid.paste(t, (16 + i * 376, 16))
        grid.save(dest / "sheet.jpg", quality=88)
    state = "DRAFT (missing: " + ", ".join(show.missing) + ")" if show.missing else "ready"
    print(f"{show.name}: {len(files)} slides → {dest.relative_to(HERE)}  [{state}]")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("names", nargs="*", help="spec names (default: all)")
    ap.add_argument("--sheet", action="store_true", help="also write sheet.jpg, all slides side by side")
    ap.add_argument("--chromium", help="path to a Chromium binary, if Playwright's own is not installed")
    a = ap.parse_args()
    names = a.names or sorted(p.stem for p in SPECS.glob("*.json"))
    unknown = [n for n in names if not (SPECS / f"{n}.json").exists()]
    if unknown:
        sys.exit("no such spec: " + ", ".join(unknown))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=a.chromium, args=["--force-color-profile=srgb"])
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        for n in names:
            render(Show(n), page, a.sheet)
        browser.close()


if __name__ == "__main__":
    main()
