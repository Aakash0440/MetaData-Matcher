# Phase 1: all four stages are done

| Stage | Delivered |
|---|---|
| 1. Bronze, trust, tiers | `mm/trust.py`, `mm/tiers.py`, `harvest` command, `MM_AUTOPROMOTE`, tier and trust on every export |
| 2. REST API | `mm/api.py` (FastAPI), `serve` command, 8 endpoints, API-key auth, `tests/test_api.py` |
| 3. Connectors | `mm/connectors/` (interface, dialects, PostgreSQL, CSV), profiling and metrics made dialect-neutral, CSV type and key inference, `--source` and `MM_SOURCE_URL` |
| 4. MySQL, UI, polish | `mm/connectors/mysql.py`, portable metric SQL (window functions), light theme, Browse split view, Tiers lanes, Sources screen, Trust or Confidence badge switch |

## What changed from Stage 1 to the end

- `profile.py`, `metrics.py`, `pipeline.py`, `cli.py` and `app.py` now talk to a connector instead of psycopg2.
- The delta metric (average change between first and latest record) now uses `ROW_NUMBER()` instead of `array_agg`, so it runs on PostgreSQL, MySQL and SQLite.
- Metric filters with `in` use `IN (...)` lists; ordering no longer uses `NULLS LAST`.
- `validate.py` accepts the `json` type on text columns (MariaDB and SQLite store JSON as text).
- `store.bulk_approve` accepts a score function, so bulk approval can use trust. `approve --by trust` uses it.
- `seed_data.py` can seed MySQL or MariaDB.

## Tests

71 tests: the original 36, 9 from Stage 1, 8 API, 14 connector (including MariaDB), 4 UI smoke tests.
Without a MySQL URL the MySQL test skips and 70 pass.

## Known limits

- MySQL was tested against MariaDB 10.11, not MySQL 8. The catalogue queries and SQL are standard and should match, but run `MM_TEST_MYSQL_URL=... pytest` against your server first.
- Metrics and Regenerate in the UI use the default source (`MM_SOURCE_URL`), not the one typed on the Sources screen.
- Boolean filters on MySQL and CSV: write the value as 1 or 0, not `true`.
- CSV sources are held in memory, so very large files are not suitable.
- The API scan endpoint is synchronous, so a large scan holds the request open. One shared API key, no rate limiting.
- Auto-promotion changes tiers and API output but not the export, which still includes only approved items.
