# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## ⚠️ Read this first

**Phase 1 is complete** (model remap, pending bug fixes, first 78 automated tests, doc
rewrite). The app works again — models are `openai/gpt-oss-20b`/`openai/gpt-oss-120b`, not the
dead Llama IDs. **Phase 2 (config + DI refactor) is also complete** — `src/config.py`, a lazy
Groq client, injectable agent clients via `build_agents()`, and a lazy RAG index. **Phase 3
(API + integration tests, CI) is also complete** — integration tests for `/upload`/`/ask`,
two real bugs found and fixed (see `docs/BUGS_FOUND.md`), a live-API test file, and a GitHub
Actions CI workflow. **Phase 4 (dynamic data context) is complete** — dataset-agnostic
`data_context_service.py`, a chart-spec redesign, and complexity-tiered prompt scaffolding
replaced the old TechMart-hardcoded RAG docs. **Phase 4b (user-supplied RAG context) is also
complete** — RAG is now built per-session from an optional uploaded business-context document
instead of a global `docs/` folder; see the RAG section below. **Phase 5 (Docker + Cloud Run +
security) is in progress**, on branch `claude/v7-deploy` — security fixes and the ONNX
embedding swap are done; Dockerfile/Cloud Run deploy are not yet started. See the Current
State section below for details.

**Project direction changed on 2026-08-24.** This repo is no longer heading toward a monetized
SaaS product. It is now a **Data Engineering portfolio project**, targeting applications from
~April 2027. The existing multi-agent system becomes the *serving layer* on top of a real data
platform (ingestion → raw storage → dbt → BigQuery → data quality).

**Work one phase per chat session.** Read `PHASES.md` first — it is the source of truth for the
13-phase plan, current status, locked decisions, risk register, and cost limits. Do not re-plan
or re-litigate settled decisions.

**Current phase:** 4 — Dynamic data context · **Branch:** `claude/v6.3-data-context` (not yet
cut — still on `claude/v6.2-ci` as of this writing)

## Working agreements

- **Never change a file without asking first.** No exceptions. Read/run/explore commands don't
  need confirmation; edits do.
- **Always check the current branch and confirm it** before touching code.
- **Never remove existing code comments** unless explicitly asked.
- **Push via SSH**, not HTTPS (PATs caused repeated 403s).
- **Keep this file and `PHASES.md` updated** after every significant change or phase completion.

## Commands

**Setup**
```bash
source venv/Scripts/activate
pip install -r requirements-dev.txt  # pulls in requirements.txt too, plus pytest/ruff
# Create .env with: GROQ_API_KEY=your_key_here
```
(Phase 5 follow-up: `requirements.txt` is runtime-only now — the Docker image installs just
that file. `requirements-dev.txt` adds pytest/pytest-cov/pytest-mock/coverage/ruff/requests for
local dev and CI. Dropped `openai` and its dependency `jiter`, since nothing imports the
`openai` package anywhere in this repo — confirmed via `pip show`'s `Required-by:` before
removing. `typer`/`rich`/`shellingham` were removed on the same basis, then added back after a
fresh-venv install (see below) revealed they're real, needed dependencies after all —
`tokenizers` requires `huggingface-hub`, which in turn requires `typer` for its own CLI.
`requirements.txt` is now fully verified against a from-scratch `pip install`, not just
`pip show` on an already-populated venv, which is what caught this: the old venv had drifted
into a state where `huggingface-hub` was silently missing despite `tokenizers` declaring it as
required — `pip check` flagged this as an inconsistency, but the app still worked, since our
own code only calls `Tokenizer.from_file()`, never the part of `tokenizers` that needs the Hub
client. A truly fresh install — which is what CI and the Docker build both do — doesn't get to
rely on that kind of drift, so `requirements.txt` needed `huggingface-hub` added explicitly.)

**Run the server**
```bash
uvicorn src.main:app --reload
```

**Tests** (arriving in Phase 1 — there are currently zero `test_*.py` files)
```bash
pytest -m "not live_api"          # default: no real API calls
pytest -m live_api                # the 2-3 tests that hit Groq for real
pytest --cov=src --cov-report=term-missing
```

**Check which Groq models are actually live** — do this before trusting any model ID, as Groq
retires them on a short cycle:
```bash
python src/groq_all_models.py
```

