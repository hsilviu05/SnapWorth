"""Tests for the second-hand clothing eval: the `/scan` client, the brand and
condition metrics, and (below) the dataset parser, splits and scoring.

No network and no backend: the client talks to an `httpx.MockTransport`, and
the dataset is a few JSON files written into `tmp_path`.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import metrics, scanclient
from tests.test_eval_intake import fake_jpeg


# ── the /scan client ─────────────────────────────────────────────────────────

SCAN_BODY = {"item_name": "Denim jacket", "brand": "Levi's", "category": "clothing",
             "condition_notes": "", "est_value_low_usd": 12.0, "est_value_high_usd": 30.0,
             "confidence": "Medium", "listing_title": "", "listing_description": "",
             "likely_price_usd": 18.0, "condition_grade": "good"}


class FakeBackend:
    """Answers `/health` and `/scan` from a script of responses, and records
    what each `/scan` request carried."""

    def __init__(self, script=None, commit="abc123"):
        self.script = list(script or [])
        self.commit = commit
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "commit": self.commit})
        assert request.url.path == "/scan"
        self.requests.append(request)
        step = self.script.pop(0) if self.script else (200, SCAN_BODY)
        if isinstance(step, Exception):
            raise step
        status, body, *headers = step
        return httpx.Response(status, json=body, headers=headers[0] if headers else None)


def client_for(backend: FakeBackend, tmp_path: Path | None = None, **kwargs):
    sleeps: list[float] = []

    async def no_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = scanclient.ScanClient(
        base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(backend),
        cache_dir=tmp_path / "cache" if tmp_path else None, sleep=no_sleep, **kwargs)
    return client, sleeps


def run(client, requests):
    async def go():
        await client.read_commit()
        return await client.scan_all(requests)
    return asyncio.run(go())


def request(item_id="i1", photo=b"front", tag=None):
    return scanclient.ScanRequest(item_id=item_id, photo=photo, tag=tag)


class TestScanClient:
    def test_a_good_scan(self):
        backend = FakeBackend()
        client, _ = client_for(backend)
        [outcome] = run(client, [request()])
        assert outcome.ok and outcome.body == SCAN_BODY and outcome.attempts == 1
        assert client.server_commit == "abc123"

    def test_the_tag_photo_goes_in_its_own_part(self):
        backend = FakeBackend()
        client, _ = client_for(backend)
        [outcome] = run(client, [request(tag=b"label")])
        body = backend.requests[0].content
        assert b'name="file"' in body and b'name="tag"' in body
        assert outcome.sent_tag

    def test_no_tag_part_without_a_tag(self):
        backend = FakeBackend()
        client, _ = client_for(backend)
        run(client, [request()])
        assert b'name="tag"' not in backend.requests[0].content

    def test_the_token_is_a_bearer_header_and_never_in_the_outcome(self):
        backend = FakeBackend()
        client, _ = client_for(backend, token="s3cret")
        [outcome] = run(client, [request()])
        assert backend.requests[0].headers["authorization"] == "Bearer s3cret"
        assert "s3cret" not in json.dumps(outcome.to_dict())

    def test_retries_a_5xx_then_succeeds(self):
        backend = FakeBackend([(503, {"code": "ai_unavailable", "detail": "x"}), (200, SCAN_BODY)])
        client, sleeps = client_for(backend)
        [outcome] = run(client, [request()])
        assert outcome.ok and outcome.attempts == 2 and len(sleeps) == 1

    def test_retries_a_timeout(self):
        backend = FakeBackend([httpx.ReadTimeout("slow"), (200, SCAN_BODY)])
        client, _ = client_for(backend)
        [outcome] = run(client, [request()])
        assert outcome.ok and outcome.attempts == 2

    def test_honours_retry_after_on_429(self):
        backend = FakeBackend([(429, {"code": "rate_limited", "detail": "x"},
                                {"Retry-After": "7"}), (200, SCAN_BODY)])
        client, sleeps = client_for(backend)
        run(client, [request()])
        assert sleeps == [7.0]

    def test_gives_up_after_the_retries(self):
        backend = FakeBackend([(503, {"code": "ai_unavailable", "detail": "x"})] * 10)
        client, _ = client_for(backend, retries=2)
        [outcome] = run(client, [request()])
        assert not outcome.ok and outcome.error == "ai_unavailable" and outcome.attempts == 3
        assert len(backend.requests) == 3

    def test_a_deterministic_refusal_is_not_retried(self):
        backend = FakeBackend([(422, {"code": "not_resalable", "detail": "x"})])
        client, _ = client_for(backend)
        [outcome] = run(client, [request()])
        assert outcome.error == "not_resalable" and outcome.attempts == 1

    def test_no_price_502_is_an_outcome_not_a_retry(self):
        backend = FakeBackend([(502, {"code": "ai_no_price", "detail": "x"})])
        client, _ = client_for(backend)
        [outcome] = run(client, [request()])
        assert outcome.error == "ai_no_price" and len(backend.requests) == 1

    def test_a_spent_allowance_halts_the_rest(self):
        backend = FakeBackend([(402, {"code": "quota_exhausted", "detail": "x"})])
        client, _ = client_for(backend, concurrency=1)
        outcomes = run(client, [request(f"i{n}", photo=bytes([n])) for n in range(4)])
        assert outcomes[0].error == "quota_exhausted"
        assert [o.error for o in outcomes[1:]] == ["halted:quota_exhausted"] * 3
        assert len(backend.requests) == 1

    def test_an_unparseable_200_is_counted(self):
        def answer(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/health":
                return httpx.Response(200, json={"commit": "c"})
            return httpx.Response(200, content=b"<html>")
        client = scanclient.ScanClient(base_url="http://127.0.0.1:8000",
                                       transport=httpx.MockTransport(answer))
        [outcome] = run(client, [request()])
        assert outcome.error == "unparseable" and not outcome.ok

    def test_results_keep_input_order(self):
        backend = FakeBackend()
        client, _ = client_for(backend, concurrency=3)
        outcomes = run(client, [request(f"i{n}", photo=bytes([n])) for n in range(6)])
        assert [o.item_id for o in outcomes] == [f"i{n}" for n in range(6)]


class TestCache:
    def test_a_rerun_makes_no_request(self, tmp_path):
        backend = FakeBackend()
        client, _ = client_for(backend, tmp_path)
        run(client, [request()])
        client2, _ = client_for(backend, tmp_path)
        [outcome] = run(client2, [request()])
        assert outcome.cached and outcome.ok and len(backend.requests) == 1

    def test_a_new_server_commit_misses(self, tmp_path):
        backend = FakeBackend()
        run(client_for(backend, tmp_path)[0], [request()])
        backend.commit = "def456"
        [outcome] = run(client_for(backend, tmp_path)[0], [request()])
        assert not outcome.cached and len(backend.requests) == 2

    def test_a_different_tag_misses(self, tmp_path):
        backend = FakeBackend()
        run(client_for(backend, tmp_path)[0], [request()])
        [outcome] = run(client_for(backend, tmp_path)[0], [request(tag=b"label")])
        assert not outcome.cached

    def test_the_cache_tag_separates_runs(self, tmp_path):
        backend = FakeBackend()
        run(client_for(backend, tmp_path, cache_tag="a")[0], [request()])
        [outcome] = run(client_for(backend, tmp_path, cache_tag="b")[0], [request()])
        assert not outcome.cached

    def test_transient_failures_are_not_cached(self, tmp_path):
        backend = FakeBackend([(503, {"code": "ai_unavailable", "detail": "x"})] * 4)
        run(client_for(backend, tmp_path, retries=0)[0], [request()])
        [outcome] = run(client_for(backend, tmp_path, retries=0)[0], [request()])
        assert not outcome.cached and len(backend.requests) == 2

    def test_a_deterministic_refusal_is_cached(self, tmp_path):
        backend = FakeBackend([(422, {"code": "not_resalable", "detail": "x"})])
        run(client_for(backend, tmp_path)[0], [request()])
        [outcome] = run(client_for(backend, tmp_path)[0], [request()])
        assert outcome.cached and outcome.error == "not_resalable"

    def test_a_damaged_entry_is_a_miss(self, tmp_path):
        backend = FakeBackend()
        run(client_for(backend, tmp_path)[0], [request()])
        for path in (tmp_path / "cache").rglob("*.json"):
            path.write_text("{not json")
        [outcome] = run(client_for(backend, tmp_path)[0], [request()])
        assert outcome.ok and not outcome.cached


class TestPreparePhoto:
    def test_a_small_jpeg_is_sent_as_is(self, tmp_path):
        data = fake_jpeg(1280, 720, b"x")
        path = tmp_path / "a.jpg"
        path.write_bytes(data)
        assert scanclient.prepare_photo(path) == data

    def test_a_large_photo_is_resized_to_the_upload_size(self, tmp_path):
        from PIL import Image
        path = tmp_path / "big.jpg"
        Image.new("RGB", (1920, 1080), "white").save(path, format="JPEG")
        out = scanclient.prepare_photo(path)
        with Image.open(io.BytesIO(out)) as image:
            assert image.format == "JPEG" and max(image.size) == 1568


# ── brand and condition metrics ──────────────────────────────────────────────

class TestBrand:
    @pytest.mark.parametrize("predicted, truth, verdict", [
        ("H&M", "H & M", "exact"),
        ("HM", "H&M", "exact"),
        ("Fjällräven", "Fjallraven AB", "exact"),
        ("KAPPAHL", "KappAhl", "exact"),
        ("Dr. Martens", "Dr Martens", "exact"),
        ("Levi's", "Levi Strauss & Co.", "fuzzy"),
        ("Lindex", "Lindex Kids", "fuzzy"),
        ("Esprit", "Espirit", "fuzzy"),
        ("Gant", "Ganni", "wrong"),
        ("Nike", "Adidas", "wrong"),
        ("Unknown", "Nike", "abstained"),
        (None, "Nike", "abstained"),
    ])
    def test_verdicts(self, predicted, truth, verdict):
        assert metrics.brand_verdict(predicted, truth) == verdict

    def test_accuracy_counts_fuzzy_on_top_of_exact(self):
        result = metrics.brand_accuracy([("H&M", "H & M"), ("Levis", "Levi Strauss"),
                                         ("Gant", "Ganni"), ("Unknown", "Zara")])
        assert result is not None
        assert result["exact_accuracy"] == 0.25 and result["fuzzy_accuracy"] == 0.5
        assert result["abstained"] == 1 and result["wrong"] == 1
        assert result["precision_when_attempted"] == pytest.approx(2 / 3)

    def test_unknown_truths_are_not_scored(self):
        assert metrics.brand_accuracy([("Nike", "unknown"), ("Nike", "")]) is None


class TestCondition:
    def test_mae_and_confusion(self):
        result = metrics.condition_error([("new", 5), ("good", 4), ("used", 1), (None, 3)])
        assert result is not None
        assert result["mae"] == pytest.approx(2 / 3)
        assert result["exact"] == pytest.approx(1 / 3)
        assert result["abstained"] == 1 and result["scored"] == 3 and result["n"] == 4
        assert result["confusion"] == {"1→used": 1, "4→good": 1, "5→new": 1}

    def test_an_unknown_grade_is_an_abstention(self):
        result = metrics.condition_error([("mint", 5)])
        assert result is not None and result["mae"] is None and result["abstained"] == 1

    def test_out_of_scale_truths_are_skipped(self):
        assert metrics.condition_error([("good", 0), ("good", 7), ("good", None)]) is None


# ── the dataset: parsing, profile, splits ────────────────────────────────────

from eval import run as evalrun, secondhand  # noqa: E402


def item_id(n: int, day: int = 5) -> str:
    return f"2024_03_{day:02d}_10_{n // 60:02d}_{n % 60:02d}"


def write_item(root: Path, station: str, n: int, *, photos=("front", "back", "brand"),
               day: int = 5, **labels) -> str:
    ident = item_id(n, day)
    folder = root / station / "mar2024"
    folder.mkdir(parents=True, exist_ok=True)
    record = {"brand": "Lindex", "type": "Jeans", "category": "Ladies", "condition": 4,
              "price": "", "usage": "Reuse"}
    record.update(labels)
    (folder / f"labels_{ident}.json").write_text(json.dumps([record]))
    for kind in photos:
        (folder / f"{kind}_{ident}.jpg").write_bytes(fake_jpeg(1280, 720, f"{kind}{n}".encode()))
    return ident


@pytest.fixture
def download(tmp_path) -> Path:
    root = tmp_path / "clothing_v3"
    for n in range(12):
        write_item(root, "station1", n, type="Jeans" if n % 2 else "Jacket")
    write_item(root, "station1", 50, photos=("front", "back"))                 # brand photo removed
    write_item(root, "station1", 51, photos=("back", "brand"))                 # no front
    write_item(root, "station1", 52, brand="Unknown")                         # no brand
    write_item(root, "station1", 53, condition=None)
    write_item(root, "station3", 60, price="149")
    write_item(root, "station3", 61, price="49")
    write_item(root, "station3", 62, price="0")
    write_item(root, "station3", 63, price="")
    write_item(root, "station3", 64, price="5 000 kr")
    write_item(root, "station3", 65, price="79,50")
    bad = root / "station2" / "x" / f"labels_{item_id(70)}.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("{not json")
    errors = root / "json_errors" / f"labels_{item_id(71)}.json"
    errors.parent.mkdir(parents=True)
    errors.write_text("[]")
    return root


class TestParsing:
    @pytest.mark.parametrize("raw, value", [
        ("149", 149.0), (49, 49.0), ("79,50", 79.5), ("49 kr", 49.0), ("5 000 kr", 5000.0),
        ("", None), (None, None), ("gratis", None), ("-5", None), ({"a": 1}, None)])
    def test_prices(self, raw, value):
        assert secondhand.parse_price(raw) == value

    @pytest.mark.parametrize("raw, value", [
        (4, 4), ("3", 3), ("5.0", 5), ("2 - Fair", 2), ("0", None), ("6", None), ("", None),
        (None, None), ("good", None)])
    def test_conditions(self, raw, value):
        assert secondhand.parse_condition(raw) == value

    def test_the_date_is_in_the_id(self):
        assert str(secondhand.day_of("2024_04_05_08_47_35")) == "2024-04-05"
        assert secondhand.day_of("2024_13_40_00_00_00") is None

    def test_the_split_is_stable_and_both_halves_exist(self):
        halves = {secondhand.split_of(item_id(n)) for n in range(40)}
        assert halves == {"dev", "test"}
        assert secondhand.split_of(item_id(3)) == secondhand.split_of(item_id(3))

    def test_photos_matched_by_id_and_kind(self):
        index = secondhand.image_index(["s1/front_2024_03_05_10_00_01.jpg",
                                        "s1/2024_03_05_10_00_01_BRAND.JPG",
                                        "s1/back_2024_03_05_10_00_01.png",
                                        "s1/notes.txt", "s1/front.jpg"])
        assert index == {"2024_03_05_10_00_01": {
            "front": "s1/front_2024_03_05_10_00_01.jpg",
            "brand": "s1/2024_03_05_10_00_01_BRAND.JPG",
            "back": "s1/back_2024_03_05_10_00_01.png"}}

    def test_a_folder_download(self, download):
        parsed = secondhand.parse(secondhand.DirSource(download))
        assert len(parsed.items) == 22
        assert parsed.problems["malformed JSON"] == 1
        assert parsed.problems["file in json_errors/ (skipped)"] == 1
        assert parsed.problems["front photo missing"] == 1
        assert parsed.problems["brand photo missing"] == 1
        first = next(i for i in parsed.items if i.id == item_id(0))
        assert (first.station, first.brand, first.type, first.condition) == (
            "station1", "Lindex", "Jacket", 4)
        assert first.front and first.front.endswith(f"front_{item_id(0)}.jpg")
        assert {"brand", "type", "price"} <= set(parsed.keys)

    def test_a_zip_download_reads_the_same(self, download, tmp_path):
        import zipfile
        archive = tmp_path / "clothing_v3.zip"
        with zipfile.ZipFile(archive, "w") as z:
            for path in download.rglob("*"):
                if path.is_file():
                    z.write(path, path.relative_to(download.parent).as_posix())
        from_dir = secondhand.parse(secondhand.DirSource(download))
        from_zip = secondhand.parse(secondhand.open_source(archive))
        assert sorted(i.id for i in from_zip.items) == sorted(i.id for i in from_dir.items)
        item = next(i for i in from_zip.items if i.id == item_id(0))
        assert secondhand.open_source(archive).read(item.front)  # readable in place

    def test_several_annotations_keep_the_first_and_are_counted(self, tmp_path):
        folder = tmp_path / "test100"
        folder.mkdir()
        ident = item_id(1)
        (folder / f"labels_{ident}.json").write_text(json.dumps([{"brand": "A", "condition": 3},
                                                                 {"brand": "A", "condition": 4}]))
        parsed = secondhand.parse(secondhand.DirSource(tmp_path))
        assert parsed.items[0].condition == 3 and parsed.items[0].annotations == 2
        assert ident in parsed.multi

    def test_a_bom_and_a_single_object_parse(self, tmp_path):
        (tmp_path / f"labels_{item_id(2)}.json").write_bytes(
            b"\xef\xbb\xbf" + json.dumps({"brand": "Gant"}).encode())
        parsed = secondhand.parse(secondhand.DirSource(tmp_path))
        assert parsed.items[0].brand == "Gant"

    def test_missing_data_dir_is_a_plain_message(self, monkeypatch):
        monkeypatch.delenv("EVAL_DATA_DIR", raising=False)
        with pytest.raises(SystemExit, match="EVAL_DATA_DIR is not set"):
            secondhand.open_source()


class TestProfileAndSplits:
    def test_profile_shows_the_price_distribution(self, download):
        report = secondhand.profile(secondhand.parse(secondhand.DirSource(download)))
        prices = report["price_station3"]
        assert prices["items"] == 6 and prices["missing"] == 1 and prices["zero"] == 1
        assert prices["positive"] == 4 and prices["max"] == 5000.0
        assert report["by_station"] == {"station1": 16, "station3": 6}

    def test_id_gold_eligibility(self, download):
        parsed = secondhand.parse(secondhand.DirSource(download))
        ids = {i.id for i in secondhand.eligible_for_id(parsed.items)}
        assert item_id(51) not in ids          # no front
        assert item_id(52) not in ids          # no brand
        assert item_id(53) not in ids          # no condition
        assert item_id(50) in ids              # brand photo missing is fine

    def test_the_sample_is_reproducible_and_stratified(self, download):
        items = secondhand.eligible_for_id(secondhand.parse(secondhand.DirSource(download)).items)
        a = secondhand.stratified_sample(items, 6, pool_below=1)
        b = secondhand.stratified_sample(list(reversed(items)), 6, pool_below=1)
        assert [i.id for i in a] == [i.id for i in b] and len(a) == 6
        assert {i.type for i in a} == {"Jeans", "Jacket"}

    def test_a_target_above_the_pool_takes_everything(self, download):
        items = secondhand.eligible_for_id(secondhand.parse(secondhand.DirSource(download)).items)
        assert len(secondhand.stratified_sample(items, 10_000)) == len(items)

    def test_more_strata_than_target(self):
        items = [secondhand.Item(id=item_id(n), station="station1", label_path="", front="f",
                                 brand="B", type=f"T{n}", category="C", condition=3)
                 for n in range(10)]
        assert len(secondhand.stratified_sample(items, 4, pool_below=1)) == 4

    def test_price_cutoffs(self, download):
        parsed = secondhand.parse(secondhand.DirSource(download))
        kept, dropped = secondhand.price_rows(
            parsed.items, secondhand.PriceCutoffs(10, 1000, (49.0,)))
        assert sorted(secondhand.parse_price(i.price_raw) for i in kept) == [79.5, 149.0]
        assert dropped == {"zero": 1, "no parseable price": 1, "fixed value 49": 1,
                           "above 1000": 1}

    def test_build_writes_manifests_without_images(self, download, tmp_path):
        out = tmp_path / "manifests"
        code = secondhand.main(["--data", str(download), "build", "--target", "8",
                                "--price-min", "10", "--price-max", "1000",
                                "--out", str(out)], out=io.StringIO())
        assert code == 0
        rows, problems = secondhand.load_manifest(out / "id_gold.jsonl")
        assert len(rows) == 8 and not problems
        assert {r["split"] for r in rows} <= {"dev", "test"}
        meta = json.loads((out / "price_silver.meta.json").read_text())
        assert meta["doi"] == secondhand.DOI and meta["currency"] == secondhand.PRICE_CURRENCY
        prices, _ = secondhand.load_manifest(out / "price_silver.jsonl")
        assert {r["currency"] for r in prices} == {secondhand.PRICE_CURRENCY}
        assert "annotator" not in (out / "id_gold.jsonl").read_text()
        assert not list(out.rglob("*.jpg"))

    def test_a_malformed_manifest_line_is_counted(self, tmp_path):
        path = tmp_path / "m.jsonl"
        path.write_text('{"id": "a"}\n{oops\n{"no": "id"}\n\n')
        rows, problems = secondhand.load_manifest(path)
        assert [r["id"] for r in rows] == ["a"]
        assert problems == {"malformed manifest line": 1, "manifest line without an id": 1}


# ── the run: end to end against a fake backend ───────────────────────────────

class TestTypes:
    @pytest.mark.parametrize("dataset_type, name, verdict", [
        ("Jeans", "Lindex high-waist jeans", "match"),
        ("Trousers", "Wool slacks", "match"),
        ("Jacket", "Denim jeans", "miss"),
        ("T-shirt", "Graphic tee, white", "match"),
        ("Mystery", "Anything", None),
    ])
    def test_type_verdicts(self, dataset_type, name, verdict):
        assert evalrun.type_verdict(dataset_type, name) == verdict

    def test_app_categories(self):
        assert evalrun.app_category("Shoes") == "shoes"
        assert evalrun.app_category("Scarf") == "accessories"
        assert evalrun.app_category("Jeans") == "clothing"
        assert evalrun.app_category(None) == "clothing"


class TestUrls:
    def test_local_is_fine(self):
        assert evalrun.check_url("http://127.0.0.1:8000", False) is None
        assert evalrun.check_url("http://localhost:8000", False) is None

    def test_remote_needs_the_flag(self):
        assert "--allow-remote" in (evalrun.check_url("https://api.snapworth.app", False) or "")
        assert evalrun.check_url("https://api.snapworth.app", True) is None

    def test_not_a_url(self):
        assert evalrun.check_url("127.0.0.1:8000", False) is not None


@pytest.fixture
def built(download, tmp_path, monkeypatch):
    out = tmp_path / "manifests"
    secondhand.main(["--data", str(download), "build", "--target", "100", "--price-min", "10",
                     "--price-max", "1000", "--out", str(out)], out=io.StringIO())
    monkeypatch.setattr(evalrun, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr("eval.identity.load_env", lambda: None)
    monkeypatch.setenv("EVAL_BACKEND_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("EVAL_DATA_DIR", str(download))
    monkeypatch.delenv("EVAL_SCAN_TOKEN", raising=False)
    return out


def answers(by_photo):
    """A backend whose /scan answer depends on which item's photo it got."""
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"commit": "c0ffee"})
        for marker, response in by_photo.items():
            if marker.encode() in request.content:
                return response
        return httpx.Response(200, json=SCAN_BODY | {"brand": "Lindex", "item_name": "Jeans",
                                                     "condition_grade": "likeNew"})
    return httpx.MockTransport(handle)


