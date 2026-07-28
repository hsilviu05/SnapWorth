"""SQLite FTS5 index over the brand/model catalog.

Scope — and a deliberate omission
---------------------------------
This indexes the **catalog** (brands, aliases, model lines, categories). It does
**not** index user scans, and that is not an oversight.

`/privacy` states, and the App Store privacy disclosure repeats:

    "Photos and scan results are processed in real time and are not retained on
     our servers. Scan history is stored locally on your device."

A server-side searchable index of previous scans would require retaining scan
results server-side, directly contradicting that. It would also convert an
anonymous, device-keyed service into one holding a per-user history — a
materially different GDPR posture requiring a new lawful basis, a new retention
schedule, and DSAR machinery none of which exists today.

So **user-scan search belongs on-device**, over the existing SwiftData store.
The design is in docs/COMPS-ARCHITECTURE.md §"Scan search (on-device)". It is a
better product decision anyway: the data is already local, so search is instant,
works offline, and needs no round-trip.

What this module is genuinely for
---------------------------------
Brand/model resolution at scale. `catalog.Catalog` does exact and alias lookup
against a dict, which is right for a few hundred brands. As the catalog grows
into thousands of model lines, prefix and token search over an FTS5 index is
what keeps "patagoina better sweater 1/4 zip" resolving to a canonical
brand + model, which is what makes a comps query precise.

FTS5 with the `unicode61` tokenizer, `prefix` indexes for typeahead, and BM25
ranking. In-memory by default — the catalog is small, static and rebuilt at
startup, so there is nothing to persist and no migration to manage.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass

from comps import normalize
from comps.catalog import Brand, Catalog, catalog as default_catalog

log = logging.getLogger("snapworth.comps.search")


class SearchUnavailable(Exception):
    """FTS5 is not compiled into this SQLite build."""


def fts5_available() -> bool:
    """Whether the runtime SQLite supports FTS5.

    Checked rather than assumed: FTS5 is optional at compile time and absent
    from some minimal Python/SQLite builds, including certain slim container
    images. Callers degrade to `Catalog`'s dict lookup rather than failing.
    """
    try:
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE VIRTUAL TABLE _probe USING fts5(x)")
            return True
        finally:
            connection.close()
    except sqlite3.OperationalError:
        return False


@dataclass(frozen=True)
class SearchHit:
    canonical: str
    kind: str                  # "brand" | "model"
    brand: str
    score: float               # higher is better
    tier: str = "unknown"
    categories: tuple[str, ...] = ()


# FTS5 treats these as query syntax; a raw user string containing them raises.
_FTS_SPECIALS = re.compile(r'[\"\'(){}\[\]^*:~\-+]')


def _sanitise_query(raw: str) -> str:
    """Make arbitrary text safe as an FTS5 MATCH expression.

    Not a SQL-injection guard — parameters handle that — but FTS5 has its own
    expression grammar, and an unescaped quote or `-` is a syntax error rather
    than a search for that character.
    """
    cleaned = _FTS_SPECIALS.sub(" ", raw or "")
    tokens = [t for t in normalize.normalise_text(cleaned).split() if t]
    return " ".join(tokens)


class CatalogSearch:
    """FTS5-backed brand and model search."""

    def __init__(self, catalog: Catalog | None = None, *, path: str = ":memory:") -> None:
        if not fts5_available():
            raise SearchUnavailable("SQLite was built without FTS5")
        self._catalog = catalog or default_catalog
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._build()

    # ── Index construction ───────────────────────────────────────────────────

    def _build(self) -> None:
        cursor = self._connection.cursor()
        cursor.executescript(
            """
            DROP TABLE IF EXISTS catalog_fts;
            CREATE VIRTUAL TABLE catalog_fts USING fts5(
                terms,              -- searchable: canonical + aliases + models
                canonical UNINDEXED,
                kind      UNINDEXED,
                brand     UNINDEXED,
                tier      UNINDEXED,
                categories UNINDEXED,
                tokenize = 'unicode61 remove_diacritics 2',
                prefix = '2 3 4'    -- typeahead without a scan
            );
            """
        )

        rows: list[tuple] = []
        for brand in self._catalog.brands:
            rows.append(self._brand_row(brand))
            for model in brand.models:
                rows.append(self._model_row(brand, model))

        cursor.executemany(
            "INSERT INTO catalog_fts "
            "(terms, canonical, kind, brand, tier, categories) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._connection.commit()
        log.info("catalog search index built", extra={"rows": len(rows)})

    def _brand_row(self, brand: Brand) -> tuple:
        terms = " ".join(
            normalize.normalise_text(t)
            for t in {brand.canonical, *brand.aliases}
        )
        return (
            terms, brand.canonical, "brand", brand.canonical,
            brand.tier.value, ",".join(sorted(brand.categories)),
        )

    def _model_row(self, brand: Brand, model: str) -> tuple:
        # Include the brand in a model row's terms so "nike air max" matches the
        # model rather than only the brand.
        terms = normalize.normalise_text(f"{brand.canonical} {model}")
        return (
            terms, model, "model", brand.canonical,
            brand.tier.value, ",".join(sorted(brand.categories)),
        )

    # ── Query ────────────────────────────────────────────────────────────────

    def search(self, raw: str, *, limit: int = 10, prefix: bool = False) -> list[SearchHit]:
        """Rank catalog entries against `raw` using BM25.

        `prefix=True` appends a `*` to the final token for typeahead. BM25
        returns lower-is-better, so it is negated into a conventional score.
        """
        query = _sanitise_query(raw)
        if not query:
            return []
        if prefix:
            tokens = query.split()
            tokens[-1] = f"{tokens[-1]}*"
            query = " ".join(tokens)

        try:
            rows = self._connection.execute(
                """
                SELECT canonical, kind, brand, tier, categories,
                       bm25(catalog_fts) AS rank
                FROM catalog_fts
                WHERE catalog_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            # A malformed expression that survived sanitisation. Return nothing
            # rather than propagating — search is an enhancement, not a gate.
            log.debug("catalog search query rejected: %s", exc)
            return []

        return [
            SearchHit(
                canonical=row["canonical"],
                kind=row["kind"],
                brand=row["brand"],
                score=round(-float(row["rank"]), 4),
                tier=row["tier"],
                categories=tuple(c for c in (row["categories"] or "").split(",") if c),
            )
            for row in rows
        ]

    def best_brand(self, raw: str) -> str | None:
        """Highest-ranked brand for a free-text string."""
        for hit in self.search(raw, limit=5):
            if hit.kind == "brand":
                return hit.canonical
        hits = self.search(raw, limit=1)
        return hits[0].brand if hits else None

    def suggest(self, prefix_text: str, *, limit: int = 8) -> list[str]:
        """Typeahead suggestions."""
        seen: list[str] = []
        for hit in self.search(prefix_text, limit=limit * 2, prefix=True):
            label = hit.canonical if hit.kind == "brand" else f"{hit.brand} {hit.canonical}"
            if label not in seen:
                seen.append(label)
        return seen[:limit]

    def close(self) -> None:
        self._connection.close()


def build_search(catalog: Catalog | None = None) -> CatalogSearch | None:
    """Construct the index, or None when FTS5 is unavailable.

    Returning None rather than raising lets callers fall back to `Catalog`'s
    dict lookup, which is correct for the current catalog size anyway.
    """
    try:
        return CatalogSearch(catalog)
    except SearchUnavailable as exc:
        log.warning("catalog search disabled: %s", exc)
        return None
