"""The app icons: Tag, tilted, inside camera-viewfinder corners. Primary icon since 1.7.0.

Writes straight into the app's asset catalog and owns what it writes, so change this script
rather than the PNGs or their Contents.json:

- `AppIcon` (primary): "Tag" in light mode, "Tag Night" as its dark appearance, and a
  grayscale tinted appearance on black, as iOS 18 asks for.
- `AppIcon-TagNight`, `AppIcon-TagWow`: alternate icons for the picker in Settings.
- `AppIconPreview/<Choice>.imageset` (a namespace): the picker's thumbnails at 60 pt (@2x,
  @3x). An .appiconset cannot be loaded as an image at run time, so the picker needs copies.

It also writes the website's `favicon-32.png`, `favicon-192.png` and `apple-touch-icon.png`
from the primary icon, so the site and the Home Screen show the same mark.

Every icon PNG is 1024 × 1024 opaque RGB; App Store Connect rejects an alpha channel.

`AppIcon-Classic` (the icon before 1.7.0) is not drawn here: it is the old PNGs, kept as an
alternate. Only its preview is derived from it. The alternate names must match
`ASSETCATALOG_COMPILER_ALTERNATE_APPICON_NAMES` and `AppIconChoice.iconName`.

    python3 export_app_icons.py      # needs Playwright's Chromium and Pillow
    PLAYWRIGHT_CHROMIUM=/path/to/chrome python3 export_app_icons.py
"""
import io, json, os, pathlib, shutil
from PIL import Image
from playwright.sync_api import sync_playwright
import mascots as M
from mascots import CREAM, TERRA, WHITE, BORDER, eyes, cheeks, mouth, spark, tag_body

HERE = pathlib.Path(__file__).resolve().parent
CATALOG = HERE.parent.parent / "ios" / "SnapWorth" / "Assets.xcassets"
PREVIEW_PT = 60


def grad(id_, top, bottom):
    return (f'<defs><linearGradient id="{id_}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{top}"/>'
            f'<stop offset="1" stop-color="{bottom}"/></linearGradient></defs><rect width="1024" height="1024" fill="url(#{id_})"/>')


TERRA_BG = grad("bgT", "#E2704A", "#B34D2E")   # the classic icon's ground
NIGHT_BG = grad("bgN", "#3A2D26", "#1E1714")   # espresso
BLACK_BG = '<rect width="1024" height="1024" fill="#000"/>'


def corners(color, inset=120, arm=150, width=42):
    """Viewfinder corners, inset clear of the icon mask's rounded corners."""
    out = ""
    for x, y, sx, sy in ((inset, inset, 1, 1), (1024 - inset, inset, -1, 1),
                         (inset, 1024 - inset, 1, -1), (1024 - inset, 1024 - inset, -1, -1)):
        out += (f'<path d="M{x} {y + sy * arm} L{x} {y} L{x + sx * arm} {y}" fill="none" stroke="{color}" '
                f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"/>')
    return out


def tilted_tag(rot, mood="happy", body=WHITE, shade=BORDER, scale=1.12):
    """Tag's body and face only (arms and legs are mush at icon size), tilted."""
    face = (eyes(512, 566, 92, rx=34, ry=44, mood=mood) + cheeks(512, 650, 160, r=34, op=.7)
            + mouth(512, 646, 50, mood=mood))
    return (f'<g transform="translate(512 560) rotate({rot}) scale({scale}) translate(-512 -575)">'
            f'{tag_body(fill=body, shade=shade)}{face}{spark(655, 420, 120, rot=8)}</g>')


def tag():
    return TERRA_BG + corners(CREAM) + tilted_tag(-12)


def tag_night():
    return NIGHT_BG + corners(TERRA) + tilted_tag(-12)


def tag_tinted():
    # iOS tints by luminance: black is the ground, the brightest parts take the full tint.
    return BLACK_BG + corners(WHITE) + tilted_tag(-12, body="#8A8A8A", shade="#777777")


def tag_wow():
    return TERRA_BG + corners(CREAM) + tilted_tag(10, mood="wow")


def render(pg, body, px, gray=False):
    pg.set_viewport_size({"width": px, "height": px})
    pg.set_content(f'<body style="margin:0"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" '
                   f'width="{px}" height="{px}" style="display:block">{body}</svg></body>')
    im = Image.open(io.BytesIO(pg.screenshot(clip={"x": 0, "y": 0, "width": px, "height": px})))
    return im.convert("L").convert("RGB") if gray else im.convert("RGB")


def write_json(path, obj):
    path.write_text(json.dumps(obj, indent=2) + "\n")


def appiconset(name, images):
    """`images`: {appearance or None: Image}. Appearance is "dark" or "tinted"."""
    folder = CATALOG / f"{name}.appiconset"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    entries = []
    for appearance, im in images.items():
        fn = f"{name}{'-' + appearance.capitalize() if appearance else ''}.png"
        im.save(folder / fn, optimize=True)
        entry = {"filename": fn, "idiom": "universal", "platform": "ios", "size": "1024x1024"}
        if appearance:
            entry = {"appearances": [{"appearance": "luminosity", "value": appearance}], **entry}
        entries.append(entry)
    write_json(folder / "Contents.json", {"images": entries, "info": {"author": "xcode", "version": 1}})


def preview_set(root, stem, im):
    folder = root / f"{stem}.imageset"
    folder.mkdir(parents=True)
    imgs = [{"idiom": "universal", "scale": "1x"}]
    for scale in (2, 3):
        fn = f"{stem}@{scale}x.png"
        im.resize((PREVIEW_PT * scale,) * 2, Image.LANCZOS).save(folder / fn, optimize=True)
        imgs.append({"filename": fn, "idiom": "universal", "scale": f"{scale}x"})
    write_json(folder / "Contents.json", {"images": imgs, "info": {"author": "xcode", "version": 1}})


if __name__ == "__main__":
    M.SHADOW = False
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM") or None,
                              args=["--force-color-profile=srgb"])
        pg = b.new_page(device_scale_factor=1)
        icons = {"Tag": render(pg, tag(), 1024), "TagNight": render(pg, tag_night(), 1024),
                 "TagWow": render(pg, tag_wow(), 1024)}
        tinted = render(pg, tag_tinted(), 1024, gray=True)
        b.close()

    appiconset("AppIcon", {None: icons["Tag"], "dark": icons["TagNight"], "tinted": tinted})
    appiconset("AppIcon-TagNight", {None: icons["TagNight"]})
    appiconset("AppIcon-TagWow", {None: icons["TagWow"]})

    previews = CATALOG / "AppIconPreview"
    if previews.exists():
        shutil.rmtree(previews)
    previews.mkdir()
    # Namespaced, so the images are "AppIconPreview/Tag" and cannot clash with the mascot's.
    write_json(previews / "Contents.json", {"info": {"author": "xcode", "version": 1},
                                            "properties": {"provides-namespace": True}})
    for stem, im in icons.items():
        preview_set(previews, stem, im)
    preview_set(previews, "Classic",
                Image.open(CATALOG / "AppIcon-Classic.appiconset" / "AppIcon-Classic.png").convert("RGB"))
    site = HERE.parent.parent / "website"
    for fn, px in (("favicon-32.png", 32), ("favicon-192.png", 192), ("apple-touch-icon.png", 180)):
        icons["Tag"].resize((px, px), Image.LANCZOS).save(site / fn, optimize=True)
    print("wrote AppIcon (any, dark, tinted), AppIcon-TagNight, AppIcon-TagWow, 4 previews and the site's icons")