class TestRun:
    def run(self, built, tmp_path, transport, *args):
        out = io.StringIO()
        code = evalrun.main(["--manifests", str(built), "--out", str(tmp_path / "run"), *args],
                            transport=transport, out=out)
        results = json.loads((tmp_path / "run" / "results.json").read_text())
        return code, results, out.getvalue()

    def test_identification_end_to_end(self, built, tmp_path):
        transport = answers({
            "front0": httpx.Response(200, json=SCAN_BODY | {"brand": "Gant", "item_name": "Coat",
                                                            "condition_grade": "used"}),
            "front2": httpx.Response(503, json={"code": "ai_unavailable", "detail": "x"}),
        })
        code, results, out = self.run(built, tmp_path, transport, "--split", "id_gold",
                                      "--subset", "all", "--retries", "0")
        assert code == 0
        counts = results["counts"]
        assert counts["errors"] == {"ai_unavailable": 1}
        assert counts["ok"] == counts["sent"] - 1
        overall = results["metrics"]["overall"]
        assert overall["brand"]["wrong"] == 1
        assert overall["condition"]["mae"] > 0
        assert results["worst"][0]["id"] == item_id(0)
        report = (tmp_path / "run" / "report.md").read_text()
        assert "## Identification" in report and item_id(0) in report
        assert results["backend"] == {"host": "127.0.0.1", "commit": "c0ffee"}

    def test_dev_is_the_default_half(self, built, tmp_path):
        _, results, _ = self.run(built, tmp_path, answers({}), "--split", "id_gold")
        manifest, _ = secondhand.load_manifest(built / "id_gold.jsonl")
        assert results["counts"]["cases"] == sum(1 for r in manifest if r["split"] == "dev")

    def test_limit(self, built, tmp_path):
        _, results, _ = self.run(built, tmp_path, answers({}), "--split", "id_gold",
                                 "--subset", "all", "--limit", "3")
        assert results["counts"]["cases"] == 3

    def test_no_tag_without_a_token(self, built, tmp_path):
        _, results, _ = self.run(built, tmp_path, answers({}), "--split", "id_gold",
                                 "--subset", "all")
        assert results["config"]["send_tag"] is False
        assert not any(o["sent_tag"] for o in results["outcomes"])

    def test_with_a_token_the_brand_photo_is_sent(self, built, tmp_path, monkeypatch):
        monkeypatch.setenv("EVAL_SCAN_TOKEN", "tok")
        _, results, out = self.run(built, tmp_path, answers({}), "--split", "id_gold",
                                   "--subset", "all")
        assert results["config"]["send_tag"] is True and results["config"]["token"] is True
        assert results["counts"]["not_sent"].get("brand photo missing (sent front only)") == 1
        assert "tok" not in (tmp_path / "run" / "results.json").read_text().replace("token", "")
        assert "tok" not in out.replace("token", "")

    def test_prices_end_to_end(self, built, tmp_path):
        transport = answers({"front60": httpx.Response(200, json=SCAN_BODY | {
            "likely_price_usd": 14.0, "est_value_low_usd": 10.0, "est_value_high_usd": 20.0})})
        code, results, _ = self.run(built, tmp_path, transport, "--split", "price_silver")
        assert code == 0 and results["counts"]["ok"] == 3     # 49, 79.50 and 149 SEK
        assert results["basis"] == evalrun.PRICE_SILVER_BASIS
        assert results["config"]["fx_table"].startswith("ecb-")
        assert {w["id"] for w in results["worst"]} == {item_id(60), item_id(61), item_id(65)}
        assert results["metrics"]["overall"] == {"n": 3}       # below MIN_GROUP: no figures
        assert "## Price" in (tmp_path / "run" / "report.md").read_text()

    def test_a_rerun_is_served_from_the_cache(self, built, tmp_path):
        self.run(built, tmp_path, answers({}), "--split", "id_gold", "--subset", "all")
        _, results, _ = self.run(built, tmp_path, answers({}), "--split", "id_gold",
                                 "--subset", "all")
        assert results["counts"]["cached"] == results["counts"]["sent"]

    def test_a_remote_url_is_refused(self, built, tmp_path, monkeypatch):
        monkeypatch.setenv("EVAL_BACKEND_URL", "https://api.example.com")
        out = io.StringIO()
        code = evalrun.main(["--split", "id_gold", "--manifests", str(built)], out=out)
        assert code == 2 and "--allow-remote" in out.getvalue()

    def test_a_spent_allowance_stops_the_run(self, built, tmp_path):
        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/health":
                return httpx.Response(200, json={"commit": "c"})
            return httpx.Response(402, json={"code": "quota_exhausted", "detail": "x"})
        code, results, out = self.run(built, tmp_path, httpx.MockTransport(handle),
                                      "--split", "id_gold", "--subset", "all",
                                      "--concurrency", "1", "--no-cache")
        assert code == 1 and "FREE_SCANS_PER_DAY" in out
        assert results["counts"]["errors"]["quota_exhausted"] == 1


