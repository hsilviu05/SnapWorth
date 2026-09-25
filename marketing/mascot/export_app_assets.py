"""Tag as Xcode imagesets: Assets.xcassets/Mascot/Tag{Happy,Joy,Wow,Blink}.imageset.

Any appearance: the espresso-outlined drawing. Dark appearance: the same drawing with a cream
die-cut edge, because an espresso outline disappears on the dark ground (#17120F) and on the
analyzing overlay's charcoal scrim. Both share one canvas so switching appearance never shifts
the image. No ground shadow is baked in: the SwiftUI view draws its own, so it can stay on the
floor while Tag bobs.

    python3 export_app_assets.py   # → app_assets/Mascot/…, app_assets/TagMascot-assets.zip
"""
import io, json, pathlib, shutil, zipfile
from PIL import Image
from playwright.sync_api import sync_playwright
import mascots as M

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "app_assets"
BASE_PT = 160                       # design height in points; @2x 320 px, @3x 480 px
EDGE = 18                           # sticker edge, 1024-space units
MOODS = {"Happy": "happy", "Joy": "joy", "Wow": "wow", "Blink": "blink"}
# The edge is a *round* dilation: blur the alpha, then keep everything above a
# low threshold. `feMorphology dilate` was used first and grows shapes with a
# square kernel, which is invisible on the rounded body but turns every sharp
# point (the sparkles) into a stepped block. For a straight edge, blur σ and
# threshold t push the boundary out by σ·Φ⁻¹(1 − t); t = 0.05 gives 1.645σ, so
# σ = EDGE / 1.645 keeps the edge as wide as before. The slope only sets how
# soft the new boundary is (about a pixel and a half at @3x).
EDGE_T = 0.05
EDGE_SIGMA = EDGE / 1.645
EDGE_SLOPE = 40
STICKER = (f'<defs><filter id="die" x="-20%" y="-20%" width="140%" height="140%" color-interpolation-filters="sRGB">'
           f'<feGaussianBlur in="SourceAlpha" stdDeviation="{EDGE_SIGMA:.3f}" result="b"/>'
           f'<feComponentTransfer in="b" result="d"><feFuncA type="linear" slope="{EDGE_SLOPE}" '
           f'intercept="{0.5 - EDGE_SLOPE * EDGE_T:.3f}"/></feComponentTransfer>'
           f'<feFlood flood-color="{M.CREAM}"/><feComposite in2="d" operator="in" result="edge"/>'
           f'<feMerge><feMergeNode in="edge"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>')


def drawings():
    M.SHADOW = False
    out = {}
    for name, mood in MOODS.items():
        inner = M.tag(mood=mood)
        out[name] = {"light": inner, "dark": f'{STICKER}<g filter="url(#die)">{inner}</g>'}
    return out


def alpha_bbox(pg, body):
    pg.set_viewport_size({"width": 1024, "height": 1024})
    pg.set_content(f'<body style="margin:0"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" '
                   f'width="1024" height="1024">{body}</svg></body>')
    png = pg.screenshot(omit_background=True, clip={"x": 0, "y": 0, "width": 1024, "height": 1024})
    return Image.open(io.BytesIO(png)).getchannel("A").point(lambda a: 255 if a > 4 else 0).getbbox()


def render(pg, body, vb, height_px):
    w_px = round(height_px * vb[2] / vb[3])
    pg.set_viewport_size({"width": w_px, "height": height_px})
    pg.set_content(f'<body style="margin:0;background:transparent"><svg xmlns="http://www.w3.org/2000/svg" '
                   f'viewBox="{" ".join(map(str, vb))}" width="{w_px}" height="{height_px}" style="display:block">{body}</svg></body>')
    png = pg.screenshot(omit_background=True, clip={"x": 0, "y": 0, "width": w_px, "height": height_px})
    return Image.open(io.BytesIO(png)).convert("RGBA")


def contents(stem):
    dark = [{"appearance": "luminosity", "value": "dark"}]
    imgs = [{"idiom": "universal", "scale": "1x"},
            {"filename": f"{stem}@2x.png", "idiom": "universal", "scale": "2x"},
            {"filename": f"{stem}@3x.png", "idiom": "universal", "scale": "3x"},
            {"appearances": dark, "idiom": "universal", "scale": "1x"},
            {"appearances": dark, "filename": f"{stem}-dark@2x.png", "idiom": "universal", "scale": "2x"},
            {"appearances": dark, "filename": f"{stem}-dark@3x.png", "idiom": "universal", "scale": "3x"}]
    return {"images": imgs, "info": {"author": "xcode", "version": 1}}


if __name__ == "__main__":
    if OUT.exists():
        shutil.rmtree(OUT)
    folder = OUT / "Mascot"
    folder.mkdir(parents=True)
    (folder / "Contents.json").write_text(json.dumps({"info": {"author": "xcode", "version": 1}}, indent=2) + "\n")
    d = drawings()
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--force-color-profile=srgb"])
        pg = b.new_page(device_scale_factor=1)
        # The canvas is the drawing plus the edge width plus a margin, measured
        # on the light drawings. Measuring the rendered dark edge instead made
        # the canvas depend on how the edge is drawn: the round edge reaches
        # less far past the sparkle tips than the square one did, which moved
        # the viewBox and would have changed TagMascot.aspectRatio.
        boxes = [alpha_bbox(pg, moods["light"]) for moods in d.values()]
        pad = EDGE + 6
        x0, y0 = min(bx[0] for bx in boxes) - pad, min(bx[1] for bx in boxes) - pad
        x1, y1 = max(bx[2] for bx in boxes) + pad, max(bx[3] for bx in boxes) + pad
        vb = [x0, y0, x1 - x0, y1 - y0]
        report = {"viewBox": vb, "points": [round(BASE_PT * vb[2] / vb[3], 1), BASE_PT], "images": {}}
        for name, variants in d.items():
            stem = f"Tag{name}"
            iset = folder / f"{stem}.imageset"
            iset.mkdir()
            (iset / "Contents.json").write_text(json.dumps(contents(stem), indent=2) + "\n")
            for app, body in variants.items():
                for scale in (2, 3):
                    im = render(pg, body, vb, BASE_PT * scale)
                    fn = f"{stem}{'-dark' if app == 'dark' else ''}@{scale}x.png"
                    im.save(iset / fn, optimize=True)
                    report["images"][fn] = list(im.size)
        b.close()
    (OUT / "report.json").write_text(json.dumps(report, indent=1))
    src = OUT / "source"
    src.mkdir()
    for f in ("mascots.py", "export_app_assets.py"):
        shutil.copy(HERE / f, src / f)
    with zipfile.ZipFile(OUT / "TagMascot-assets.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(folder.rglob("*")):
            z.write(f, f.relative_to(OUT))
        for f in sorted(src.iterdir()):
            z.write(f, f"source/{f.name}")
    print(json.dumps(report, indent=1))