**Test the API**
```bash
# Upload endpoint (multipart — for real users)
curl -X POST http://localhost:8000/upload \
  -F "question=What is total revenue?" \
  -F "file=@sample_data.csv"

# Follow-up question (reuse session, no re-upload)
curl -X POST http://localhost:8000/upload \
  -F "question=What is revenue by region?" \
  -F "session_id=<session_id_from_response>"

# Original endpoint (local file path — dev/testing only; has an unfixed path-traversal
# hole, addressed in Phase 5 before deploy)
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What is total revenue?", "file_path": "sample_data.csv"}'
```

## Architecture

A **FastAPI multi-agent data analysis system** that answers natural language questions about
CSV data using LLM-generated code execution. The hand-rolled planner, complexity routing, and
retry loops are the project's differentiator — **do not rewrite them into LangChain or any
framework.** LangGraph is added *alongside* in Phase 11 behind a config flag, never as a
replacement.

### Request Flow

```
POST /ask  (JSON, file_path) ─────────────────────────────────┐
                                                               ↓
POST /upload (multipart, file + question)             analyst_service.analyse()
  ↓                                                            ↓
  Validate file (CSV, ≤10MB, non-empty, parseable)    PlannerAgent (routes to one or more agents)
  ↓                                                            ↓
  Save to data/uploads/{session_id}.csv          PythonAgent | SQLAgent | ChartAgent
  ↓                                               (code generation + retry loop)
  session_service.create_session()                             ↓
  ↓                                                    AnalysisResponse (Pydantic)
  analyst_service.analyse()                            (includes session_id)
  ↓
  Return response with session_id

Follow-up: POST /upload (session_id + question, no file)
  ↓
  session_service.get_session() → reuse saved file_path
  ↓
  analyst_service.analyse()
```

### Agent Responsibilities

- **PlannerAgent** (`src/agents/planner_agent.py`): Two-call planner. Call 1 decides routing
  (agents + task_type). Call 2 classifies complexity (low/medium/high) at `temperature=0.0` for
  determinism. Complexity is capped at `medium` when `row_count < 500` —
  `HIGH_COMPLEXITY_ROW_THRESHOLD`.
- **PythonAgent** (`src/agents/python_agent.py`): Generates and executes pandas code. Captures
  stdout. On failure, sends the error back to the LLM to fix and retries.
- **SQLAgent** (`src/agents/sql_agent.py`): Converts CSV to SQLite via `database_service`,
  generates SQL, executes queries. Same retry logic.
- **ChartAgent** (`src/agents/chart_agent.py`): Generates matplotlib code, saves charts to
  `data/charts/{session_id}.png`. Receives prior analysis result as context.

### Key Services

- **`src/services/analyst_service.py`**: Main orchestration. Loads CSV, extracts metadata, calls
  PlannerAgent (passing `row_count`), dispatches to agents with `complexity` and `session_id`,
  returns a structured response. `python` and `sql` both run independently if the planner names
  both (fixed in Phase 3 — previously `elif`-chained, so `sql` was silently dropped; results are
  now labeled and concatenated). A chart-only plan is coerced to include `python` before
  dispatch, since ChartAgent has nothing to chart without a prior computed result.
- **`src/services/session_service.py`**: In-memory session store. Maps `session_id` →
  `{file_path, original_filename, created_at, last_accessed}`. TTL 30 minutes. Background
  cleanup every 5 minutes; startup sweep removes orphans.
- **`src/services/rag_service.py`**: Per-session RAG (Phase 4b) — no global index, no startup
  build. `POST /upload` accepts an optional `context_file` (plain-text/Markdown business
  context a developer could never pre-write, e.g. column glossaries, terminology, fiscal
  calendar quirks). If provided, `build_session_index(session_id, text)` chunks and embeds it
  into a `RagIndex` scoped to that session only, stored in a module-level
  `{session_id: RagIndex}` dict; `retrieve_session_context(session_id, question)` returns ""
  for any session that never uploaded one (the backward-compat guarantee — omitting
  `context_file` changes nothing). `drop_session(session_id)` is called from
  `session_service`'s TTL/cleanup path so a session's RAG index dies alongside its CSV and DB
  files, on the same sliding TTL. The old global `docs/`-folder RAG (Phase 1-4) was removed
  entirely once its one real content file, `routing_rules.txt`, turned out to duplicate rules
  already hardcoded in agent prompts — see Phase 4's note and Phase 4b's rationale in
  `PHASES.md`.
