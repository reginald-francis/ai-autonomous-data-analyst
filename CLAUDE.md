# CLAUDE.md

Guidance for AI coding agents in this repo. This is the short briefing: `PHASES.md` holds the
plan, locked decisions, risk register and cost limits; `docs/PHASE6_DESIGN.md` holds the
data-platform decisions, ingestion design and real-data measurements.

## Status

**Phases 1-6 are done.** Phase 6 (SEC EDGAR ingestion into bronze Parquet) is complete on branch
`claude/v8-ingestion`, PR to `main` pending. **Next: Phase 7** — Spark + Delta Lake (silver/gold),
branch `claude/v9-spark-lakehouse`, not yet cut. Work one phase per chat session; do not re-plan or
re-litigate settled decisions.

This is a **Data Engineering portfolio project** (monetization dropped 2026-08-24). A hand-rolled
multi-agent system — the *serving layer*, live on Cloud Run at
`https://agent-1079929934435.us-central1.run.app` — sits on a data platform:

```
SEC EDGAR Financial Statement Data Sets
  -> Python + pyarrow ingestion -> bronze Parquet      (Phase 6, done)
  -> PySpark + Delta Lake -> silver, gold              (Phase 7)
  -> BigQuery, gold layer only -> agents query it      (Phases 7-8)
```

dbt was dropped in favor of PySpark + Delta Lake. **Do not rewrite the planner, complexity
routing or retry loops into LangChain or any framework** — LangGraph is added *alongside* in
Phase 11 behind a config flag, never as a replacement.

## Working agreements

- **One file per message.** Edit one file, say what it is for and what each function/change does
  (high level), then wait for the owner's go-ahead before the next file. Don't paste whole files
  into chat for approval: make the edit, then summarize it. A "go ahead"/"next" covers that one file.
- **Don't change files the owner hasn't asked you to change.** Reading and exploring with the
  file tools needs no confirmation. Confirm the current branch before touching code (ask the
  owner for `git branch --show-current`).
- **The owner is new to pyarrow and PySpark.** Explain the concept before the code, keep steps
  small enough to follow, and discuss a phase's purpose, tools, cost and alternatives (with your
  own recommendation) before implementing it.
- **Verify against real data and sources, not memory or docs.** The SEC's real files contradicted
  its own readme; only a real run caught it.
- **Never remove existing code comments** unless asked (update one only when it has become false,
  and say so).
- **Never run shell commands yourself — not bash, not PowerShell.** Give the owner exact
  PowerShell commands; they run them and paste back the output. Use the file tools (read, search,
  edit) for everything else.
- **Before every commit the owner runs what CI runs** (see Commands). One commit per step; push via SSH, not
  HTTPS (PATs caused repeated 403s). Messages: short subject, then brief bullets, one per
  distinct change, no file-by-file breakdown; prefixes `feat`/`fix`/`docs`/`chore`/`refactor`.
- **Test file basenames must be unique across `tests/`.** There are no `__init__.py` files, so
  pytest imports each file under its bare name and a duplicate breaks collection.
- **Zero-cost mandate:** every component stays inside a free tier (cost table in `PHASES.md`);
  every BigQuery query needs `maximum_bytes_billed`.
- **Keep this file and `PHASES.md` updated** after every significant change or phase completion.

## Commands (PowerShell)

For the owner to run — agents hand these over and never execute them.

```powershell
.\venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt     # runtime + ingestion + pytest/ruff
# .env: GROQ_API_KEY=...   and for ingestion: SEC_USER_AGENT="Your Name your.email@example.com"

uvicorn src.main:app --reload           # run the app
```

**Checks — these mirror CI** (ruff, then tests with the 70% coverage gate; Python 3.11 and 3.13):

```powershell
.\venv\Scripts\ruff.exe check .
.\venv\Scripts\python.exe -m pytest -m "not live_api" --cov=src --cov=ingestion --cov-report=term-missing --cov-fail-under=70
.\venv\Scripts\python.exe -m pytest -m live_api    # real Groq calls, needs a real key
```

**Ingestion** (needs `SEC_USER_AGENT`; exit code 0 ok, 1 a quarter failed, 2 bad usage/config):

