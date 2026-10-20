"""The load test measures the real /scan path only if its inputs are realistic.

A harness whose upload failed validation would measure the 400 path, and one
whose fake model answer failed to parse would measure the error path, and
either would still print a tidy table.
"""

from __future__ import annotations

import asyncio
import importlib
import io

import pytest
from PIL import Image

from scan import aiconfig
from scan import imagevalidation
import main
from loadtest import run


def test_the_upload_passes_validation_at_the_apps_size():
    data = run._photo_like_jpeg()
    assert imagevalidation.validate(data, "image/jpeg") == "image/jpeg"
    with Image.open(io.BytesIO(data)) as img:
        assert max(img.size) == 1568  # ScanAPIClient.maxUploadEdge
    # A real garment photo at that size and quality 0.8 is a few hundred KB.
    assert 150_000 < len(data) < 600_000


@pytest.fixture
def fake_model(monkeypatch):
    real = main._model
    monkeypatch.setenv("LOADTEST_MODEL_MEDIAN_S", "0.01")
    monkeypatch.setenv("LOADTEST_MODEL_CAP_S", "0.05")
    import loadtest.server as server
    server = importlib.reload(server)
    try:
        yield main._model
    finally:
        main._model = real


def test_the_fake_answer_reads_as_a_valuation_with_usage(fake_model):
    response = asyncio.run(fake_model.generate_content_async([]))
    text = aiconfig.extract_text(response)
    assert main._extract_json(text)["est_value_high_usd"] == 90.0
    assert aiconfig.usage_of(response)["thoughts_tokens"] == 1450
