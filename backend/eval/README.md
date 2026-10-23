# backend/eval

The evaluation platform is documented in [`docs/EVALUATION.md`](../../docs/EVALUATION.md).
This file covers the second-hand clothing eval and the attribution its
dataset requires.

## Second-hand clothing eval

Scores a running backend's `POST /scan` against a public dataset of
second-hand garments: brand, garment type and condition on `id_gold`, and
price on `price_silver`. It goes over HTTP, so it measures `/scan` as served
(`eval.runner` is the in-process harness for the gold set).

| Module | Does |
| --- | --- |
| `secondhand.py` | Reads the download in place (zip or folder), `profile`s it, `build`s the split manifests |
| `scanclient.py` | `/scan` client: concurrency, retries with backoff, timeouts, disk cache |
| `run.py` | `python -m eval.run`: sends each item, scores it, writes `results.json` and `report.md` |
| `metrics.py` | `brand_verdict` / `brand_accuracy`, `condition_error`, beside the existing price metrics |

### Setup

1. Download `clothing_v3.zip` (26.8 GB) from [Zenodo](https://zenodo.org/records/12518734).
   There is no need to unzip it; both the zip and the extracted folder work.
2. In `backend/.env` or the shell:
   ```sh
   EVAL_DATA_DIR=~/path/to/clothing_v3.zip
   EVAL_BACKEND_URL=http://127.0.0.1:8000
   EVAL_SCAN_TOKEN=            # optional: a Pro bearer token for the local server
   ```
3. Start the backend locally with the limits raised for the run, since every
   request without a token is the same free caller:
   ```sh
   FREE_SCANS_PER_DAY=100000 RATE_MAX_REQUESTS=100000 IP_RATE_MAX_REQUESTS=100000 \
     uvicorn main:app --port 8000
   ```

### Use

```sh
python -m eval.secondhand profile                     # what the download holds, price distribution
python -m eval.secondhand build                       # add --price-bands '<50' 50-100 to keep only some
python -m eval.run --split id_gold --limit 20         # dev half by default
python -m eval.run --split id_gold --subset test      # confirm a change; never tune on test
python -m eval.run --split price_silver
python -m eval.run --split price_gold                 # the approved sales in gold.jsonl
```

Each run writes `eval/data/secondhand/runs/<split>-<time>/results.json` and
`report.md`. Both folders, and the `cache/` beside them, are gitignored.

### Splits

- **`id_gold`**: about 500 items with a brand, type, category, condition and a
  front photo, sampled with a fixed seed and stratified by category × type.
  Each item is `dev` or `test` by a hash of its id. Despite the name it is not
  part of the gold set (`gold.jsonl`), which holds verified sales.
- **`price_silver`**: station3 (Myrorna) items with a price. Every station3
  price in v3 is a band (`<50`, `50-100`, `100-150`, `150-250`, `250-400`,
  `>400`, and one `150-200`), not a number, so each row keeps the band raw
  and as `price_low` / `price_high` (no high for `>400`), its currency marked
  `unconfirmed (likely SEK)`. Items with no price, a zero or an unreadable
  one are left out and counted in `price_silver.meta.json`.
- **`price_gold`**: the existing `gold.jsonl`, scored over HTTP. Its columns
  are `id`, `images[]`, `brand`, `category`, `condition`, `actual_sale_price`,
  `currency`, `marketplace`, `sold_date` and `evidence_note`; `eval.intake`
  is how a sale gets in.

Manifests in `eval/data/secondhand/` hold labels and paths inside the
download only: never image bytes, never the annotator field.

### What is sent and scored

- **Photos.** The front photo is sent as `file`, resized to the app's upload
  (1568 px long edge, JPEG quality 80). The brand photo is sent as `tag` only
  with a token (`--tag auto`), because `/scan` uses a tag photo only for a Pro
  caller. The back photo has no slot in `/scan` and is never sent.
- **Brand.** Exact after normalising (case, accents, punctuation, `&`,
  company suffixes), and fuzzy (word containment, a prefix of four or more
  letters, or a `difflib` ratio of 0.85 or more). "Unknown" counts as an
  abstention, reported apart. Split by whether the item has a brand photo.
- **Category.** The dataset's garment type mapped to the app's categories
  (`TYPE_CATEGORY` in `run.py`), compared with `category`. Close to trivial:
  nearly every garment is `clothing`.
- **Type.** Whether the scan's `item_name` or `model_name` names the dataset's
  garment type (`TYPE_WORDS`). Approximate; a type not in the table is not
  scored.
- **Condition.** `condition_grade` on the dataset's 1-5 scale (`new` 5,
  `likeNew` 4, `good` 3, `used` 2): MAE, exact, within one, and a confusion
  table.
- **Price, `price_silver`.** The scan's `likely_price_usd` (the range's
  middle when absent, counted) is converted to the band's currency at the
  item's annotation date from the pinned ECB table (`fx.py`) and compared
  with the band: the share inside it; the share in the right band of the
  scheme and within one band; the median band error (positive is priced
  high); how far outside the band it lands, as a percentage of the nearer
  edge (0 inside: the band's stand-in for MdAPE); whether the scan's range
  overlaps the band; the range width in USD; and a band confusion table.
  Overall and per garment type.
- **Price, `price_gold`.** MdAPE, the share of true prices inside
  `est_value_low_usd` to `est_value_high_usd`, and the range width, overall
  and per category.
- A group under 5 items shows its size and no figures.

Every item that is not scored is counted with its reason: a missing front or
brand photo, a malformed annotation or manifest line, no exchange rate, a
timeout, or the backend's error code.

### Known limitations

- **The price labels are not resale prices.** They are shelf-price bands set
  by a Swedish charity-shop chain, and their currency is assumed.
  `price_silver` is a sanity check, never a gate input. A band is coarse: a
  50-100 band cannot tell a good estimate of 60 from a poor one of 95.
- The exchange-rate table has to cover the station3 annotation dates
  (`profile` prints the first and last day); an item outside it is skipped
  and counted, never converted at today's rate.
- The backend has no garment-type field, so type accuracy is a keyword match.
- The 1-5 scale and the app's four grades do not line up; grade 1 has no
  counterpart.
- The back photo is never sent, about 4,000-5,000 brand photos were removed
  from the dataset for privacy, and the brand photo reaches the model only for
  a Pro caller.
- The photos are studio shots on a table or grid, not thrift-rack scans, and
  the brand mix is Nordic.
- The cache is keyed on the backend's commit. Uncommitted prompt edits on the
  local server reuse old answers: use `--no-cache` or `--cache-tag` while
  iterating.
- Each uncached item is one billed model call on the local server's key.

## Attribution

The second-hand clothing eval uses the **Clothing Dataset for Second-Hand
Fashion**, version 3, by Farrukh Nauman (RISE Research Institutes of Sweden),
with data collected by Wargön Innovation AB and Myrorna AB. Published on
Zenodo, 2024, [doi:10.5281/zenodo.12518734](https://doi.org/10.5281/zenodo.12518734),
under the [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/)
licence.

Changes made: the manifests in `eval/data/secondhand/` are a sample of the
dataset's annotations (item id, station, photo paths, brand, type, category,
condition and, for station3, price), reformatted as JSON Lines. No image from
the dataset is redistributed.