- **`src/services/llm_service.py`**: Groq client, `DEFAULT_MODEL`, `MODEL_ROUTING`, and
  `get_model_for_complexity()`. The client is now constructed lazily via a `@lru_cache`d
  `get_llm_client()` (Phase 2) — no client is built at import time. Model routing values are
  read from `src/config.py`'s `Settings`.
- **`src/services/database_service.py`**: On-demand CSV → SQLite conversion. DB saved as
  `data/{session_id}_{table_name}.db`.

### Response Schema (`src/utils/schemas.py`)

`AnalysisResponse` includes: `question`, `result`, `status`, `attempts`, `time_taken`,
`model_used`, `row_count`, `column_count`, `file_name`, `timestamp`, `agents_used`, `task_type`,
`reasoning`, `chart_path`, `session_id`.

## Configuration

**Centralized in `src/config.py`** (Phase 2) — a `pydantic-settings` `Settings` class, accessed
via the process-cached `get_settings()`. Owns model IDs, retry budget, the 500-row complexity
threshold, session TTL, max CSV/context-doc upload sizes, data/upload/chart paths, the
embedding model name, and RAG `top_k`/distance threshold. Every field is overridable via an env
var or `.env` entry of the same name (uppercased); defaults match the values that were
previously hardcoded, so no `.env` changes are required after this refactor.

**Model routing — remapped in Phase 1.** The three previously configured Llama IDs are gone
from Groq. The only usable free-tier text models are `openai/gpt-oss-20b` (fast/cheap) and
`openai/gpt-oss-120b` (strongest). Because two tiers must share one model, **tiering means more
than model identity**: complexity drives model *and* retry budget (`Settings.retry_budget`)
*and*, for `high` complexity only, reasoning-scaffolding prompt instructions in
`python_agent.py`/`sql_agent.py` (`HIGH_COMPLEXITY_SCAFFOLDING`/`HIGH_COMPLEXITY_SQL_SCAFFOLDING`)
that ask the model to decompose the problem into intermediate steps before writing code/SQL,
rather than just answering directly. (An earlier "prompt richness" lever —
`Settings.prompt_sample_rows`, scaling how many sample rows appeared in the prompt — was
removed in Phase 4 once `data_context_service.py` made it redundant; see `PHASES.md` Phase 4's
complexity-tiering follow-up for why.) This keeps the two-call planner and the 500-row
threshold meaningful.

