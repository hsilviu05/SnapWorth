"""Does a gold record's photo show what its label says? (#359, step 1)

A mislabelled gold record skews every baseline built on it, and a reviewer
approving a draft reads the label far more closely than the photo beside it.
So before records are approved, this asks TypeSafe's Jev two yes/no questions
about each one's primary photo, with the label beside it:

* `shows_label`: does the photo show the labelled brand and model?
* `one_item`: is there exactly one sellable item in it?

The output is a short list of records to look at by hand, least likely first.
It is not a gate and changes no record.

    cd backend
    python -m eval.photo_check                     # records not yet approved
    python -m eval.photo_check --all --out photo-check.json

Needs TYPESAFE_API_KEY, exported or in `backend/.env`. Image questions need a
vision-language model; Jev's arrived in v1.14 (2026-10-14) as experimental and
opt-in, so this defaults to `jev-preview` rather than `jev-latest`. Set
`--model` to whichever image model `GET /v1/models` lists for your key.

What is sent: the operator's own sale photos, which `eval.intake` placed in
`eval/data/images/`, and their labels. Never a user's scan. Each JPEG's
metadata segments (EXIF, with any location the camera wrote) are dropped
before it leaves; the pixels are unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import promptsafety  # noqa: E402
from eval import identity, schema  # noqa: E402
from eval.intake import DEFAULT_GOLD, JPEG_MAGIC  # noqa: E402
import typesafe  # noqa: E402
from typesafe import API_KEY_ENV, noul  # noqa: E402

log = logging.getLogger("snapworth.eval.photo_check")

DEFAULT_MODEL = os.environ.get("TYPESAFE_IMAGE_MODEL", "jev-preview")
#: Below this, the answer is "no" and the record goes on the list.
FLAG_BELOW = 0.5
#: Larger than any photo the intake writes (1568 px at quality 80), so a file
#: this big is not one of its photos.
MAX_PHOTO_BYTES = 8 * 1024 * 1024
#: Per phase. A photo makes the upload far larger than a text question's.
TIMEOUT_S = 60.0

SHOWS_LABEL = "shows_label"
ONE_ITEM = "one_item"

_SHOWS_LABEL = {
    "type": "noul",
    "instructions": "Does the photo show the labelled item? A different colourway, size "
                    "or condition does not make it a different item. A different brand, "
                    "model, model number or generation does.",
    "criteria": {
        "true": "The photo shows the labelled brand and model.",
        "false": "The photo shows a different item, a different model of it, or nothing "
                 "that can be identified as it.",
    },
}
_ONE_ITEM = {
    "type": "noul",
    "instructions": "Is there exactly one item for sale in the photo?",
    "criteria": {
        "true": "One item, perhaps with its own box, tags or accessories.",
        "false": "Several separate items, or no item at all.",
    },
}


@dataclass(frozen=True)
class Target:
    """One record's primary photo and the label it is checked against."""
    id: str
    photo: Path
    label: dict[str, str]           # category, brand, model, variant: those set
    review_state: str


def strip_jpeg_metadata(data: bytes) -> bytes:
    """`data` without its APPn (EXIF, XMP, …) and COM segments, or unchanged
    when it is not a JPEG this can walk. Everything from start-of-scan on is
    kept byte for byte, so the decoded image is identical. APP0 (JFIF) and
    APP14 (Adobe) say how to read the colours, so they stay."""
    if not data.startswith(JPEG_MAGIC):
        return data
    out = bytearray(data[:2])
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return data
        marker = data[i + 1]
        if marker == 0xDA:                      # start of scan: the rest is image data
            return bytes(out + data[i:])
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            out += data[i:i + 2]
            i += 2
            continue
        length = int.from_bytes(data[i + 2:i + 4], "big")
        if length < 2 or i + 2 + length > len(data):
            return data
        is_metadata = (0xE1 <= marker <= 0xEF and marker != 0xEE) or marker == 0xFE
        if not is_metadata:
            out += data[i:i + 2 + length]
        i += 2 + length
    return data


def _fenced(value: object, limit: int) -> str:
    return promptsafety.fence(promptsafety.sanitize_text(value, limit))


def targets(items: list[schema.GoldItem], root: Path,
            include_approved: bool) -> tuple[list[Target], list[str]]:
    """The records to check, and a line per record skipped and why."""
    found: list[Target] = []
    skipped: list[str] = []
    for item in items:
        if item.review_state.usable and not include_approved:
            continue
        if item.difficulty is schema.Difficulty.NEGATIVE_CONTROL:
            skipped.append(f"{item.id}: negative control, nothing sellable to check")
            continue
        if not (item.brand or item.model):
            skipped.append(f"{item.id}: no brand or model to check the photo against")
            continue
        image = item.primary_image
        if image is None:
            skipped.append(f"{item.id}: no photo")
            continue
        photo = (root / image.path).resolve()
        if not photo.is_file():
            skipped.append(f"{item.id}: {image.path} not found (is the photo store mounted?)")
            continue
        label = {key: value for key, value in (
            ("category", item.category), ("brand", item.brand),
            ("model", item.model), ("variant", item.variant)) if value}
        found.append(Target(item.id, photo, label, item.review_state.value))
    return found, skipped


