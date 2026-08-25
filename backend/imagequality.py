"""Measurable image-quality signals for the confidence model.

Why this exists
---------------
The old confidence value was whatever the model said about itself. Asking an LLM
"how sure are you?" measures nothing: language models are poorly calibrated at
self-assessment and systematically overconfident, so a blurry photo of an
unidentifiable jumper could come back "High confidence" and the UI would render a
checkmark next to it.

Image quality is the one input to a valuation that we can measure *objectively*,
before the model runs, from bytes we already hold. A photo that is out of focus,
badly exposed or too small genuinely does carry less information — so it should
genuinely lower the confidence we report, regardless of how fluent the model's
answer sounds.

Everything here is deliberately cheap. All metrics are computed on a downsampled
copy (256 px longest edge), which costs single-digit milliseconds and runs before
the paid model call rather than adding to the critical path after it.

Degradation policy
------------------
Pillow is an optional dependency (see `imagevalidation.py`). Without it, every
metric returns `None` and the confidence model simply drops image quality from
its weighting rather than assuming the worst — an unmeasurable signal must not
be treated as a bad signal.
"""

from __future__ import annotations

import io
import logging
import math
from dataclasses import dataclass

log = logging.getLogger("snapworth.imagequality")

# Analysis resolution. Sharpness is scale-sensitive, so every image is normalised
# to the same longest edge before measuring — otherwise a 4032 px photo and a
# 640 px photo of the same scene score differently for no real reason.
ANALYSIS_EDGE = 256

# Laplacian-variance thresholds, calibrated against the normalised edge above.
# Below `BLUR_FLOOR` an image is unusably soft; above `BLUR_CEILING` extra
# sharpness stops telling us anything new.
BLUR_FLOOR = 40.0
BLUR_CEILING = 600.0

# Mean-luminance window (0–255) outside which an image is under/over exposed.
EXPOSURE_MIN = 45.0
EXPOSURE_MAX = 215.0

# Below this many pixels on the long edge, detail like a brand tag is gone.
MIN_USEFUL_EDGE = 400


@dataclass(frozen=True)
class ImageQuality:
    """Normalised 0–1 quality signals. `None` means "could not measure"."""

    sharpness: float | None = None       # 1.0 = crisp, 0.0 = unusably soft
    exposure: float | None = None        # 1.0 = well exposed
    detail: float | None = None          # 1.0 = plenty of resolution
    contrast: float | None = None        # 1.0 = good tonal separation
    width: int | None = None
    height: int | None = None

    @property
    def measured(self) -> bool:
        return self.sharpness is not None

    @property
    def overall(self) -> float | None:
        """Single 0–1 score, or None when nothing could be measured.

        Sharpness dominates because focus is what determines whether a brand tag
        or model number is legible, which is the difference between an identified
        item and a guess.
        """
        parts = [
            (self.sharpness, 0.45),
            (self.detail, 0.25),
            (self.exposure, 0.20),
            (self.contrast, 0.10),
        ]
        available = [(v, w) for v, w in parts if v is not None]
        if not available:
            return None
        total_weight = sum(w for _, w in available)
        return sum(v * w for v, w in available) / total_weight

    def issues(self) -> list[str]:
        """Human-readable problems, for the explainability payload.

        Phrased as what the *user* can do, not as a metric readout: "the photo is
        slightly out of focus" is actionable, "laplacian variance 38.2" is not.
        """
        found: list[str] = []
        if self.sharpness is not None and self.sharpness < 0.35:
            found.append("the photo is soft or out of focus")
        if self.exposure is not None and self.exposure < 0.4:
            found.append("the lighting is uneven — too dark or blown out")
        if self.detail is not None and self.detail < 0.35:
            found.append("the photo is low resolution, so small details like tags aren't legible")
        if self.contrast is not None and self.contrast < 0.3:
            found.append("the item doesn't stand out clearly from the background")
        return found


def _normalise(value: float, floor: float, ceiling: float) -> float:
    """Map `value` onto 0–1 across [floor, ceiling], clamped."""
    if ceiling <= floor:
        return 0.0
    return max(0.0, min(1.0, (value - floor) / (ceiling - floor)))


def analyse(data: bytes) -> ImageQuality:
    """Measure quality signals. Never raises — returns an empty result instead.

    Called before the model, so a failure here must not cost the user their scan.
    """
    try:
        from PIL import Image, ImageFilter, ImageStat
    except ImportError:  # pragma: no cover - exercised only without Pillow
        log.warning("Pillow unavailable — image quality signals disabled")
        return ImageQuality()

    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            grey = img.convert("L")

            # Normalise scale before measuring sharpness.
            longest = max(grey.size)
            if longest > ANALYSIS_EDGE:
                ratio = ANALYSIS_EDGE / longest
                grey = grey.resize(
                    (max(1, int(grey.width * ratio)), max(1, int(grey.height * ratio))),
                    Image.Resampling.BILINEAR,
                )

            # Laplacian variance: the standard focus measure. A 3×3 discrete
            # Laplacian responds to intensity discontinuities, so a sharp image
            # produces a wide response distribution and a blurred one collapses
            # toward zero.
            laplacian = grey.filter(
                ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)
            )
            lap_stats = ImageStat.Stat(laplacian)
            variance = lap_stats.var[0]

            base_stats = ImageStat.Stat(grey)
            mean = base_stats.mean[0]
            stddev = base_stats.stddev[0]

        sharpness = _normalise(variance, BLUR_FLOOR, BLUR_CEILING)

        # Exposure: distance from the centre of the usable luminance window,
        # mapped so the middle of the window scores 1.0 and either edge 0.0.
        centre = (EXPOSURE_MIN + EXPOSURE_MAX) / 2
        half_span = (EXPOSURE_MAX - EXPOSURE_MIN) / 2
        exposure = max(0.0, 1.0 - abs(mean - centre) / half_span)

        # Detail: resolution on the long edge, on a log scale because the gain
        # from 400→800 px matters far more than 3000→3400 px.
        long_edge = max(width, height)
        detail = _normalise(
            math.log10(max(long_edge, 1)), math.log10(MIN_USEFUL_EDGE), math.log10(2400)
        )

        # Contrast: global tonal spread. Low stddev means a flat, washed-out
        # frame where the subject doesn't separate from its background.
        contrast = _normalise(stddev, 12.0, 70.0)

        return ImageQuality(
            sharpness=round(sharpness, 4),
            exposure=round(exposure, 4),
            detail=round(detail, 4),
            contrast=round(contrast, 4),
            width=width,
            height=height,
        )
    except Exception as exc:
        # HEIC without a plugin, truncated bytes, exotic colour spaces. The scan
        # still proceeds; confidence simply loses one input.
        log.info("image quality analysis skipped: %s", exc)
        return ImageQuality()
