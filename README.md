---
title: Metadata Matcher
emoji: 🧭
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

# Mini Metadata Matcher

Scans a database (PostgreSQL, MySQL/MariaDB or CSV files), uses an LLM to draft metadata, validates everything in code,
scores how much to trust each item, and lets a data steward approve it. Approved items are exported, and everything is
available as JSON through a REST API.

**The LLM suggests, the code verifies, a person approves.**

Needs: Python 3.11+ and a database to scan (PostgreSQL 16 for the bundled example, or just use the sample CSV files). No Docker.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
createdb energy_grants
python seed_data.py                 # applies schema.sql and loads synthetic data (13 tables)
python -m mm scan                   # Bronze (harvest, profile) then Silver (LLM metadata, validation, trust)
streamlit run app.py                # review and approve; then export with:  python -m mm export
```

No PostgreSQL? Scan the bundled CSV files instead: `python -m mm scan --source csv://sample_data/csv`

## Sources (connectors)

Choose the database with `--source URL` or `MM_SOURCE_URL`. With neither, PostgreSQL via `MM_SOURCE_DSN` (the original behaviour).

| Source | URL | Notes |
|---|---|---|
| PostgreSQL | `postgresql://user:pw@host/db` or `dbname=energy_grants` | Reads `information_schema` and `pg_catalog` |
| MySQL / MariaDB | `mysql://user:pw@host:3306/db` | `tinyint(1)` is treated as boolean. MariaDB stores JSON as text, which is accepted. |
| CSV | `csv://folder` or a path to one `.csv` | Each file becomes a table. Types and primary keys are inferred (see DESIGN.md). |

Seed MySQL or MariaDB with the same example data: `MM_SOURCE_URL=mysql://user:pw@host:3306/db python seed_data.py`.
All connections are read-only. Passwords are hidden in exports and in the UI.

## Bronze, Silver, Gold and trust

- **Bronze:** harvested facts (catalogue and profile). `python -m mm harvest` runs Bronze alone: no LLM, no cost.
- **Silver:** AI-written metadata, each item with a trust score. `python -m mm scan` runs Bronze then Silver.
- **Gold:** approved or edited by a person. With `MM_AUTOPROMOTE=1`, High-trust items also reach Gold and Medium-trust items reach Gold (flagged).
- **Trust** blends five checks: matches the data, source and lineage, fits the glossary, model consistency, human approval. Hover a tier chip to see them.

## REST API

```bash
MM_API_KEY=my-secret python -m mm serve --port 8000         # docs at http://127.0.0.1:8000/docs
curl -H 'X-API-Key: my-secret' -X POST localhost:8000/v1/generate -H 'Content-Type: application/json' -d '{"asset": "ppl.nino"}'
```

| Endpoint | Does |
|---|---|
| `GET /v1/health` | Liveness (no key needed) |
| `POST /v1/generate` | `{asset: 'table.column'}` or `{asset: 'table'}`. Returns the stored item, or generates it (`refresh: true` forces it). |
| `POST /v1/scan` | Runs a full scan and returns the summary (synchronous) |
| `GET /v1/assets` | List. Filters: `kind`, `tier`, `level`, `flagged`, `limit`, `offset` |
| `GET /v1/assets/{asset}` | One item. Optional `?kind=` when names clash |
| `POST /v1/assets/{asset}/approve` or `/reject` | Changes the status (approved items become Gold) |
| `GET /v1/tiers` | Counts per tier |
| `GET /v1/model` | The approved semantic model (the YAML content as JSON) |

Every asset carries `tier`, `flagged`, `status`, `source`, `description`, `classification` (`personal_data` or `none`), `glossary_term` and
`trust {score, level, checks {data_match, lineage, glossary, consistency, human_approval}}`.
Every endpoint except health needs `X-API-Key`. If `MM_API_KEY` is not set the API answers 503, so it is never open by accident.

## The UI (8 screens, light theme)

Scan overview (counts, tiers, cost, rescan), Browse (tables, columns and column detail in one split view), Tiers (Bronze, Silver and Gold lanes with the
five trust checks as bars, approve from the lane), Review queue (lowest first, bulk approve), Relationship graph, Glossary, Metrics, Sources (test a connection, run Bronze or a full scan against any source).
The sidebar switches the badge score between Trust and Confidence and sets the High and Medium thresholds.

## Commands

| Command | Does |
|---|---|
| `python -m mm harvest [--source URL]` | Bronze only |
| `python -m mm scan [--source URL] [--provider mock\|bedrock\|anthropic] [--consistency N] [--chaos]` | Bronze then Silver |
| `python -m mm approve --min 0.85 [--by confidence\|trust]` | Bulk approve |
| `python -m mm export [--out FILE] [--calls FILE]` | Approved items to YAML, and the LLM call log |
| `python -m mm status` | Counts per kind, status and tier |
| `python -m mm serve [--port 8000]` | REST API |

## Settings (environment variables)

| Variable | Default | Effect |
|---|---|---|
| `MM_SOURCE_URL` | empty | Database to scan (see Sources) |
| `MM_SOURCE_DSN` | `dbname=energy_grants` | PostgreSQL DSN used when `MM_SOURCE_URL` is empty |
| `MM_MODEL_DB` | `model.db` | SQLite file holding the model |
| `MM_PROVIDER` | `mock` | LLM provider. Bedrock needs `MM_BEDROCK_MODEL`; Anthropic needs `ANTHROPIC_API_KEY` |
| `MM_API_KEY` | none | Key required by the API |
| `MM_AUTOPROMOTE` | off | Let High and Medium trust items reach Gold without approval |
| `MM_HIGH`, `MM_MEDIUM` | 0.85, 0.6 | Badge thresholds |
| `MM_MIN_OVERLAP` | 0.95 | Relationship inference threshold |
| `MM_MAX_ATTEMPTS` | 3 | LLM tries per table |
| `MM_PRICE_IN_PER_M`, `MM_PRICE_OUT_PER_M` | 3.0, 15.0 | Placeholder token prices, set your model's |

## Tests

`pytest -q`. LLM tests use the mock and need no key. Database tests skip themselves when PostgreSQL is unreachable.
To include the MySQL test: `MM_TEST_MYSQL_URL=mysql://user:pw@host:3306/db pytest -q` (database seeded with `seed_data.py`).

## Layout

```
schema.sql, seed_data.py          example database (PostgreSQL or MySQL)
sample_data/csv/                  three sample CSV files
mm/connectors/                    base interface and dialects, postgres, mysql, csv_source
mm/harvest.py, profile.py         catalogue types, column profiling (dialect-neutral)
mm/masking.py                     personal-data detection and masking
mm/llm.py, mock_llm.py            LLMProvider interface and call log
mm/generate.py, validate.py       prompts, validation, retries
mm/confidence.py, trust.py        evidence score and the five-check trust score
mm/tiers.py                       Bronze, Silver, Gold
mm/relationships.py, glossary.py, metrics.py, entities.py     semantic layer
mm/pipeline.py, store.py, export.py, cli.py                   orchestration and storage
mm/api.py                         REST API
app.py, .streamlit/config.toml    UI and light theme
tests/                            pytest suite
```