_LIMITS = {"category": promptsafety.MAX_CATEGORY, "brand": promptsafety.MAX_BRAND,
           "model": promptsafety.MAX_ITEM_NAME, "variant": promptsafety.MAX_ITEM_NAME}


def build_request(label: dict[str, str], jpeg: bytes, model: str) -> dict[str, Any]:
    """One `/v1/systemone` body. The photo travels as an asset, and its
    placeholder stands where it belongs in `state` (the SDK's own
    `<image:hex>` form). Labels are typed by a person, but fenced as every
    other Jev call fences text, so none can read as an instruction."""
    placeholder = f"<image:{uuid.uuid4().hex}>"
    fenced = {key: _fenced(value, _LIMITS[key]) for key, value in label.items()}
    return {
        "model": model,
        "state": {"photo": placeholder, "label": fenced},
        "questions": {SHOWS_LABEL: _SHOWS_LABEL, ONE_ITEM: _ONE_ITEM},
        "assets": {placeholder: {"type": "image", "format": "jpg",
                                 "data_base64": base64.b64encode(jpeg).decode("ascii")}},
    }


def parse(body: object) -> dict[str, float | None]:
    answers = body.get("answers") if isinstance(body, dict) else None
    return {name: noul(answers, name) for name in (SHOWS_LABEL, ONE_ITEM)}


def summarise(rows: list[dict[str, Any]], skipped: list[str]) -> dict[str, Any]:
    """Counts, and the records to look at: any answer below `FLAG_BELOW`,
    least likely first. A record with no answers is unjudged, never clean. Pure."""
    def answered(row: dict[str, Any]) -> bool:
        return any(p is not None for p in row["answers"].values())

    judged = [r for r in rows if answered(r)]

    def lowest(row: dict[str, Any]) -> float:
        return min((p for p in row["answers"].values() if p is not None), default=1.0)

    look = sorted((r for r in judged if lowest(r) < FLAG_BELOW), key=lowest)
    return {
        "checked": len(rows),
        "judged": len(judged),
        "unjudged": [r["id"] for r in rows if not answered(r)],
        "skipped": skipped,
        "look_at": look,
        "models": sorted({r["model"] for r in judged if r.get("model")}),
    }


async def check(found: list[Target], model: str, concurrency: int) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(max(1, concurrency))
    # A photo makes the request far larger than a text question.
    client = typesafe.make_client(TIMEOUT_S, max_connections=max(1, concurrency))

    async def one(target: Target) -> dict[str, Any]:
        row: dict[str, Any] = {"id": target.id, "review_state": target.review_state,
                               "label": target.label}
        data = target.photo.read_bytes()
        if not data.startswith(JPEG_MAGIC) or len(data) > MAX_PHOTO_BYTES:
            log.warning("%s: %s is not a photo the intake wrote; not sent",
                        target.id, target.photo.name)
            return row | {"answers": {SHOWS_LABEL: None, ONE_ITEM: None}}
        body = build_request(target.label, strip_jpeg_metadata(data), model)
        async with semaphore:
            reply = await typesafe.post(client, body)
        answered = reply.get("model") if isinstance(reply, dict) else None
        return row | {"answers": parse(reply), "model": answered}

    try:
        return list(await asyncio.gather(*(one(t) for t in found)))
    finally:
        await client.aclose()


def _format(report: dict[str, Any]) -> str:
    lines = ["", "═══ Gold photo check ═══",
             f"{report['checked']} records checked, {report['judged']} answered"
             + (f" by {', '.join(report['models'])}" if report["models"] else "")]
    if report["checked"] and not report["judged"]:
        lines.append("Nothing was answered. Check the warnings above; an HTTP 4xx usually "
                     "means --model is not an image model your key can use.")
    if report["unjudged"]:
        lines.append(f"Unanswered: {', '.join(report['unjudged'])}")
    if report["skipped"]:
        lines += ["", "Skipped"] + [f"  {line}" for line in report["skipped"]]
    lines += ["", "Look at these by hand" if report["look_at"] else "Nothing to look at."]
    for row in report["look_at"]:
        answers = ", ".join(f"{name}={p:.2f}" for name, p in row["answers"].items()
                            if p is not None)
        label = " ".join(row["label"].get(k, "") for k in ("brand", "model", "variant")).strip()
        lines.append(f"  {row['id']} [{row['review_state']}] {label}  ({answers})")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check gold photos against their labels")
    parser.add_argument("--gold", default=str(DEFAULT_GOLD), help="default eval/data/gold.jsonl")
    parser.add_argument("--all", action="store_true",
                        help="approved records too (default: only those not yet approved)")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"an image model from GET /v1/models (default {DEFAULT_MODEL})")
    parser.add_argument("--limit", type=int, help="check only the first N records")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--out", help="write every answer and the summary here")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    identity.load_env()
    if not typesafe.enabled():
        print(f"needs {API_KEY_ENV}, exported or in backend/.env")
        return 2
    gold = Path(args.gold)
    if not gold.is_file():
        print(f"no gold set at {gold}: add sales with `python -m eval.intake` first")
        return 2

    found, skipped = targets(schema.load_gold(gold), gold.parent, args.all)
    if args.limit:
        found = found[: args.limit]
    rows = asyncio.run(check(found, args.model, args.concurrency)) if found else []
    report = summarise(rows, skipped)
    print(_format(report))
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": report, "records": rows},
                                             indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
