"""Tests for the eBay sold-listings collector (#213).

eBay is replaced by recorded responses: no network, no keys. These pin what
the collector exists to guarantee: it reads only the official sold-items API,
it says in plain words when the keys or the approval are the problem, it
writes nothing public that identifies a third party's listing, it keeps the
sale as evidence in the private store, and it adds drafts, never approvals.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import ebay_sold, identity, schema
from tests.test_eval_intake import fake_jpeg, stub_converter


def sale(item_id: str, **overrides) -> dict:
    raw = {
        "itemId": item_id, "title": f"Nike Air Max 97 Silver Bullet {item_id}",
        "lastSoldPrice": {"value": "142.50", "currency": "USD"},
        "lastSoldDate": "2026-10-01T18:22:11.000Z", "conditionId": "3000",
        "image": {"imageUrl": f"https://i.ebayimg.com/images/g/{item_id}/s-l1600.jpg"},
        "itemWebUrl": f"https://www.ebay.com/itm/{item_id}",
    }
    raw.update(overrides)
    return raw


class FakeEbay:
    """Answers the token, search and image requests; records what was asked."""

    def __init__(self, sales=None, token_status=200, token_body=None, search_status=200):
        self.sales = sales if sales is not None else [sale("111"), sale("222")]
        self.token_status = token_status
        self.token_body = token_body if token_body is not None else {"access_token": "t0k"}
        self.search_status = search_status
        self.searches: list[dict] = []
        self.urls: list[str] = []

    def post(self, url, headers=None, data=None):
        assert url == ebay_sold.API + ebay_sold.TOKEN_PATH
        assert data == {"grant_type": "client_credentials", "scope": ebay_sold.SCOPE}
        assert headers and headers["Authorization"].startswith("Basic ")
        return httpx.Response(self.token_status, json=self.token_body)

    def get(self, url, headers=None, params=None):
        self.urls.append(url)
        if url == ebay_sold.API + ebay_sold.SEARCH_PATH:
            assert headers and headers["Authorization"] == "Bearer t0k"
            self.searches.append(dict(params or {}, market=headers["X-EBAY-C-MARKETPLACE-ID"]))
            return httpx.Response(self.search_status, json={"itemSales": self.sales})
        assert url.startswith("https://i.ebayimg.com/")
        return httpx.Response(200, content=fake_jpeg(1600, 1200, url.encode()))


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setattr(identity, "load_env", lambda: None)
    monkeypatch.setenv("EBAY_CLIENT_ID", "client-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "client-secret")


@pytest.fixture
def gold(tmp_path: Path) -> Path:
    (tmp_path / "images" / ".git").mkdir(parents=True)      # the private clone
    return tmp_path / "gold.jsonl"


ONE_QUERY = ["--plan", "shoes|Nike|Air Max 97|nike air max 97", "--per-query", "5"]


def run(ebay: FakeEbay, gold: Path, *args: str) -> tuple[int, str]:
    out = io.StringIO()
    code = ebay_sold.main(["--gold", str(gold), *args], get=ebay.get, post=ebay.post,
                          convert=stub_converter([]), out=out)
    return code, out.getvalue()


class TestCollect:
    def test_sold_listings_become_high_drafts(self, gold):
        code, out = run(FakeEbay(), gold, *ONE_QUERY)
        assert code == 0, out
        items = schema.load_gold(gold)
        assert [i.id for i in items] == ["G-0001", "G-0002"]
        first = items[0]
        assert first.review_state is schema.ReviewState.DRAFT
        assert first.label_confidence is schema.LabelConfidence.HIGH
        assert (first.actual_sale_price, first.currency, first.region) == (142.5, "USD", "US")
        assert (first.category, first.brand, first.model) == ("shoes", "Nike", "Air Max 97")
        assert first.condition == "used"
        assert first.sold_date and first.sold_date.isoformat() == "2026-10-01"
        assert first.difficulty is schema.Difficulty.EASY
        assert {"ebay", "listing_photo"} <= set(first.tags)
        assert first.evidence_note == "private:ebay/111.json"
        assert "Added 2 draft(s)" in out

    def test_the_public_file_never_names_the_listing(self, gold):
        run(FakeEbay(), gold, *ONE_QUERY)
        text = gold.read_text()
        assert "ebay.com/itm" not in text and "Silver Bullet" not in text
        assert '"111"' not in text and "ebayimg" not in text

    def test_the_sale_is_kept_as_private_evidence(self, gold):
        run(FakeEbay(), gold, *ONE_QUERY)
        saved = json.loads((gold.parent / "images" / "ebay" / "111.json").read_text())
        assert saved["title"].startswith("Nike Air Max 97") and saved["itemId"] == "111"
        assert (gold.parent / "images" / "G-0001-front.jpg").is_file()

    def test_unusable_sales_are_left_out(self, gold):
        sales = [sale("1"), sale("2", lastSoldPrice=None), sale("3", image={}),
                 sale("4", lastSoldDate=None), sale("5", lastSoldPrice={"value": "0",
                                                                         "currency": "USD"})]
        code, out = run(FakeEbay(sales=sales), gold, *ONE_QUERY)
        assert code == 0
        assert len(schema.load_gold(gold)) == 1
        assert "5 sold, 1 usable" in out

    def test_a_rerun_adds_nothing_twice(self, gold):
        run(FakeEbay(), gold, *ONE_QUERY)
        _, out = run(FakeEbay(), gold, *ONE_QUERY)
        assert len(schema.load_gold(gold)) == 2
        assert "Added 0 draft(s); 2 already in the set" in out

    def test_the_marketplace_sets_the_region_and_header(self, gold):
        ebay = FakeEbay(sales=[sale("9", lastSoldPrice={"value": "80", "currency": "EUR"})])
        run(ebay, gold, *ONE_QUERY, "--marketplace", "EBAY_DE")
        item = schema.load_gold(gold)[0]
        assert (item.region, item.currency) == ("DE", "EUR")
        assert ebay.searches[0]["market"] == "EBAY_DE"

    def test_only_the_official_api_and_its_images_are_requested(self, gold):
        ebay = FakeEbay()
        run(ebay, gold, *ONE_QUERY)
        hosts = {httpx.URL(u).host for u in ebay.urls}
        assert hosts == {"api.ebay.com", "i.ebayimg.com"}

    def test_dry_run_writes_nothing(self, gold):
        code, out = run(FakeEbay(), gold, *ONE_QUERY, "--dry-run")
        assert code == 0
        assert "Would add 2 draft(s)" in out
        assert not gold.exists()
        assert not (gold.parent / "images" / "ebay").exists()

    def test_it_refuses_without_the_private_store(self, tmp_path):
        code, out = run(FakeEbay(), tmp_path / "gold.jsonl", *ONE_QUERY)
        assert code == 1
        assert "not the private photo store" in out


class TestPlainErrors:
    def test_probe_says_the_keys_and_scope_work(self, gold):
        code, out = run(FakeEbay(), gold, "--probe")
        assert code == 0
        assert "can read eBay sold-items data" in out
        assert "lastSoldPrice" in out
        assert not gold.exists()

    def test_wrong_keys(self, gold):
        code, out = run(FakeEbay(token_status=401), gold, "--probe")
        assert code == 1 and "rejected the Client ID" in out

    def test_an_app_without_the_sold_data_scope(self, gold):
        ebay = FakeEbay(token_status=400, token_body={"error": "invalid_scope"})
        code, out = run(ebay, gold, "--probe")
        assert code == 1 and "not approved for sold-items data" in out

    def test_search_refused(self, gold):
        code, out = run(FakeEbay(search_status=403), gold, "--probe")
        assert code == 1 and "no Marketplace Insights access" in out

    def test_missing_keys(self, gold, monkeypatch):
        monkeypatch.delenv("EBAY_CLIENT_ID")
        code, out = run(FakeEbay(), gold, "--probe")
        assert code == 1 and "EBAY_CLIENT_ID and EBAY_CLIENT_SECRET are not set" in out

    def test_secrets_are_never_printed(self, gold):
        _, out = run(FakeEbay(), gold, *ONE_QUERY)
        assert "client-secret" not in out and "t0k" not in out

    def test_a_malformed_plan_entry(self, gold):
        code, out = run(FakeEbay(), gold, "--plan", "shoes|Nike")
        assert code == 2 and "category|brand|model|query" in out
