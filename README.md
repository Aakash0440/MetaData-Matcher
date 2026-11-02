# Mini Metadata Matcher

Scans a PostgreSQL database, uses an LLM to draft metadata, then builds a reviewed semantic layer
(entities, relationships, glossary, metrics). The LLM only suggests. Code validates everything and a
data steward approves before anything is exported.

Needs: Python 3.11+, PostgreSQL 16 running locally. No Docker.

## Run it (5 commands, empty database to exported model)

```bash
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
createdb energy_grants
python seed_data.py                 # applies schema.sql and loads synthetic data (13 tables)
python -m mm scan                   # harvest, profile, generate, validate, relate, glossary, metrics
streamlit run app.py                # review and approve in the UI, then export from the CLI:  python -m mm export
```

`python -m mm export` writes `semantic_model.yaml` (approved items only) and `llm_call_log.jsonl`.
Headless shortcut for a demo: `python -m mm approve --min 0.85` approves everything at or above 0.85.

## LLM provider

The default is `mock`: offline, deterministic, no key. It is a heuristic, so it proves the controls, not
LLM quality. Use a real model for the submitted run.

| Provider | How |
|---|---|
| Bedrock | `pip install boto3`, then `MM_BEDROCK_MODEL=<model or inference profile id> python -m mm scan --provider bedrock` (region `AWS_REGION`, default eu-west-2, standard AWS credentials) |
| Anthropic API | `pip install anthropic`, `ANTHROPIC_API_KEY=...`, optional `MM_ANTHROPIC_MODEL`, then `python -m mm scan --provider anthropic` |

Cost is computed from `MM_PRICE_IN_PER_M` and `MM_PRICE_OUT_PER_M` (placeholder defaults, set your model's prices).
Other settings: `MM_SOURCE_DSN`, `MM_MODEL_DB`, `MM_HIGH`, `MM_MEDIUM`, `MM_MIN_OVERLAP`, `MM_MAX_ATTEMPTS`.
Useful flags: `--consistency 2` (repeat each table to measure agreement), `--chaos` (mock only: wrong first answers, to show validation and retries).

## Tests

`pytest` (36 tests). LLM tests use the mock and need no key. Metric SQL and rescan tests also need the seeded database and skip themselves if it is not reachable.

## Layout

```
schema.sql, seed_data.py     the source database
mm/harvest.py, profile.py    catalogue, row counts, null %, distinct, min/max, patterns, masked samples
mm/masking.py                personal-data detection and masking before anything reaches the LLM
mm/llm.py, mock_llm.py       one LLMProvider interface (Bedrock, Anthropic, mock) and the call log gateway
mm/generate.py, validate.py  prompts, Pydantic schemas, grounding checks, retries with feedback
mm/confidence.py             how confidence is calculated (see DESIGN.md)
mm/relationships.py          declared, inferred, link and polymorphic relationships
mm/glossary.py, metrics.py   shared terms; metric specs compiled to SQL and executed
mm/pipeline.py, export.py    scan orchestration and YAML export
app.py                       Streamlit steward UI (7 screens)
sample_output/               exported model and LLM call log from a MOCK run
```

## UI screens

Scan overview (counts, % approved, LLM calls and cost, rescan), Table browser (filters), Column detail (evidence next to
suggestion, edit, approve, reject, regenerate), Review queue (lowest confidence first, bulk approve above a threshold),
Relationship graph (solid = declared, dashed = inferred with confidence), Glossary (merge, rename, approve), Metrics (SQL, run, approve, add).
Confidence badges show High / Medium / Low as text plus colour; hover for the reasons. Thresholds are sliders in the sidebar.
