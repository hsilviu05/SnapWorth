"""Model construction and generation parameters.

The problem this fixes
----------------------
`main.py` previously did:

    _model = genai.GenerativeModel("gemini-2.5-flash")

with no generation config at all. That means every sampling parameter took its
API default, and for Gemini that is **temperature 1.0** — full sampling
randomness on a task whose entire purpose is producing a number a user will act
on. Two uploads of the same photo could return materially different prices, and
nothing in the system was pinning them together.

For a valuation task that is simply the wrong setting. We are not generating
prose, we are extracting a quantity. Low temperature is not a stylistic
preference here, it is a correctness requirement: the same evidence should yield
the same price.

Four settings, and why each
---------------------------
* **temperature 0.15** — near-greedy. Preserves enough sampling to escape a
  degenerate token loop, while making repeat scans of one item cluster tightly.
  Measured by the `consistency` metric in `backend/eval/`.

* **response_mime_type "application/json"** — constrains decoding to valid JSON
  at the sampler. This is what makes `_extract_json`'s regex scraping and the
  `_retry_as_json` second billed call into a fallback rather than the norm. Both
  are retained, because a fallback that never fires costs nothing and a missing
  one costs a user their scan.

* **max_output_tokens** — v2 returns considerably more structure than v1, so
  this is sized for it with headroom. Without a ceiling a pathological response
  is unbounded in both latency and cost.

* **safety_settings at BLOCK_ONLY_HIGH** — thrift inventory legitimately
  includes pocket knives, lighters, vintage medical and military items, and
  alcohol-branded glassware. Default thresholds block those, and a block
  surfaces as an exception on `response.text`, which the old code caught with a
  bare `except Exception`, retried, and finally reported as "the AI service is
  temporarily unavailable". Users got a service outage message for a photo of a
  penknife. We loosen to BLOCK_ONLY_HIGH (genuinely harmful content still
  blocked) and handle the block explicitly — see `ModelBlocked`.

Determinism caveat
------------------
`seed` is attached only when the installed SDK *and* its protobuf pairing both
accept it — see `proto_supported_optional_fields`. With
google-generativeai 0.8.3 they do not agree, so it is currently omitted and
determinism rests on temperature and top_p alone.

Even where it is sent, Gemini does not guarantee bit-identical output across
calls with a fixed seed and temperature. It narrows variance rather than
eliminating it, which is why consistency is measured empirically rather than
assumed.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

import google.generativeai as genai

log = logging.getLogger("snapworth.aiconfig")

MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Near-greedy: the same photo should produce the same price.
TEMPERATURE = float(os.environ.get("GEMINI_TEMPERATURE", "0.15"))
TOP_P = float(os.environ.get("GEMINI_TOP_P", "0.90"))
SEED = int(os.environ.get("GEMINI_SEED", "20260728"))

# v2's structured payload runs ~700-900 tokens; 2048 leaves headroom without
# letting a pathological response run unbounded.
MAX_OUTPUT_TOKENS = int(os.environ.get("GEMINI_MAX_OUTPUT_TOKENS", "2048"))

# The listing endpoint returns far less, so it gets a tighter ceiling.
LISTING_MAX_OUTPUT_TOKENS = int(os.environ.get("GEMINI_LISTING_MAX_TOKENS", "800"))

# Set to "0" to fall back to prose parsing if a future model regresses on
# constrained decoding. The regex extractor stays in place either way.
JSON_MODE = os.environ.get("GEMINI_JSON_MODE", "1").lower() in {"1", "true", "yes"}


class ModelBlocked(Exception):
    """The model refused to answer because of a safety filter.

    Distinct from a transient failure: retrying is pointless and reporting it as
    an outage is misleading. The caller should tell the user what happened.
    """


class ModelUnavailable(Exception):
    """A transient upstream failure. Retrying is reasonable."""


def _safety_settings() -> list[dict]:
    """Permit ordinary resale inventory; still block genuinely harmful content.

    A photo of a vintage hunting knife or a whisky decanter is normal thrift
    stock and must not read as an outage.
    """
    categories = [
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    ]
    return [{"category": c, "threshold": "BLOCK_ONLY_HIGH"} for c in categories]


def to_proto_config(config: genai.types.GenerationConfig):
    """Convert a `GenerationConfig` exactly the way the SDK does before a call.

    Exposed (rather than inlined) because this conversion is the step that
    actually rejects unsupported fields, so it is also the only honest way to
    test that a config we intend to send is sendable.
    """
    from google.generativeai.types import generation_types
    import google.ai.generativelanguage as glm

    return glm.GenerationConfig(generation_types.to_generation_config_dict(config))


@lru_cache(maxsize=1)
def proto_supported_optional_fields() -> frozenset[str]:
    """Which optional fields this SDK *and* its protobuf pairing will actually accept.

    Why this is probed rather than assumed
    --------------------------------------
    `google-generativeai` 0.8.3 exposes `seed` on the `GenerationConfig`
    *dataclass*, but the `google.ai.generativelanguage` protobuf it serialises
    into has no such field. Construction therefore succeeds and the call fails
    later, at proto conversion, with:

        ValueError: Unknown field for GenerationConfig: seed

    Because that error text contains none of the markers in `main._NON_RETRYABLE`
    it was classified transient, retried twice, and surfaced to users as "the AI
    service is temporarily unavailable" — a permanent, 100%-reproducible bug
    reported as an outage. Every scan reaching Gemini failed this way from
    783aad3 (28 Jul) until this fix.

    The previous guard tried to handle exactly this and could not: it caught
    `TypeError` around dataclass construction, which never raises, while the real
    failure is a `ValueError` one layer down and one call later. Validating here
    against the same conversion that runs at request time is what makes the guard
    match the failure.

    Probed once and cached: the answer is a property of the installed packages
    and cannot change while the process is alive. Being field-agnostic, it also
    covers the *next* field to diverge this way rather than only `seed`; and if a
    future SDK/proto bump adds real support, the field is picked back up with no
    code change, so the fix cannot silently strand determinism either.
    """
    supported: set[str] = set()
    for name, probe_value in (("seed", 1), ("response_mime_type", "application/json")):
        try:
            to_proto_config(genai.types.GenerationConfig(**{name: probe_value}))
            supported.add(name)
        except Exception as exc:
            # Dropping an optional field costs determinism or JSON mode; sending
            # one the transport rejects costs every request. Degrade.
            log.warning(
                "generation config field %r unsupported by the installed "
                "SDK/proto pairing — omitting it: %s", name, exc)
    return frozenset(supported)


def generation_config(
    *, json_mode: bool = True, max_output_tokens: int | None = None
) -> genai.types.GenerationConfig:
    kwargs: dict = {
        "temperature": TEMPERATURE,
        "top_p": TOP_P,
        "candidate_count": 1,
        "max_output_tokens": max_output_tokens or MAX_OUTPUT_TOKENS,
    }
    supported = proto_supported_optional_fields()
    if "seed" in supported:
        kwargs["seed"] = SEED
    if json_mode and JSON_MODE and "response_mime_type" in supported:
        kwargs["response_mime_type"] = "application/json"
    return genai.types.GenerationConfig(**kwargs)


def build_model(system_instruction: str | None = None) -> genai.GenerativeModel:
    """Construct the vision model with deterministic-leaning parameters."""
    return genai.GenerativeModel(
        MODEL_NAME,
        generation_config=generation_config(),
        safety_settings=_safety_settings(),
        system_instruction=system_instruction,
    )


_BLOCK_MARKERS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_SAFETY"}


def _finish_reason_name(response) -> str:
    """Best-effort finish reason as an upper-case string, or "" if unavailable.

    Deliberately defensive. The SDK returns an enum here, but proto-plus objects,
    plain ints and test doubles all appear in practice, and treating an
    unrecognised shape as a block would fail a perfectly good response.
    """
    candidates = getattr(response, "candidates", None)
    if not isinstance(candidates, (list, tuple)) or not candidates:
        return ""
    finish = getattr(candidates[0], "finish_reason", None)
    if finish is None:
        return ""
    name = getattr(finish, "name", None)
    return str(name if isinstance(name, str) else finish).upper()


def extract_text(response) -> str:
    """Read text from a response, distinguishing a safety block from a failure.

    Ordering matters: **the success path is tried first**. If the response
    carries text, that text is the answer and nothing else needs interrogating.
    Only when there is no usable text do we look at why — because that is the
    only situation where the distinction changes what the user is told.

    The inverse (inspecting block metadata before reading text) is fragile: it
    has to correctly interpret every shape the SDK might use for those fields,
    and any misread turns a good response into a spurious refusal.
    """
    text = ""
    try:
        text = (response.text or "").strip()
    except Exception as exc:
        # `.text` raises when there are no candidates — usually a block.
        reason = _finish_reason_name(response)
        feedback = getattr(response, "prompt_feedback", None)
        block_reason = getattr(feedback, "block_reason", None)
        if reason in _BLOCK_MARKERS or isinstance(block_reason, str) and block_reason:
            raise ModelBlocked(reason or str(block_reason)) from None
        raise ModelUnavailable(f"response carried no text: {exc}") from exc

    if text:
        if _finish_reason_name(response) == "MAX_TOKENS":
            # Truncated mid-JSON. Logged so it reads as a config problem rather
            # than an unexplained parse failure downstream.
            log.warning("model response hit max_output_tokens — output truncated")
        return text

    # Empty but no exception: a block that returned an empty candidate.
    reason = _finish_reason_name(response)
    if reason in _BLOCK_MARKERS:
        raise ModelBlocked(reason)
    raise ModelUnavailable(f"model returned empty text (finish_reason={reason or 'unknown'})")


def usage_of(response) -> dict:
    """Token counts for cost attribution. Absent on some SDK paths.

    Values are coerced to `int` and non-numeric shapes dropped, so a changed SDK
    surface degrades to "no usage recorded" rather than writing opaque objects
    into structured logs.
    """
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return {}
    fields = {
        "prompt_tokens": "prompt_token_count",
        "output_tokens": "candidates_token_count",
        "total_tokens": "total_token_count",
    }
    out: dict[str, int] = {}
    for key, attr in fields.items():
        value = getattr(usage, attr, None)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        out[key] = int(value)
    return out