```powershell
python -m ingestion --source sec_fsds --quarter 2026q2
python -m ingestion --source sec_fsds --from 2024q1 --to 2026q2 --check    # --check never downloads; --force rebuilds
```

**Other:** `python src/groq_all_models.py` lists the Groq models actually live — run it before
trusting any model ID, since Groq retires them on a short cycle. The API (`POST /upload` with
`file` + `question`, optionally `context_file`; follow-ups reuse `session_id`) is documented at
`/docs`; `POST /ask` is disabled by default (`ENABLE_ASK_ENDPOINT`).

**Dependencies:** `requirements.txt` is runtime-only (the Docker image installs just it).
`requirements-ingestion.txt` is self-contained for a future ingestion container and pulled into dev
via `-r`. Verify dependency changes with a fresh venv install, not `pip show` on an old venv — a
drifted venv once hid a missing `huggingface-hub`.

## Serving layer (`src/`)

A FastAPI multi-agent system that answers natural-language questions about a CSV using
LLM-generated code. `POST /upload` validates and saves the CSV (≤10MB) as
`data/uploads/{session_id}.csv`, creates a session (30-min sliding TTL), and calls
`analyst_service.analyse()`; follow-ups send `session_id` and no file.

**Agents** (`src/agents/`, all injectable via `client=`; `analyst_service.build_agents()` makes
fresh instances per request):
- **PlannerAgent** — two calls: routing (agents + task type), then complexity (low/medium/high) at
  `temperature=0.0`. Complexity is capped at `medium` when `row_count < 500`.
- **PythonAgent** — generates and runs pandas code, retrying with the error. Its `execute_code()`
  is the only `exec()`: restricted builtins, an AST pre-check (no imports, no dunder access) and a
  best-effort timeout — defense in depth, not a hard sandbox (`docs/THREAT_MODEL.md`).
- **SQLAgent** — CSV to SQLite via `database_service`, generates and runs SQL (column names
  quoted), same retry logic.
- **ChartAgent** — the LLM only picks a small JSON chart spec; hand-written code renders the PNG
  to `data/charts/{uuid}.png`, served at `/charts/{uuid}.png` (`chart_url`).

**Services** (`src/services/`):
- `analyst_service` — orchestration. `python` and `sql` run independently if both are named; a
  chart-only plan gets `python` added first.
- `session_service` — in-memory sessions; cleanup every 5 min also deletes a session's CSV, DB,
  charts and RAG index.
- `rag_service` — per-session RAG over an optional uploaded `context_file` (glossary, terminology).
  Sessions without one get `""` — nothing changes. Embeddings are a quantized ONNX
  `all-MiniLM-L6-v2` in `models/`, loaded lazily on first RAG use; no index is built at startup.
- `data_context_service` — dataset-agnostic context (counts, dtypes, categorical values, ranges).
- `llm_service` — lazy `get_llm_client()` and model routing from settings.

`AnalysisResponse` (`src/utils/schemas.py`): `question`, `result`, `status`, `attempts`,
`time_taken`, `model_used`, `row_count`, `column_count`, `file_name`, `timestamp`, `agents_used`,
`task_type`, `reasoning`, `chart_path`, `chart_url`, `session_id`.

**Configuration:** `src/config.py` (`pydantic-settings`, cached `get_settings()`) owns model IDs,
retry budget, the 500-row threshold, TTLs, upload limits, paths and RAG settings; every field is
overridable by env var or `.env`. Only two free-tier Groq text models exist
(`openai/gpt-oss-20b`, `openai/gpt-oss-120b`), so **complexity tiering means more than model
choice**: it drives the model, the retry budget, and — for `high` only — reasoning-scaffolding
prompt instructions. Groq free tier: 30 RPM, 1k RPD, **8K TPM**, 200k TPD.

## Data platform (`ingestion/`)

A separate deployable: its own config, its own requirements file, nothing imported from `src/`.

