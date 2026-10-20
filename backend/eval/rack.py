"""Rack scan's manual test set (#89): run it, look at it, score it.

The acceptance bar is a judgement only a person can make — "a sensible
candidate", "a candidate that is not in the photo" — so this does not grade
anything. It runs the shipping prompt and `rack.validate` over a folder of
rack photos, draws each pin on a copy of its photo, and then scores a review
file a person fills in from those copies.

    python -m eval.rack run --photos eval/data/rack/images --out eval/data/rack/runs/<date>
    # open <out>/*.jpg, fill eval/data/rack/review.jsonl (see the README)
    python -m eval.rack score eval/data/rack/review.jsonl

`run` needs GEMINI_API_KEY and costs one model call a photo. `score` and
everything it reads are offline, which is what the tests exercise.

The bar, from the issue: of 20 photos, at least 15 return at least one
sensible candidate, and none returns a candidate that is not in the photo.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from scan import prompts
from scan import rack

#: The issue's bar.
MIN_PHOTOS = 20
MIN_WITH_SENSIBLE = 15
MAX_NOT_IN_PHOTO = 0

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".webp"}


@dataclass
class Review:
    photo: str
    sensible: int
    not_in_photo: int


def load_review(path: Path) -> list[Review]:
    """One JSON object a line: `photo`, `sensible`, `not_in_photo` (counts of
    the photo's pins). Blank lines and `#` comments are skipped."""
    reviews: list[Review] = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        row = json.loads(line)
        try:
            reviews.append(Review(photo=str(row["photo"]), sensible=int(row["sensible"]),
                                  not_in_photo=int(row["not_in_photo"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{path}:{n}: {exc}") from None
    return reviews


def score(reviews: list[Review]) -> dict:
    """The bar's three numbers and whether it is met."""
    with_sensible = sum(1 for r in reviews if r.sensible > 0)
    not_in_photo = sum(r.not_in_photo for r in reviews)
    return {
        "photos": len(reviews),
        "with_sensible": with_sensible,
        "not_in_photo": not_in_photo,
        "passed": (len(reviews) >= MIN_PHOTOS and with_sensible >= MIN_WITH_SENSIBLE
                   and not_in_photo <= MAX_NOT_IN_PHOTO),
    }


def _draw(photo: Path, candidates: list[dict], out: Path) -> None:
    """The photo with each candidate's box and rank, as the app pins them."""
    from PIL import Image, ImageDraw, ImageOps

    with Image.open(photo) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    draw = ImageDraw.Draw(image)
    w, h = image.size
    stroke = max(3, w // 200)
    for c in candidates:
        b = c["box"]
        rect = (b["x"] * w, b["y"] * h, (b["x"] + b["width"]) * w, (b["y"] + b["height"]) * h)
        draw.rectangle(rect, outline=(214, 90, 49), width=stroke)
        draw.text((rect[0] + stroke * 2, rect[1] + stroke * 2), str(c["rank"]),
                  fill=(255, 255, 255), stroke_width=stroke, stroke_fill=(214, 90, 49))
    image.save(out, quality=85)


async def _run(photos: list[Path], out: Path) -> list[dict]:
    from scan import aiconfig
    from main import _extract_json

    model = aiconfig.build_model()
    results: list[dict] = []
    for photo in photos:
        data = photo.read_bytes()
        mime = "image/png" if photo.suffix.lower() == ".png" else "image/jpeg"
        started = time.monotonic()
        row: dict = {"photo": photo.name, "prompt_version": prompts.RACK_PROMPT_VERSION}
        try:
            response = await model.generate_content_async(
                [prompts.RACK_PROMPT_V1,
                 {"mime_type": mime, "data": base64.standard_b64encode(data).decode()}])
            row["candidates"] = rack.validate(_extract_json(aiconfig.extract_text(response)))
            _draw(photo, row["candidates"], out / f"{photo.stem}.jpg")
        except Exception as exc:  # noqa: BLE001 — one bad photo must not end the run
            row["error"] = str(exc)[:200]
        row["latency_ms"] = round((time.monotonic() - started) * 1000)
        results.append(row)
        print(f"{photo.name}: {len(row.get('candidates', []))} candidate(s)"
              + (f" — {row['error']}" if "error" in row else ""))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.rack")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the prompt over a folder of rack photos")
    run.add_argument("--photos", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    sc = sub.add_parser("score", help="score a filled-in review file")
    sc.add_argument("review", type=Path)
    args = parser.parse_args(argv)

    if args.command == "score":
        result = score(load_review(args.review))
        print(json.dumps(result, indent=2))
        return 0 if result["passed"] else 1

    photos = sorted(p for p in args.photos.iterdir() if p.suffix.lower() in _IMAGE_SUFFIXES)
    if not photos:
        print(f"no photos in {args.photos}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    results = asyncio.run(_run(photos, args.out))
    (args.out / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"wrote {args.out / 'results.json'} and one marked-up copy a photo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