- Embeddings: `all-MiniLM-L6-v2`, quantized ONNX export (`models/all-MiniLM-L6-v2-onnx/`,
  ~22MB, bundled into the image at build time — never downloaded at runtime), run via
  `onnxruntime` + `tokenizers` instead of `sentence-transformers`/`torch`/`transformers`
  (Phase 5 — see PHASES.md's Docker-size tradeoff). `rag_service.py`'s `OnnxEmbedder` hand-
  implements the mean-pooling + L2-normalization `sentence-transformers` did internally;
  quality-parity against the original model was verified in `scripts/validate_onnx_embedder.py`
  and `scripts/validate_retrieval_threshold.py` — quantization shifts raw similarity values
  slightly but changes zero retrieve/don't-retrieve decisions at this app's actual
  `rag_distance_threshold`. Loaded lazily on first RAG use, same trigger point as before
  (first `/upload` that includes a `context_file`); every session after that reuses the same
  loaded instance. No index of any kind is built at startup (removed in Phase 4b along with
  the global `docs/` index).
- Environment: `GROQ_API_KEY` required in `.env`; the Groq client itself is constructed lazily
  on first use (Phase 2), not at import time
- Groq free tier: 30 RPM, 1k RPD, **8K TPM**, 200k TPD — the TPM ceiling is tight against
  131k-context models

## Data

- Sample data: `sample_data.csv` — TechMart Electronics sales (date, revenue, region, product),
  12 rows
- Test fixtures: `tests/data/` — `large_sales.csv` (1000 rows), `employees.csv`, `stocks.csv`,
  `missing_values.csv`, `special_chars.csv` (spaces/parens/slash in headers), `empty.csv`
- RAG corpus: none checked in. `docs/` only holds `BUGS_FOUND.md` — RAG content is now
  entirely user-supplied at runtime (Phase 4b's optional `context_file` on `/upload`), never a
  static file in the repo.
- Generated at runtime, all gitignored: `data/{session_id}_{table_name}.db`,
  `data/charts/{session_id}.png`, `data/uploads/{session_id}.csv`; a session's RAG index lives
  only in `rag_service._session_indexes` (in-memory, not on disk)

## Known issues

Full ranked register with severities and owning phases is in `PHASES.md`. The ones most likely
to bite while working in this codebase:

- **All model IDs are dead** — nothing works until Phase 1a lands
- ~~**`exec()` of LLM-generated code is not sandboxed**~~ — **Fixed in Phase 5.**
  `python_agent.execute_code()` is the only remaining `exec()` call site (`chart_agent.py`
  stopped executing LLM-written code entirely in the Phase 4 redesign — the LLM only picks a
  JSON chart spec now). Restricted `__builtins__`, an AST pre-check rejecting `import`
  statements and dunder-attribute access, and a best-effort wall-clock timeout are documented
  as defense-in-depth, not a hard guarantee, in `docs/THREAT_MODEL.md` — including the honest
  gap that a thread-based timeout can't forcibly reclaim a hung snippet's CPU/memory the way a
  subprocess kill could (noted there as a near-term follow-up).
- ~~**`/ask` has a path-traversal hole**~~ — **Fixed in Phase 5.** `file_path` is resolved
  against the project root and any escape is rejected (403); the endpoint itself is also
  disabled by default (`Settings.enable_ask_endpoint`), so a deployed instance never exposes it
  without an explicit opt-in.
- ~~**Import-time side effects**~~ — **Fixed in Phase 2.** `llm_service.get_llm_client()` is now
  lazy (`@lru_cache`), `rag_service`'s `SentenceTransformer`/`faiss` load on first RAG use via a
  `RagIndex` class, and `analyst_service.build_agents()` replaces the four import-time agent
  singletons. `python -c "import src.main"` builds no client and loads no transformer.
- **SQLite connections aren't in try-finally** — the cause of Windows `PermissionError` during
  session cleanup. Phase 1b.
- **7 redundant `pd.read_csv` calls** — one request can read the same file up to 6 times.
- **The pipeline is file-path-shaped.** `python_agent` assumes one in-memory dataframe, which
  breaks at warehouse scale. This is the deepest change ahead — Phase 8.

## Current State (Phase 4b complete, Phase 5 next)

V5 file upload merged to `main` via PR #3 (`3a07c1f`). `POST /upload` accepts CSVs via
multipart/form-data, saved to `data/uploads/{session_id}.csv`, with a 30-minute session TTL so
users upload once and ask multiple follow-ups. Chart and DB filenames are UUID-based, fixing the
old `chart.png` collision. `POST /ask` is unchanged for backward compatibility.

Phase 1 (on `claude/v6-revive-and-test`) is done: the Groq models are remapped to
`openai/gpt-oss-20b`/`120b`, the five pending V5 bugs are fixed, 78 automated tests pass, and
`README.md`/`PROJECT_PLAN.md` reflect the data-platform direction. `tests/TEST_RESULTS.md` is
now a historical manual-testing record — its 31 documented results predate the Groq model
retirement and are unreproducible; the pytest suite supersedes it.

Phase 2 (config + DI refactor, on `claude/v6.1-config-di`) is done: `src/config.py` centralizes
the ~18 previously hardcoded values behind a `pydantic-settings` `Settings` class; the Groq
client and the RAG `SentenceTransformer`/FAISS index are now built lazily instead of at import
time; all four agents accept an injectable `client`; `analyst_service.build_agents()` replaces
the four import-time agent singletons (also closing the shared-singleton mutation race); and the
POSIX-only `split("/")` path bug is fixed. 16 new tests added (`test_config.py`,
`test_agent_factory.py`) — full non-live suite is 94 tests, and dropped from 32.82s to 2.09s
since nothing loads a SentenceTransformer at collection time anymore.

Phase 3 (API + integration tests, CI, on `claude/v6.2-ci`) is done: integration tests for
`/upload` and `/ask` using `TestClient` and a `FakeGroqClient` (no real Groq calls);
an orchestration test suite that caught and fixed two real bugs — the python/sql `elif` mutual
exclusion (sql was silently dropped when both agents were named) and the chart-only-plan crash
(`result=None` failing Pydantic validation) — both documented in `docs/BUGS_FOUND.md`; a
`live_api`-marked test file verified against the real Groq API; and a GitHub Actions CI
workflow (matrix 3.11/3.13, `ruff check`, 70% coverage gate, no `GROQ_API_KEY` in the job).
Also fixed `python_agent.execute_code`'s `sys.stdout` reassignment
(`contextlib.redirect_stdout`) and cleaned up lint issues surfaced by `ruff`. The `/ask`
path-traversal hole was confirmed live (not just theoretical) during this phase via a browser
reproduction against a real running server. 119 total tests (115 non-live + 4 live), 1 `xfail`,
83.66% coverage.

Phase 4 (dynamic data context, on `claude/v6.3-data-context`) is done: `data_context_service.py`
generates dataset-agnostic context (row/column counts, dtypes, categorical values, numeric
ranges) so the app stops being hardcoded to `sample_data.csv`'s TechMart shape; `sql_agent`
quotes column names; `chart_agent` was redesigned from "LLM writes matplotlib code" to "LLM
picks a small chart spec, hand-written code renders it"; complexity tiering gained
`HIGH_COMPLEXITY_SCAFFOLDING`/`HIGH_COMPLEXITY_SQL_SCAFFOLDING` prompt instructions once the old
`prompt_sample_rows` lever became dead code. Mid-phase, `docs/routing_rules.txt` and its RAG
retrieval were removed entirely — the content duplicated rules already hardcoded in agent
prompts, so retrieving it changed nothing the LLM saw. See `PHASES.md` Phase 4 for the full
account of both mid-phase corrections.

Phase 4b (user-supplied RAG context, on `claude/v6.4-rag-context`) is done: RAG's one real use
case is now a user-uploaded business-context document (e.g. a column glossary, terminology, or
fiscal-calendar note a developer could never pre-write), not a static repo file.
`POST /upload` gained an optional `context_file` field; `rag_service.py` replaced the global
`docs/`-folder `RagIndex` singleton with `build_session_index()`/`retrieve_session_context()`/
`drop_session()` operating on a per-session `{session_id: RagIndex}` dict, so one session's
document can never leak into another's answers; `python_agent`/`sql_agent` retrieve
session-scoped context instead of the old global one, and `session_service`'s TTL cleanup now
also drops each expired session's RAG index. A session that never uploads a `context_file`
behaves exactly as before — the additive, backward-compatible pattern this project uses
throughout. `main.py`'s lifespan no longer builds any index at startup; the embedding model
loads lazily on the first `/upload` that actually includes a context document. 8 new tests
(`test_rag_service.py`) plus integration coverage in `test_upload_endpoint.py` for retrieval,
session isolation, and the new upload's validation (non-UTF-8, empty, oversized, duplicate
field) — 170 total tests, 91% coverage.

**Phase 5 (Docker + Cloud Run + security), on branch `claude/v7-deploy`, is in progress.**
Weekend 1 (security) is done: the `/ask` path-traversal hole is fixed and the endpoint is
disabled by default; `python_agent.execute_code()`'s `exec()` is sandboxed (restricted
`__builtins__`, an AST pre-check, a best-effort timeout — see `docs/THREAT_MODEL.md`); live
edge-case testing against the real Groq API found and fixed several real bugs along the way
(missing `pd`/`__build_class__` in the sandbox, `python_agent` writing chart/import code it
shouldn't, the planner never seeing a session's RAG context, raw numpy reprs leaking into
output) — see `docs/BUGS_FOUND.md`'s Phase 5 section. The `sentence-transformers`+`torch` ->
ONNX embedding swap (RAG stays real, per Phase 4b's resolution of the Docker-size tradeoff) is
also done: a quantized ONNX export of `all-MiniLM-L6-v2` (~22 MB, better than the ~90 MB
originally estimated) bundled into `models/`, run via `onnxruntime`+`tokenizers` — see the
Configuration section above. `requirements.txt` dropped from 79 to 65 packages. Weekend 2
(Dockerfile, Cloud Run deploy, chart-image serving) is not yet started.