```
__main__.py -> cli.py            flags -> pipeline calls -> printed results + exit code
config.py                        SEC_USER_AGENT (required), INGESTION_LAKE_ROOT (default data/lake),
                                 retry settings; loads .env
storage.py                       lake paths + atomic manifest read/write
sources/sec_fsds.py              URL building, quarters_in_range, streamed atomic download with
                                 retry, HEAD-request pre-check (needs_download), sha256
sources/sec_fsds_schema.py       the four table schemas, transcribed from the SEC's field
                                 definitions, plus SCHEMA_VERSION
sources/sec_fsds_bronze.py       write_bronze(): ZIP -> typed Parquet + manifest
sources/sec_fsds_pipeline.py     ingest_quarter / check_quarter / run_ingest / run_check
```

Bronze lives at `data/lake/bronze/sec_fsds/<table>/quarter=<q>/part-0.parquet`, with manifests in
`_manifests/`, scratch space in `_staging/`, and the raw ZIP kept in `data/lake/landing/`.

**Invariants — don't break these:**
- **The manifest is the commit marker.** It is written last, after all four tables are in place,
  so a failed rebuild never corrupts the last good build.
- **A quarter is skipped, rebuilt from the saved ZIP, or downloaded** — in that order of
  preference — using the SEC's `Last-Modified` (it sends no `ETag`) and the manifest. The ZIP's
  sha256 is the final authority.
- **The SEC's files are CSV-style quoted** (tabs, quotes and newlines inside values), whatever
  its readme says. **Only an empty field is NULL** (`"NA"` is Namibia). Row counts are checked
  with a second, quote-aware parser (Python's `csv`).
- **Types are explicit, never inferred.** `cik`/`sic`/`ein` are strings on purpose (leading
  zeros); bump `SCHEMA_VERSION` when any type changes.
- **Expected failures** (`BronzeError`, `requests.RequestException`) become a "failed" quarter and
  the range continues; anything else is a bug and crashes loudly.
- Tests build synthetic ZIPs in code and mock all HTTP — no network, no real SEC data.

## Data and generated files

- `sample_data.csv` — TechMart Electronics sales, 12 rows. Test fixtures in `tests/data/`
  (`large_sales`, `employees`, `stocks`, `missing_values`, `special_chars`, `empty`).
- No RAG corpus is checked in; RAG content is user-supplied at runtime.
- Generated and gitignored: `data/*.db`, `data/charts/`, `data/uploads/`, `data/lake/`.
- `tests/TEST_RESULTS.md` is a historical manual-testing record from before the Groq model
  retirement; the pytest suite supersedes it.

## Known issues

Full ranked register: `PHASES.md`. The ones most likely to bite:
- **The serving pipeline is file-path-shaped:** `python_agent` assumes one in-memory dataframe,
  which breaks at warehouse scale — the deepest change ahead (Phase 8). Also: up to 6 redundant
  `pd.read_csv` calls per request.
- **`exec()` timeout is thread-based** and can't reclaim a hung snippet's CPU/memory the way a
  subprocess kill could (`docs/THREAT_MODEL.md`).
- **Ingestion gaps, tracked as risks #28-#32:** the lake is local-only (`gs://` needs a
  write-then-upload step), no request throttle, no automatic quarter selection (a 404 for an
  unpublished quarter is a failure today), no run locking, and a 128 KB csv field limit.

## History

One line per phase; the full write-ups are in `PHASES.md`.
1. Revived the dead app: models remapped to `gpt-oss`, bugs fixed, first tests.
2. `src/config.py`, lazy clients, injectable agents.
3. Integration tests with a `FakeGroqClient`, GitHub Actions CI, two orchestration bugs fixed
   (`docs/BUGS_FOUND.md`).
4. Dynamic data context (the app no longer assumes TechMart data); chart-spec redesign; complexity
   scaffolding; the global RAG docs removed.
4b. Per-session RAG from a user-uploaded context document.
5. Docker + Cloud Run: `/ask` hardened and disabled, `exec()` sandboxed, ONNX embeddings, 211 MB
   image, deployed.
6. SEC ingestion: CLI, pipeline, explicit schemas, atomic bronze writer, measured on a real
   quarter (2026q2: 7,714 filings, 3.6M figures, 58 MB zipped, 53 s to rebuild).
