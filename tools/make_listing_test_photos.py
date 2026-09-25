#!/usr/bin/env python3
"""Generate the ten synthetic photos `ListingPhotoCleanupTests` runs on (#91).

Synthetic on purpose: licence-free, reproducible, and small. They are not a
test of Vision's segmentation quality — Vision's foreground mask cannot run in
the simulator at all ("Could not create inference context"), so CI can only
test what happens around it: orientation, cropping, canvas shape, backdrop,
and the fallback. Each photo exists for one of those.

    python3 tools/make_listing_test_photos.py   # needs Pillow

Output: ios/SnapWorthTests/ListingPhotos/*.jpg (a folder reference in the test
target, so adding a photo here needs no project change).
"""
import pathlib
import random

from PIL import Image, ImageDraw, ImageFilter

OUT = pathlib.Path(__file__).resolve().parent.parent / "ios" / "SnapWorthTests" / "ListingPhotos"
rng = random.Random(91)


def clutter(size, base=(120, 110, 100)):
    """A busy 'shop' background: blocks and stripes in muddy colours."""
    im = Image.new("RGB", size, base)
    d = ImageDraw.Draw(im)
    w, h = size
    for _ in range(60):
        x, y = rng.randrange(w), rng.randrange(h)
        c = tuple(max(0, min(255, v + rng.randint(-50, 50))) for v in base)
        d.rectangle([x, y, x + rng.randint(10, w // 4), y + rng.randint(10, h // 4)], fill=c)
    for x in range(0, w, 37):
        d.line([x, 0, x - h // 3, h], fill=(90, 85, 80), width=3)
    return im


def item(im, box, colour, shape="rounded"):
    d = ImageDraw.Draw(im)
    if shape == "ellipse":
        d.ellipse(box, fill=colour, outline=(30, 25, 20), width=4)
    else:
        d.rounded_rectangle(box, radius=40, fill=colour, outline=(30, 25, 20), width=4)
    return im


def save(im, name, **kw):
    im.save(OUT / name, quality=85, **kw)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.jpg"):
        old.unlink()

    # 01 landscape, the common case: a jumper-sized block on a rack.
    save(item(clutter((1024, 768)), [300, 180, 720, 620], (170, 40, 40)), "01-landscape.jpg")
    # 02 portrait.
    save(item(clutter((768, 1024)), [180, 260, 590, 820], (40, 70, 160), "ellipse"), "02-portrait.jpg")
    # 03 square, soft gradient ground.
    g = Image.linear_gradient("L").resize((1024, 1024)).convert("RGB")
    save(item(g, [260, 330, 780, 700], (60, 130, 60)), "03-square.jpg")
    # 04 tiny: a thumbnail-sized source must still export at full canvas size.
    save(item(clutter((200, 150)), [60, 30, 140, 120], (200, 160, 30)), "04-tiny.jpg")
    # 05 panorama: extreme width.
    save(item(clutter((1024, 300)), [420, 40, 600, 260], (150, 60, 150)), "05-panorama.jpg")
    # 06 very tall.
    save(item(clutter((300, 1024)), [60, 300, 240, 760], (30, 150, 150), "ellipse"), "06-tall.jpg")
    # 07 EXIF-rotated: pixels stored sideways, orientation tag 6 (display
    # rotated 90° clockwise). UIImage reports `.right`; `cgImage` ignores it.
    rotated = item(clutter((1024, 768)), [300, 200, 720, 560], (220, 120, 40))
    exif = Image.Exif()
    exif[0x0112] = 6
    save(rotated, "07-exif-rotated.jpg", exif=exif)
    # 08 greyscale source.
    save(item(clutter((900, 900)), [250, 250, 650, 650], (200, 200, 200)).convert("L").convert("RGB"),
         "08-greyscale.jpg")
    # 09 a plain wall, nothing on it: the photo a real mask would find empty.
    save(Image.new("RGB", (1024, 768), (205, 200, 192)).filter(ImageFilter.GaussianBlur(2)), "09-no-item.jpg")
    # 10 item cut off by the frame edge.
    save(item(clutter((1024, 768)), [600, 150, 1100, 700], (40, 40, 40)), "10-item-at-edge.jpg")

    print(f"wrote {len(list(OUT.glob('*.jpg')))} photos to {OUT}")


if __name__ == "__main__":
    main()
