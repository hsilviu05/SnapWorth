"""Model construction and generation parameters.

The problem this fixes
----------------------
`main.py` previously did:

    _model = genai.GenerativeModel("gemini-2.5-flash")   # old SDK

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

Determinism, and the SDK this now uses
--------------------------------------
This module targets **google-genai**. The previous SDK, `google-generativeai`,
reached end-of-life at 0.8.6 and announced it on import.

That migration also removed a workaround. `google-generativeai` exposed `seed`
on its `GenerationConfig` dataclass while the protobuf it serialised into had
no such field, so construction succeeded and the *call* failed — surfacing as
"the AI service is temporarily unavailable" for every scan over nine days in
production. The fix at the time was to probe which optional fields the
SDK/proto pairing actually accepted and drop the ones it did not, which meant
shipping without a seed. google-genai accepts `seed` end-to-end, verified
against the live API, so it is now sent unconditionally and the probe is gone.

Gemini still does not guarantee bit-identical output across calls with a fixed
seed and temperature. It narrows variance rather than eliminating it, which is
why consistency is measured empirically rather than assumed.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

from google import genai
from google.genai import types

log = logging.getLogger("snapworth.aiconfig")

MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Near-greedy: the same photo should produce the same price.
TEMPERATURE = float(os.environ.get("GEMINI_TEMPERATURE", "0.15"))
TOP_P = float(os.environ.get("GEMINI_TOP_P", "0.90"))
SEED = int(os.environ.get("GEMINI_SEED", "20260728"))

# v2's structured payload runs ~700-900 tokens — but on a thinking model the
# reasoning tokens are drawn from this SAME ceiling, and they are both larger
# and more variable than the answer: measured at 1177-1777 for one scan, and
# higher with an image than with text.
#
# The old 2048 was sized for the payload alone. In production that left ~256
# tokens for JSON after thinking, so every scan truncated mid-object — before
# reaching the price fields, which sit two-thirds down the schema. The parse
# failure was then papered over downstream and users were shown "$1-5", a
# number no model ever produced. Raising the ceiling is the actual fix.
#
# 8192 is a cap, not a spend: unused headroom is not billed, and the tokens
# already being burned on truncated answers are pure waste. Sized so the
# worst observed thinking (~1800) plus a full payload (~900) still leaves
# room for a harder image, while keeping a pathological response bounded.
MAX_OUTPUT_TOKENS = int(os.environ.get("GEMINI_MAX_OUTPUT_TOKENS", "8192"))

# The listing endpoint returns far less prose, but pays the same thinking tax
# out of the same ceiling — 800 did not cover the reasoning alone.
LISTING_MAX_OUTPUT_TOKENS = int(os.environ.get("GEMINI_LISTING_MAX_TOKENS", "4096"))

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


def _safety_settings() -> list[types.SafetySetting]:
    """Permit ordinary resale inventory; still block genuinely harmful content.

    A photo of a vintage hunting knife or a whisky decanter is normal thrift
    stock and must not read as an outage.
    """
    categories = [
        types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
    ]
    # The SDK's enums rather than the equivalent strings: pydantic coerces the
    # strings, so both work, but a typo in one would only surface as a category
    # silently not being configured.
    return [types.SafetySetting(
                category=c,
                threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH)
            for c in categories]


def generation_config(
    *, json_mode: bool = True, max_output_tokens: int | None = None
) -> types.GenerateContentConfig:
    """Sampling parameters, plus the safety thresholds.

    `seed` is now attached unconditionally. On google-generativeai it could not
    be: the dataclass exposed the field, the protobuf it serialised into did
    not, and the mismatch surfaced only at request time as an outage. google-genai
    accepts it end-to-end, so the probe that worked around that is gone and
    determinism rests on seed as well as temperature and top_p.

    Safety settings live here rather than on the client because this SDK takes
    the whole configuration per call.
    """
    kwargs: dict[str, Any] = {
        "temperature": TEMPERATURE,
        "top_p": TOP_P,
        "candidate_count": 1,
        "max_output_tokens": max_output_tokens or MAX_OUTPUT_TOKENS,
        "seed": SEED,
        "safety_settings": _safety_settings(),
    }
    if json_mode and JSON_MODE:
        kwargs["response_mime_type"] = "application/json"
    return types.GenerateContentConfig(**kwargs)


# google-genai's HttpOptions.timeout defaults to None, i.e. no timeout at all,
# and its retry_options default to None as well. Unbounded is the wrong default
# for a request on the scan path: a hung upstream would hold a worker until the
# process restarted, and `_generate_with_retry` in main.py could never take over
# because the call it is wrapping never returns. Observed directly during this
# migration — one probe ran past ten minutes before it was killed.
#
# 60s against a p95 /scan budget of 20s (see RUNBOOK §3): generous enough that a
# slow-but-working call still completes, bounded enough that a dead one fails
# fast and hits the retry loop. Milliseconds, per the SDK's field.
REQUEST_TIMEOUT_MS = int(os.environ.get("GEMINI_TIMEOUT_MS", "60000"))


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    """One client per process. Reads GEMINI_API_KEY at first use.

    Retries are deliberately left at the SDK default of None: main.py already
    classifies and retries around this call, and a second layer underneath it
    would multiply billed attempts on a failure it has already decided is
    terminal — exactly what the quota-exhaustion fix was for.
    """
    return genai.Client(
        api_key=os.environ.get("GEMINI_API_KEY", ""),
        http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
    )


class _Model:
    """Binds a model name and its default config to a `generate_content_async`.

    An adapter, deliberately. google-genai is client-first
    (`client.aio.models.generate_content(model=..., config=...)`) whereas the
    old SDK was model-first, and the call shape is depended on by main.py,
    eval/runner.py and roughly forty test mocks that patch
    `generate_content_async`. Keeping that one method signature stable confined
    this migration to a single module.
    """

    __slots__ = ("model_name", "_default_config", "_system_instruction")

    def __init__(self, model_name: str, config: types.GenerateContentConfig,
                 system_instruction: str | None = None) -> None:
        self.model_name = model_name
        self._default_config = config
        self._system_instruction = system_instruction

    async def generate_content_async(
        self, contents, generation_config: types.GenerateContentConfig | None = None
    ):
        config = generation_config or self._default_config
        if self._system_instruction is not None:
            # Copied per call: system_instruction belongs to the config in this
            # SDK, and mutating the shared default would leak across callers.
            config = config.model_copy(
                update={"system_instruction": self._system_instruction})
        if not isinstance(contents, list):
            contents = [contents]
        return await _client().aio.models.generate_content(
            model=self.model_name, contents=contents, config=config)


def build_model(system_instruction: str | None = None) -> _Model:
    """Construct the vision model with deterministic-leaning parameters."""
    return _Model(MODEL_NAME, generation_config(), system_instruction)


_BLOCK_MARKERS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_SAFETY"}


def _finish_reason_name(response) -> str:
    """Best-effort finish reason as an upper-case string, or "" if unavailable.

    Deliberately defensive. The SDK returns an enum here, but proto-plus objects,
    plain ints and test doubles all appear in practice, and treating an
    unrecognised shape as a block would fail a perfectly good response.

    The container check is duck-typed, not `isinstance(candidates, (list,
    tuple))`. The real SDK hands back a `proto.marshal.collections.repeated.
    RepeatedComposite`, which is neither, so that guard rejected every genuine
    response and this returned "" in production while the unit tests — which
    pass plain lists — went on passing. Two things silently stopped working:
    the max_output_tokens truncation warning never fired, and no reply ever
    matched _BLOCK_MARKERS, so safety blocks surfaced to users as "the AI
    service is unavailable" instead of "try a clear photo of a single item".
    """
    candidates = getattr(response, "candidates", None)
    if candidates is None:
        return ""
    try:
        first = candidates[0]
    except (TypeError, IndexError, KeyError):
        return ""
    finish = getattr(first, "finish_reason", None)
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
        # Reasoning tokens. google-generativeai never surfaced these, which is
        # part of why the max_output_tokens bug was invisible: thinking is drawn
        # from the same ceiling as the answer, so a scan could spend 1800 tokens
        # reasoning, truncate the JSON, and look — by prompt and output counts
        # alone — entirely normal. google-genai reports it, so it is recorded.
        "thoughts_tokens": "thoughts_token_count",
    }
    out: dict[str, int] = {}
    for key, attr in fields.items():
        value = getattr(usage, attr, None)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        out[key] = int(value)
    return out