class TestPriceScoring:
    def test_figures_once_a_group_is_big_enough(self):
        cases = [evalrun.Case(id=f"p{n}", group="Jeans", front="f", price_usd=10.0)
                 for n in range(6)]
        bodies = [  # (likely, low, high)
            (10.0, 5.0, 15.0), (12.0, 8.0, 16.0), (20.0, 15.0, 30.0),
            (5.0, 4.0, 6.0), (11.0, 9.0, 14.0), (None, 6.0, 14.0)]
        outcomes = {}
        for case, (likely, low, high) in zip(cases, bodies):
            body = SCAN_BODY | {"likely_price_usd": likely, "est_value_low_usd": low,
                                "est_value_high_usd": high}
            outcomes[case.id] = scanclient.ScanOutcome(case.id, 200, body)
        result, worst = evalrun.score_prices(cases, outcomes)
        overall = result["overall"]
        assert overall["n"] == 6
        assert overall["mdape"] == pytest.approx(15.0)       # APEs 0, 20, 100, 50, 10, 0
        assert overall["within_range"] == pytest.approx(4 / 6)
        assert overall["midpoint_fallbacks"] == 1
        assert result["by_category"]["Jeans"]["n"] == 6
        assert worst[0]["id"] == "p2" and worst[0]["ape"] == pytest.approx(100.0)

    def test_failed_scans_are_not_scored(self):
        case = evalrun.Case(id="p", group="g", front="f", price_usd=10.0)
        result, worst = evalrun.score_prices(
            [case], {"p": scanclient.ScanOutcome("p", 502, {"code": "ai_no_price"},
                                                 error="ai_no_price")})
        assert result["overall"] == {"n": 0} and worst == []
