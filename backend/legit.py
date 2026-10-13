"""Legit check (#90): what a model's read of 2-4 close-ups may say.

The endpoint is `main.legit`; the prompt is `prompts.LEGIT_PROMPT_V1`. This
module is the part between them that decides what reaches the app, and it is
deliberately strict, because the read sits next to a brand name and a price
and a person may act on it.

* **The verdict is a closed set.** Four values, and anything else the model
  writes — a synonym, a sentence, a different case — becomes "cannot_tell".
  The app words each value itself, in five languages, and analytics records
  it; free text in that field would reach both.

* **Reasons are observations, never certificates.** The prompt forbids
  concluding that an item is authentic or fake. `_CERTIFYING` drops a reason
  that does so anyway rather than trusting the instruction alone; it is a
  backstop for the wording rule, not a filter the prompt relies on.

* **No reasons, no verdict.** A verdict the model cannot ground in a single
  observation is served as "cannot_tell". That includes "consistent" — the
  read most likely to be taken as a pass — whose whole value is in naming
  what was checked.
"""

from __future__ import annotations

import re

import promptsafety
import prompts

#: The four verdicts, in order from best read to worst, then the honest
#: "could not judge". The app has a case and a translation for each, and
#: `legit_check_completed` reports only these.
CONSISTENT = "consistent"
SOME_CONCERNS = "some_concerns"
LIKELY_REPLICA = "likely_replica"
CANNOT_TELL = "cannot_tell"
VERDICTS = (CONSISTENT, SOME_CONCERNS, LIKELY_REPLICA, CANNOT_TELL)

#: The photos a check can carry, in the order they reach the model.
PHOTO_SLOTS = tuple(prompts.LEGIT_PHOTO_LABELS)
MIN_PHOTOS = 2
MAX_PHOTOS = len(PHOTO_SLOTS)

MAX_REASONS = 5
MAX_REASON_CHARS = 240

# A reason that concludes what the item *is*, rather than describing a photo.
# "matches genuine examples" and "typical of replicas" are observations and
# pass; "this is authentic", "100% real", "verified genuine" do not.
_CERTIFYING = re.compile(
    r"(?i)("
    r"\b(?:it|this|the\s+item|the\s+bag|the\s+jacket|the\s+shoe|item)\s+"
    r"(?:is|looks|appears)\s+(?:to\s+be\s+)?(?:an?\s+)?"
    r"(?:definitely\s+|certainly\s+|clearly\s+)?"
    r"(?:authentic|genuine|real|legit|fake|counterfeit)\b"
    r"|\b100\s*%"
    r"|\bguarantee"
    r"|\bcertif"
    r"|(?<!be )(?<!not )\bverified\b"
    r"|\bauthenticated\b"
    r")"
)


def _reasons(raw: object) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    kept: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            continue
        text = promptsafety.sanitize_text(entry, MAX_REASON_CHARS, field="legit_reason")
        if not text or _CERTIFYING.search(text):
            continue
        if text not in kept:
            kept.append(text)
        if len(kept) == MAX_REASONS:
            break
    return kept


def validate(data: dict, sent: list[str]) -> dict:
    """The model's reply reduced to what may reach the app.

    `sent` is the slots the request carried; `unclear_photos` may name only
    those. Returns the fields of `main.LegitResponse` but its version."""
    verdict = data.get("verdict")
    verdict = verdict.strip().lower() if isinstance(verdict, str) else ""
    if verdict not in VERDICTS:
        verdict = CANNOT_TELL

    reasons = _reasons(data.get("reasons"))
    if not reasons:
        verdict = CANNOT_TELL

    unclear_raw = data.get("unclear_photos")
    unclear: list[str] = []
    if isinstance(unclear_raw, list):
        for entry in unclear_raw:
            slot = entry.strip().lower() if isinstance(entry, str) else ""
            if slot in sent and slot not in unclear:
                unclear.append(slot)
    # Every photo unclear is nothing judged, whatever the verdict said.
    if unclear and len(unclear) == len(sent):
        verdict = CANNOT_TELL

    return {"verdict": verdict, "reasons": reasons,
            "unclear_photos": sorted(unclear, key=PHOTO_SLOTS.index)}


def item_context(brand: str | None, item_name: str | None, category: str | None) -> str:
    """The earlier scan's identification, made safe to put in the prompt.

    It is model output carried back by the client: sanitised like `/listing`'s
    fields and fenced, so that text read from the first photo gets no second
    attempt at instructing this call."""
    brand = promptsafety.sanitize_text(brand, promptsafety.MAX_BRAND, field="brand")
    name = promptsafety.sanitize_text(item_name, promptsafety.MAX_ITEM_NAME, field="item_name")
    category = promptsafety.sanitize_text(category, promptsafety.MAX_CATEGORY, field="category")
    parts = [f"brand: {brand or 'Unknown'}"]
    if name:
        parts.append(f"item: {name}")
    if category:
        parts.append(f"category: {category}")
    return promptsafety.fence("; ".join(parts))
