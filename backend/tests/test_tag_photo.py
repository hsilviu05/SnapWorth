"""A second photo of the label (#88): accepted, Pro-only, never load-bearing."""

from __future__ import annotations

import io
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import prompts  # noqa: E402
from main import app  # noqa: E402
from tests.test_main import MOCK_RESPONSE_JSON, padded_image_bytes  # noqa: E402

client = TestClient(app)


def scan_request(*, with_tag: bool, tag_bytes: bytes | None = None, device: str = "tag-test",
                 pro: bool = True):
    """POST /scan with, or without, a label close-up."""
    files = {"file": ("scan.jpg", io.BytesIO(padded_image_bytes("JPEG", 2048)), "image/jpeg")}
    if with_tag:
        payload = tag_bytes if tag_bytes is not None else padded_image_bytes("JPEG", 1024)
        files["tag"] = ("tag.jpg", io.BytesIO(payload), "image/jpeg")

    response = MagicMock()
    response.text = json.dumps(MOCK_RESPONSE_JSON)
    with patch("main._model") as model, \
         patch("auth.Principal.is_pro", property(lambda self: pro)):
        model.generate_content_async = AsyncMock(return_value=response)
        result = client.post("/scan", files=files, headers={"x-device-id": device})
        calls = model.generate_content_async.await_args_list
    return result, calls


def contents_of(calls):
    """The `contents` list handed to the model on the first call."""
    return calls[0].args[0]


class TestTagPhotoReachesTheModel:
    def test_two_parts_and_the_addendum_when_a_tag_is_sent(self):
        result, calls = scan_request(with_tag=True, device="tag-two")
        assert result.status_code == 200
        contents = contents_of(calls)
        # prompt, item, tag — in that order, because the prompt names them so.
        assert len(contents) == 3
        assert prompts.TAG_PHOTO_ADDENDUM in contents[0]
        assert contents[1]["mime_type"] == "image/jpeg"
        assert contents[2]["mime_type"] == "image/jpeg"
        assert contents[1]["data"] != contents[2]["data"]

    def test_one_part_and_no_addendum_without_a_tag(self):
        result, calls = scan_request(with_tag=False, device="tag-one")
        assert result.status_code == 200
        contents = contents_of(calls)
        assert len(contents) == 2
        assert prompts.TAG_PHOTO_ADDENDUM not in contents[0]

    def test_the_addendum_never_edits_the_single_photo_prompts(self):
        # The eval baselines are measured against these exact strings.
        v2, _ = prompts.get_prompt("v2")
        assert not v2.endswith(prompts.TAG_PHOTO_ADDENDUM)
        assert prompts.with_tag_photo(v2) == v2 + prompts.TAG_PHOTO_ADDENDUM


class TestTagPhotoIsNeverLoadBearing:
    def test_a_corrupt_tag_is_dropped_and_the_scan_still_answers(self):
        result, calls = scan_request(with_tag=True, tag_bytes=b"not an image at all",
                                     device="tag-corrupt")
        assert result.status_code == 200, "the item photo alone still produces the answer"
        assert len(contents_of(calls)) == 2

    def test_an_empty_tag_part_is_ignored(self):
        result, calls = scan_request(with_tag=True, tag_bytes=b"", device="tag-empty")
        assert result.status_code == 200
        assert len(contents_of(calls)) == 2

    def test_free_tier_tag_is_ignored(self):
        result, calls = scan_request(with_tag=True, pro=False, device="tag-free")
        assert result.status_code == 200
        assert len(contents_of(calls)) == 2, "a second photo is a Pro feature"


class TestQuotaAndCost:
    def test_one_model_call_for_two_photos(self):
        _, calls = scan_request(with_tag=True, device="tag-cost")
        assert len(calls) == 1, "a refinement, not a second scan"
