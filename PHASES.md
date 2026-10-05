# Phases — Data Engineering Platform Build

**Purpose of this file:** the single source of truth for where this project is. Each phase is
scoped to roughly one weekend at ~10 hrs/week, has its own branch, and ends mergeable. Work
**one phase per chat session** — read this file first to find the current phase.

**Last updated:** 2026-10-05 · **Current phase:** 6 — Ingestion, in progress ·
**Branch:** `claude/v8-ingestion`

---

## Why this plan exists

This repo was heading toward a monetized SaaS product. That direction is dead. It is now
explicitly a **Data Engineering portfolio piece**.

The problem with the previous direction: strip away the LLM layer and the data did almost
nothing. A CSV was read into in-process SQLite, queried once, and discarded. No ingestion, no
scheduling, no warehouse, no modeling, no lineage, no data quality. A DE interviewer would see
a competent AI app, not evidence of pipeline engineering.

**Target architecture** — the existing multi-agent system becomes the *serving layer* on top
of a real platform:

```
SEC EDGAR Financial Statement Data Sets, checked weekly
    -> ingestion (Python + pyarrow) into bronze Parquet
Lake (local / GCS): bronze -> silver -> gold
    -> transformation (PySpark + Delta Lake)
Warehouse (BigQuery, free tier): gold only
    -> data quality (Great Expectations on Spark)
Existing agent queries the gold layer instead of a CSV
```

Decisions, alternatives and the architecture diagram: `docs/PHASE6_DESIGN.md`.

**Preserved and not to be rewritten:** the hand-rolled `PlannerAgent`, complexity-based
routing, and retry loops. This is the differentiator. Almost every candidate's AI project is
LangChain plus glue; rewriting into a framework destroys what makes this repo distinctive.
LangGraph is *added alongside* in Phase 11, never as a replacement.

## Sequencing principle

> You cannot safely change code you cannot test, and you cannot test code with import-time
> side effects.

That single sentence dictates Phases 1–3, and is the answer to "why did you start with
testing?"

---

## Phase table

| # | Phase | Branch | Weekends | Target | Status |
|---|---|---|---|---|---|
| 1 | Resurrection + first tests | `claude/v6-revive-and-test` | 1 | Aug 2026 | ✅ Done |
| 2 | Config + DI refactor | `claude/v6.1-config-di` | 1 | Sep 2026 | ✅ Done |
| 3 | API + integration tests, CI | `claude/v6.2-ci` | 1 | Sep 2026 | ✅ Done |
| 4 | Dynamic data context | `claude/v6.3-data-context` | 1 | Sep 2026 | ✅ Done |
| 4b | User-supplied RAG context | `claude/v6.4-rag-context` | 1 | Sep 2026 | ✅ Done |
| 5 | Docker + Cloud Run + security | `claude/v7-deploy` | 2 | Sep–Oct 2026 | ✅ |
| 6 | Ingestion: SEC EDGAR → bronze Parquet | `claude/v8-ingestion` | 1 | Oct 2026 | ⬜ |
| 7 | Spark + Delta Lake: bronze → silver → gold; gold → BigQuery | `claude/v9-spark-lakehouse` | 2 | Oct 2026 | ⬜ |
| 8 | Data-source abstraction; agent reads gold | `claude/v10-warehouse-serving` | 2 | Nov 2026 | ⬜ |
| 9 | Airflow (Docker) + Cloud Scheduler + Workflows | `claude/v11-orchestration` | 2 | Nov 2026 | ⬜ |
| 10 | Data quality: Great Expectations on Spark | `claude/v12-data-quality` | 1 | Dec 2026 | ⬜ |
| 11 | LangGraph dual implementation | `claude/v13-langgraph` | 1 | Dec 2026 | ⬜ |
| 12 | Streamlit demo UI | `claude/v14-streamlit` | 1 | Jan 2027 | ⬜ |
| 13 | Polish: docs, diagrams, ADR log | `claude/v15-polish` | 1 | Jan 2027 | ⬜ |

17 weekends across ~21 available. The slack is deliberate — Phases 7, 8, and 9 are the most
likely to overrun.

**If time runs short:** cut Phase 12 (Streamlit), not Phase 11 (LangGraph). See
"Career framing" below.

---

## Locked decisions — do not re-litigate

1. **Tests: hybrid.** Pure units first (CI green fast) → config/DI refactor → integration.
2. **Orchestration: Docker Airflow locally + Cloud Run Jobs & Cloud Scheduler deployed.**
   Cloud Composer has **no free tier** (~$300+/mo) — never use it.
3. **Python agent: keep both agents, dual-source.** SQL agent → BigQuery marts; Python agent
   gets a *bounded* `LIMIT`ed pull into a dataframe for pandas-shaped work.
4. **CSV and warehouse side by side**, behind one `DataSource` abstraction.
5. **Model tiers differentiated beyond model choice** — complexity drives model *and* retry
   budget *and* prompt richness (forced by the Groq retirement; see Phase 1).
6. **Monetization killed.** No SaaS tiers, no usage tracking, no API-key billing, no
   white-label. A minimal Streamlit demo UI only, no auth.
7. **Security fixes land with the Cloud Run deploy, before deploying** — not after.

## Career framing

Targeting **both** Data Engineering and Agentic AI roles. "AI agents querying a real
warehouse" is precisely the emerging AI Platform / AI Data Engineer intersection, and few
candidates occupy it — most agent projects have no data platform, most DE projects have no
agent work.

The build is **DE-weighted** (more open roles, more standardized interview loop, maps onto
existing QA/ETL experience — the stronger fallback), but the agent story stays strong. At
application time: **one repo, two README framings** — lead with the platform for DE
applications, lead with the agent architecture for AI roles.

---

## Phase 1 — Resurrection + first tests ✅

**Branch:** `claude/v6-revive-and-test` · **1 weekend**

### The problem

**The app is 100% non-functional.** Verified 2026-08-24 against the live Groq API with the
project's own key: all three models in `MODEL_ROUTING` are **absent from the API** — not
deprecated, gone. No Llama text-generation model remains on Groq at all. Every request dies at
the planner's first call.

Dead: `llama-3.1-8b-instant`, `llama-3.3-70b-versatile`,
`meta-llama/llama-4-scout-17b-16e-instruct`. Also gone: `qwen/qwen3-32b`.

Consequently **all 31 documented outcomes in `tests/TEST_RESULTS.md` are unreproducible.**

Do not write tests against model IDs that 404. Fix the models first.

### 1a. Model remap — do this before anything else

`src/services/llm_service.py`:

```python
MODEL_ROUTING = {
    "low":    "openai/gpt-oss-20b",   # fast/cheap: counts, totals, lookups
    "medium": "openai/gpt-oss-120b",  # grouping, filtering, charts
    "high":   "openai/gpt-oss-120b",  # same model, richer prompt + more retries
}
DEFAULT_MODEL = "openai/gpt-oss-120b"
```

Only two usable free-tier text models exist, so tiering **must** mean more than model identity
or it means nothing. Add:

```python
RETRY_BUDGET       = {"low": 2, "medium": 3, "high": 5}
PROMPT_SAMPLE_ROWS = {"low": 3, "medium": 5, "high": 10}
```

`python_agent`/`sql_agent` read `max_attempts` from `RETRY_BUDGET[complexity]` instead of the
hardcoded `3`; `get_csv_context()` takes a `sample_rows` count. This keeps the two-call planner
and the 500-row `HIGH_COMPLEXITY_ROW_THRESHOLD` meaningful — they now govern retry policy and
prompt budget rather than only model choice.

**Interview answer:** "My provider retired the entire model family my routing depended on. I
re-derived the tiers around retry budget and prompt richness rather than model identity, so the
routing survives provider churn."

### 1b. Pending V5 bug fixes

Found during Postman testing after V5 shipped.

| File | Change |
|---|---|
| `src/services/database_service.py` | Wrap the SQLite connection in `contextlib.closing` / try-finally; stop erasing the original exception type with `raise Exception(...)` |
| `src/agents/sql_agent.py` (`execute_sql`) | Same connection fix — **this is the root cause of the Windows `PermissionError` on cleanup** |
| `src/services/session_service.py` | Wrap every `os.remove()` in `try/except (PermissionError, OSError)`; log and let the next cycle retry. Extend `cleanup_orphaned_files()` to sweep `data/*.db` |
| `src/main.py` | Add a `RequestValidationError` handler → clean 400 instead of the verbose Pydantic 422 |
| `requirements.txt` | Add `requests` (imported by `src/groq_all_models.py` but missing — latent CI/Docker failure), plus `pytest`, `pytest-cov`, `pytest-mock` |

### 1c. First tests — pure units only, no refactor required

**Added:** `pytest.ini`, `tests/__init__.py`, `tests/conftest.py`, and:

| Test file | ~Tests | Focus |
|---|---|---|
| `tests/unit/test_session_service.py` | 12 | create/get/expiry, TTL boundary, `_delete_session_files`, orphan sweep, thread-safety under `ThreadPoolExecutor` |
| `tests/unit/test_schemas.py` | 6 | `AnalysisResponse` required fields, `Optional` defaults, `agents_used` default isolation |
| `tests/unit/test_llm_service.py` | 8 | `get_model_for_complexity` parametrized; assert every `MODEL_ROUTING` value is a live model ID |
| `tests/unit/test_cleaners.py` | 10 | `clean_sql`/`clean_code` parametrized: bare, fenced, whitespace, empty |
| `tests/unit/test_rag_chunking.py` | 8 | `load_documents` + `split_into_chunks`: min-word threshold, paragraph splitting, filename metadata |
| `tests/unit/test_database_service.py` | 8 | table-name derivation + sanitization, `session_id` prefixing, real SQLite round-trip in `tmp_path` |

**~52 tests.** `conftest.py` provides `sample_csv`, `empty_csv`, `messy_csv`, `tmp_data_dir`
(monkeypatched cwd so nothing writes into the real `data/`), and `fake_groq_client`.

Register markers: `unit`, `integration`, `live_api`.

**Done when:** `pytest -m "not live_api"` green (~52 tests); `/upload` answers a real question
with `openai/gpt-oss-120b`; a `data/*.db` file is deleted after TTL with no `PermissionError`.

---

## Phase 2 — Config + DI refactor ✅

**Branch:** `claude/v6.1-config-di` · **1 weekend** · **Completed 2026-08-27**

September's Docker work pulled forward — you cannot ship hardcoded paths and model IDs in a
container. It also removes the test blockers.

**Added:** `src/config.py` — a `pydantic-settings` `Settings` class owning the ~18 hardcoded
values (model IDs, retry budget, prompt sample rows, `session_ttl_minutes`, `max_file_size`, row
threshold, `uploads_dir`, `charts_dir`, `data_dir`, `docs_dir`, embedding model, RAG `top_k` +
distance threshold, temperatures), cached process-wide via `get_settings()` (`@lru_cache`). Plus
`tests/unit/test_config.py` (11) and `tests/unit/test_agent_factory.py` (5).

**Modified:**
- `src/services/llm_service.py` — `client = Groq(...)` at import became a lazy,
  `@lru_cache`d `get_llm_client()`; `DEFAULT_MODEL`/`MODEL_ROUTING`/`RETRY_BUDGET`/
  `PROMPT_SAMPLE_ROWS` now derive from `Settings` instead of hardcoded literals (module
  attribute names unchanged, so no caller needed to change)
- `src/agents/*.py` — `__init__(self, client=None)` on all four agents (`chart_agent.py` also
  takes `settings=None` for `charts_dir`); falls back to `get_llm_client()`/`get_settings()`
  when nothing is injected
- `src/services/analyst_service.py` — replaced the four module-level agent singletons with a
  `build_agents(client=None)` factory called inside `analyse()`; also fixes the shared-singleton
  mutation race (risk #13) since each request now gets its own agent instances
- `src/services/rag_service.py` — `SentenceTransformer` and `faiss` imports moved inside
  methods, model construction deferred to first actual use; module globals (`model`, `index`,
  `chunks`) replaced with a `RagIndex` class holding that state, wrapped by a lazily-created
  module-level singleton so `build_index()`/`retrieve_context()` call sites are unchanged
- `src/agents/chart_agent.py` — removed the `os.makedirs` side effect from `__init__`
  (directory creation already happens once in `main.py`'s lifespan)
- `src/services/analyst_service.py` — `file_path.split("/")[-1]` (POSIX-only, broke on Windows
  paths) replaced with `os.path.basename(file_path)` (risk #15)

**Measured impact:** full non-live suite dropped from 32.82s to 2.09s (94 tests) — the ~35s
SentenceTransformer import-time tax (risk #8) is gone since nothing imports the model at
collection time anymore.

**Done when:** `python -c "import src.main"` builds no Groq client and loads no transformer
✅ verified (Groq `lru_cache` shows 0 hits/misses after import; no "Loading weights" output);
Phase 1 tests still pass ✅ (94/94, `pytest -m "not live_api"`).

---

## Phase 3 — API + integration tests, CI ✅

**Branch:** `claude/v6.2-ci` · **1 weekend** · **Completed 2026-08-28**

**Added:**
- `tests/integration/test_analyst_orchestration.py` (5) — routing → dispatch via
  `FakeGroqClient` (a content-sniffing fake in `conftest.py` that drives the real `analyse()`
  end to end, no real Groq calls). Caught and fixed two real bugs — see `docs/BUGS_FOUND.md`
  #1–2: the `python`/`sql` `elif` mutual exclusion (sql was silently dropped when both were
  named — now both run independently, results labeled and concatenated) and the chart-only
  plan crash (`result=None` failing `AnalysisResponse`'s Pydantic validation — now coerced to
  `["python", "chart"]` before dispatch).
- `tests/integration/test_upload_endpoint.py` (10) — ports `TEST_RESULTS.md`'s V5 scenarios
  into automated `TestClient` requests: first upload, session follow-up, chart routing,
  non-CSV/empty/corrupt-file rejection, invalid session 404, missing-file 400,
  session-priority-over-file, oversized-file rejection.
- `tests/integration/test_ask_endpoint.py` (6 + 1 `xfail`) — backward compat, auto-generated
  session_id, 404/400 paths, and one `xfail`-marked path-traversal test documenting the known
  hole (confirmed live during this phase — see below) until Phase 5 fixes it.
- `tests/live/test_live_api.py` (4) — `@pytest.mark.live_api`, deselected by default,
  `skipif`-guarded when no real key is configured. Verified live: both `openai/gpt-oss-20b`
  and `openai/gpt-oss-120b` still respond.
- `.github/workflows/ci.yml` — matrix 3.11/3.13, `actions/checkout@v4` +
  `actions/setup-python@v5`, `ruff check .`, `pytest -m "not live_api" --cov=src
  --cov-fail-under=70`. No `GROQ_API_KEY` anywhere in the job.
- `docs/BUGS_FOUND.md` — new running bug log (separate from this file's risk register), used
  throughout this phase.

**Modified:**
- `src/agents/python_agent.py` — `execute_code`'s manual `sys.stdout` reassign/restore
  replaced with `contextlib.redirect_stdout`, closing the "exception path skips the restore"
  gap this phase's own note flagged. The deeper concurrency issue (two simultaneous requests
  fighting over the same process-wide `sys.stdout`) is **not** fixed by this — see
  `docs/BUGS_FOUND.md` #3 and Phase 5.
- Lint cleanup surfaced by adding `ruff` to CI: an unnecessary f-string prefix
  (`src/routes/ask.py`), import-order issues from `load_dotenv()` sitting between imports
  (`src/services/llm_service.py`, `src/groq_all_models.py`), an unused `import os` left over
  from Phase 2's `chart_agent.py` cleanup, and two unused test imports.
- `tests/unit/test_database_service.py` — two tests were using `with sqlite3.connect(...) as
  conn:`, which only manages the transaction, not the connection lifetime — the connection was
  never actually closed. Switched to `with closing(sqlite3.connect(...)) as conn:`, the same
  pattern the Phase 1b fix already uses in the real `database_service.py`.

**Verified live, not just via docs:** the `/ask` path-traversal hole (risk #3) was reproduced
against a real running server via the browser's `/docs` Swagger UI, using `file_path: ".env"`
— confirmed reading and returning the contents of the real `.env` file, including the live
`GROQ_API_KEY`. Key was rotated afterward as a precaution. This confirms the `xfail` test's
premise is accurate, not just theoretical.

**Also documented (not part of this phase's code):** a future multi-user/login SaaS idea was
raised and parked as "Candidate Phase 15+" below — explicitly not reopening the no-SaaS locked
decision, gated on all 13 phases finishing first.

**Result:** 119 tests total (115 non-live + 4 live), 1 `xfail`, 83.66% coverage (`--cov=src`),
`ruff check .` clean. **Done when** criteria met: green CI run on first push (matrix 3.11/3.13
both green), ~100+ tests, ≥70% coverage.

**Done when:** green CI badge in README; ~100 tests; ≥70% coverage.

---

## Phase 4 — Dynamic data context ✅

**Branch:** `claude/v6.3-data-context` · **1 weekend**

`docs/business_context.txt` and `docs/data_dictionary.txt` describe TechMart Electronics
(Laptop/Phone/Tablet, 4 Indian regions) and are hardcoded to `sample_data.csv`. RAG retrieves
them for *every* question on *every* dataset. Postman testing proved the consequence: on a real
sales CSV, "how many Fruits under Item Type?" returned *"cannot be answered from the provided
data"* because the planner read TechMart context and ruled Fruits out of scope.

**The system only works on its own sample file** — fatal for a portfolio piece someone else
will clone and run.

**Added:** `src/services/data_context_service.py` — `generate_data_context(file_path,
sample_rows)` returns row/column counts, dtypes, unique counts, categorical values (≤20
unique), numeric ranges, null counts, sample rows. Pure pandas, no LLM call. Plus
`tests/unit/test_data_context_service.py` (~12) over all six fixtures.

**Modified:** `analyst_service.py` generates the context once and passes it to all agents (also
removing several of the 7 redundant `pd.read_csv` calls); planner/python/sql agents accept
`data_context`; `sql_agent` quotes column names (fixes the observed `WHERE Total Revenue >
10000` syntax error); `chart_agent` gets large-dataset prompt rules (fixes the observed
cluttered charts).

**RAG correction made mid-phase (2026-08-31):** while wiring the planner to a new
`retrieve_routing_context()` helper, `routing_rules.txt`'s content turned out to be a
near-total duplicate of rules already hardcoded directly in `planner_agent.py`'s and
`python_agent.py`'s own prompts (same routing logic, same `pd.Grouper(freq='ME')` rule, worded
slightly differently). Retrieving it added an embedding + FAISS search round-trip for text
that changed nothing the LLM couldn't already see. **Decision: remove RAG-for-routing-rules
entirely** — delete `docs/routing_rules.txt`, delete `retrieve_routing_context()`, keep only
the hardcoded prompt rules as the single source of truth. This does not mean dropping RAG from
the project — see Phase 4b below, which replaces this with a RAG use case that's actually
load-bearing (user-supplied documents, unknowable at build time, genuinely too large to
hardcode).

**Done when:** questions on `tests/data/employees.csv` and `stocks.csv` route to an agent
instead of returning out-of-scope.

### Chart agent redesign (added mid-phase, 2026-09-02): chart spec, not chart code

The originally-planned "large-dataset prompt rules" fix for `chart_agent` (cap at top-15
categories, conditionally add value labels) was implemented first as more prompt instructions,
and immediately proved the fragility of that whole approach. Concretely, on
`tests/data/large_sales.csv` (1000 rows, 652 unique dates): the first version of the fix
produced a chart where several close-valued bars' labels rendered on top of each other
(illegible, doubled text). Adding a rule to detect close values and suppress labels in that
case *fixed the label collision* but caused a **new, unrelated crash** — the model, now
juggling more instructions, decided to re-parse the (already-usable) computed-result string
via `pd.read_csv(io.StringIO(...), delim_whitespace=True)` instead of using it directly, and
`delim_whitespace` doesn't exist in the pandas version pinned in `requirements.txt`. This
crashed inside `chart_agent.run()`'s single `try/except` (chart_agent has no retry loop,
unlike python/sql agents), surfacing as `status: "failed"` with `agents_used: ['python']` —
the chart agent had run and failed, not "not been detected," but the response made that hard
to tell apart.

**The lesson, stated plainly: every instruction added to a prompt that asks an LLM to write and
execute a full program from scratch is a new opportunity for a new failure mode.** Matplotlib
wasn't the problem — asking an LLM to freely author charting *code*, then `exec()` it, on every
single request, is. More rules on top of that pattern trade one bug for another rather than
converging on correctness.

**Decision: redesign `chart_agent` from "LLM writes matplotlib code" to "LLM picks a small,
structured chart spec; hand-written, deterministic code renders it."**

- The LLM's job shrinks to producing something like `{"chart_type": "bar", "x_column": ...,
  "y_column": ..., "title": ...}` — a low-variance decision, not a program.
- All the fragile mechanics — top-N category selection, close-value label suppression,
  rotation/offset, `tight_layout()`, file saving — move into hand-written Python in
  `chart_agent.py` itself, executed identically every time, unit-testable the same way
  `data_context_service.py` is, never regenerated per-request.
- This directly serves "don't bloat the prompt": the chart prompt shrinks (no matplotlib API
  instructions needed at all), while correctness improves, since the parts that must always
  work correctly are no longer subject to LLM variance.
- It also structurally prevents the `delim_whitespace` bug and the label-collision bug at the
  same time: deterministic code computes top-N and label-spacing directly from the real
  dataframe/SQL result object, never by re-parsing a truncated `print()` string.
- Matplotlib stays the renderer for now (matches the current PNG-returning API); see Phase 12
  for the Plotly-if-Streamlit-lands note — library choice stops being LLM-exposed either way
  once this redesign lands.

### Complexity tiering lost its third lever — resolved (2026-09-04)

Removing `python_agent.get_csv_context()` in favor of the shared `data_context` (generated
once by `data_context_service.py` with a fixed `sample_rows=5`, not complexity-scaled) made
`get_sample_rows()`/`PROMPT_SAMPLE_ROWS`/`Settings.prompt_sample_rows_*` genuinely dead code —
nothing calls `get_sample_rows()` anymore. Before this phase, complexity drove three things:
model tier, retry budget, and prompt richness (`sample_rows`, more example rows for harder
questions). `CLAUDE.md`'s Configuration section still claims all three; that claim is now
stale.

**Why this isn't just "delete the dead code and move on":** with `MODEL_ROUTING["medium"] ==
MODEL_ROUTING["high"]` already (both `gpt-oss-120b`, forced by Phase 1's Llama-retirement
remap), losing the `sample_rows` lever too would leave **retry budget as the only real
difference** between medium and high complexity — a thin justification for the whole tiering
system. Retry budget only helps after a first attempt already failed; it doesn't change what
the model can reason about on attempt one, which is what "complexity" is supposed to capture.

**Also worth naming honestly: the old `sample_rows` lever was never doing much either.** More
example rows in the "Sample rows" section doesn't give the model more *reasoning* capacity for
a genuinely multi-step question ("which region is growing fastest") — `data_context_service`
already surfaces the full categorical/numeric picture regardless of complexity; sample rows are
just a "here's what a row looks like" illustration. Restoring it would stop the dead-code
problem without actually fixing the thin-justification problem.

**Proposed alternative (not yet implemented) — tier the task instructions, not just the
surrounding context:** add complexity-scaled *reasoning scaffolding* to
`python_agent.generate_code()`/`sql_agent.generate_sql()`'s prompts. For `high` complexity
only, add an instruction like "before writing code, list the intermediate values you need to
compute, in order, then compute and print each step explicitly, not just the final answer" —
changing what the model actually *does* on a hard question, not just how many chances it gets
or how many example rows it sees. `low`/`medium` keep today's direct-answer prompt style
(scaffolding would be unnecessary token overhead against the 8K TPM ceiling for a
single-metric question). This costs *fewer* extra tokens on low/medium (no scaffolding
instruction) rather than more on high (bigger sample block) — a better fit for the tight TPM
budget than the old lever. A secondary, more surgical idea: raise `sample_rows` for `high`
only when the dataset has date/time structure and the question is trend-shaped, rather than
blanket-more-rows-for-high regardless of relevance.

**Implemented:** the tiered-instruction approach, exactly as proposed above.
`python_agent.py` gained `HIGH_COMPLEXITY_SCAFFOLDING` (asks the model to list intermediate
values and print each step, not just the final answer); `sql_agent.py` gained
`HIGH_COMPLEXITY_SQL_SCAFFOLDING` (asks the model to use a CTE/subquery to make each step of a
multi-step query verifiable). Both are spliced into `generate_code()`/`fix_code()` and
`generate_sql()`/`fix_sql()`'s prompts only when `complexity == "high"` — `low`/`medium`
prompts are byte-for-byte unchanged. Placement matters: the scaffolding sits right after the
question (or, in `fix_code()`/`fix_sql()`, right after the error) and *before* the mechanical
formatting rules (print statements, date parsing, column quoting) — burying a "think through
the approach first" instruction after a checklist of syntax reminders risked the model treating
it as just another minor rule instead of the framing instruction it's meant to be.

The now-dead `get_sample_rows()`/`PROMPT_SAMPLE_ROWS`/`prompt_sample_rows_*` were deleted from
`llm_service.py`/`config.py`, along with their tests in `test_config.py`/`test_llm_service.py`.
`CLAUDE.md`'s "prompt richness" claim corrected to describe the scaffolding mechanism instead.

---

## Phase 4b — User-supplied RAG context ✅

**Branch:** `claude/v6.4-rag-context` · **1 weekend** · **Completed 2026-09-11**

### Why this phase exists

Phase 4 found that the project's only RAG use case (`docs/routing_rules.txt`, retrieved for
every planning call) was decorative — its content duplicated rules already hardcoded in
`planner_agent.py`/`python_agent.py`'s own prompts, so removing the retrieval call changed
nothing the LLM saw. That's the wrong shape of problem for RAG: a small, developer-authored,
rarely-changing file fits trivially inline in a prompt and gains nothing from
chunk-embed-retrieve. **RAG earns its place when the corpus is too large to inline, changes
independently of code, and is supplied by someone other than the developer** — none of which
was true of `routing_rules.txt`.

This phase gives the project a RAG use case that actually satisfies those three conditions:
**user-supplied business context, uploaded alongside a CSV.** A user who understands their own
data (e.g. "in our pipeline, 'won' means closed-deal," "fiscal year starts in April," a column
glossary) can upload a `.txt`/`.md` document describing it — text a developer could never
pre-write, that can genuinely run to multiple pages, and where only the chunks relevant to the
*specific question being asked* should be pulled into a prompt rather than the whole document
every time. This is also the direction the project's Phase 5 Docker-size tradeoff assumed would
either shrink to nothing or stay real — this phase settles it: RAG stays real, so Phase 5 needs
a lighter-weight embedding stack (see Phase 5's updated note), not RAG removal.

### Added

- **Upload path**: `POST /upload` gains an optional second file field (e.g. `context_file`) —
  or a dedicated `POST /upload/context` tied to an existing `session_id` — accepting a
  plain-text or Markdown document. No file is required; nothing changes for a session that
  doesn't provide one.
- **Per-session RAG index**: `RagIndex` (today a single process-wide singleton built once at
  startup from `docs/`) needs a per-session instance built on demand only when a context
  document is actually uploaded — analogous to how `session_service` already isolates each
  session's CSV by `session_id`. A user's business-context document must never leak into
  another session's questions.
- **`retrieve_session_context(session_id, question)`** in `rag_service.py` (or equivalent) —
  looks up the requesting session's own index (if one exists) and retrieves the chunks nearest
  to the question, the same nearest-neighbor mechanics `RagIndex.retrieve_context()` already
  has, just scoped per-session instead of global.
- Agents (likely python/sql, possibly the planner) accept this retrieved context alongside the
  Phase 4 `data_context`, the same additive, backward-compatible parameter pattern used
  throughout this project (default `""`, no session doc → no behavior change).

### Removed (carried over from the Phase 4 correction above)

- `docs/routing_rules.txt`, `retrieve_routing_context()` — already deleted in Phase 4; this
  phase doesn't reintroduce them.

**Implemented, reusing existing plumbing:** `python_agent.py`/`sql_agent.py` already had an
`rag_context` prompt parameter left over from the removed global RAG — Phase 4b just swapped
its source. `retrieve_context()`/global `RagIndex`/`build_index("docs")` deleted; added
`build_session_index()`, `retrieve_session_context()`, `drop_session()` in `rag_service.py`
over a module-level `{session_id: RagIndex}` dict. `/upload` gained an optional `context_file`
field (rejects duplicates, empty, oversized `Settings.max_context_doc_size`, and non-UTF-8 —
same pattern as the existing CSV checks). `session_service._delete_session_files()` now also
calls `drop_session()`, so a session's RAG index rides the same sliding TTL as its CSV/DB
files. `python_agent.run()` gained the `session_id` param it was missing.

**Verified live** (2026-09-11, manual Postman + real Groq API): a session with an uploaded
glossary term correctly resolved a question using it; a fresh session without one didn't;
isolation confirmed between two concurrent sessions with different glossaries.

**Tests:** `tests/unit/test_rag_service.py` (7, new — isolation, no-op cases) and 8 new
integration tests in `test_upload_endpoint.py` (retrieval, isolation, the 4 rejection cases),
both using a fake bag-of-words embedder so the suite never loads the real SentenceTransformer.
170 total tests, 91% coverage, `ruff check .` clean.

**Also noted, not acted on:** risk register #22 below — free-tier Groq models are adequate for
this portfolio but not at parity with current frontier models; a future upgrade candidate.

**Done when:** a session that uploads a context document alongside its CSV gets answers that
correctly incorporate that document's content for a question it's actually relevant to, and a
session that doesn't upload one behaves exactly as before (no regression, no required field).

---

## Phase 5 — Docker + Cloud Run + security ✅

**Branch:** `claude/v7-deploy` · **2 weekends** — do not attempt in one

**Zero-cost mandate (added mid-phase, 2026-09-16):** this project must cost literal $0 through
the portfolio phase, not "basically free." Verified live: Artifact Registry's 0.5 GB free tier
is shared across the whole billing account (not per-project); Cloud Run's 2M requests/180k
vCPU-s/360k GiB-s per month is permanent and effectively unlimited for a demo; Secret Manager's
6 active versions + 10k accesses/month is trivial at low traffic. **The real image-size target
is <0.5 GB, not the <1.5 GB done-criterion below** — that number only guarantees "small," not
"free." Revise the done-criterion once Weekend 2 lands.

### Security — before deploy, not after ✅ done (Weekend 1)

1. ✅ **`/ask` path traversal** — `file_path` resolved against the project root, any escape
   rejected (403); endpoint disabled by default via `Settings.enable_ask_endpoint`.
2. ✅ **`exec()` of LLM-generated code** — `python_agent.execute_code()` (the only remaining
   call site; `chart_agent` stopped executing LLM code in the Phase 4 redesign) now runs with a
   restricted `__builtins__`, an AST pre-check rejecting imports and dunder-attribute access,
   and a best-effort wall-clock timeout. Documented honestly in `docs/THREAT_MODEL.md`,
   including the known gap that a thread-based timeout can't forcibly reclaim CPU/memory from a
   hung snippet the way a subprocess kill could.
3. **Found via live edge-case testing against the real Groq API, fixed alongside the above**
   (see `docs/BUGS_FOUND.md`'s Phase 5 section for full detail): the sandbox initially broke
   legitimate code (missing `pd`, missing `__build_class__`/`__name__`); `python_agent` had no
   instruction against writing imports or generating charts itself, causing repeated
   import-retry failures on chart questions; the planner never received a session's RAG
   context, so a question phrased in glossary terms the user uploaded could get rejected as
   out-of-scope before `python_agent` (which would've understood it) ever ran; raw
   numpy/dict reprs were leaking into user-facing output. All fixed and verified live.

Remaining, deferred to Phase 13 (not blocking this phase — see `docs/BUGS_FOUND.md` #15-17):
two more `chart_agent` bugs sharing the already-tracked root cause from findings #10-12.

### The Docker size problem ✅ resolved — embedding swap, then the actual Dockerfile/deploy in Weekend 2 below

`torch` + `sentence-transformers` + `faiss-cpu` was ~2.5 GB installed; a naive image was
~3.5 GB. Artifact Registry's free tier is **0.5 GB** (see the zero-cost mandate above).

**Resolved in Phase 4/4b — RAG stays, the embedding stack shrinks.** The original framing here
assumed RAG would settle into retrieving one static 3 KB file (`routing_rules.txt`), in which
case deleting RAG entirely and inlining that file was the obvious move. Phase 4 found that use
case was decorative (the file duplicated rules already hardcoded in agent prompts) and removed
it — but Phase 4b replaces it with a real RAG use case (user-supplied, per-session business
context documents) that's genuinely too large and too variable to inline. So the Docker-size
fix here is ONNX embeddings instead of `sentence-transformers`+`torch` — keeps real retrieval
capability, drops the ~2.5 GB stack entirely.

**Implemented:** a quantized ONNX export of `all-MiniLM-L6-v2` (`models/all-MiniLM-L6-v2-onnx/`,
~22.6 MB — well under the ~90 MB originally estimated, since the int8-quantized variant was
used instead of fp32), run via `onnxruntime`+`tokenizers`, bundled into the repo/image at build
time rather than downloaded at runtime (works with a read-only container filesystem, no
HuggingFace network dependency in production). `rag_service.py`'s new `OnnxEmbedder` class
hand-implements the mean-pooling + L2-normalization `sentence-transformers` did internally.
Quality-parity verified two ways before `sentence-transformers`/`torch`/`transformers`/
`scikit-learn`/`scipy` (and their now-orphaned transitive deps) were uninstalled:
`scripts/validate_onnx_embedder.py` compares cosine similarity rankings against captured
reference embeddings from the original model, and `scripts/validate_retrieval_threshold.py`
confirms zero retrieve/don't-retrieve decision changes at this app's actual
`rag_distance_threshold` — the two raw ranking differences quantization introduced were both
between near-zero, statistically-insignificant similarity pairs well below the real cutoff; the
one genuinely-related pair tested stayed at an identical 0.760 in both models. Verified live
against the real Groq API afterward (the "senior employee" glossary scenario from finding #13).
`requirements.txt` dropped from 79 to 65 packages.

### Weekend 2 ✅ done (2026-10-01)

Settings fixes (`SESSION_TTL_MINUTES`/`MAX_FILE_SIZE` were hardcoded, silently ignoring their
`Settings` fields); the chart-image-serving gap resolved — `/charts/{uuid}.png` static route in
`main.py`, a new `chart_url` field (`chart_path` kept for backward compat), chart filenames are
random UUIDs rather than `session_id`-derived (closes a real access-credential leak — see
`docs/THREAT_MODEL.md`'s "Chart URL exposure"), and charts are now deleted on session cleanup
(previously never deleted — a slow memory leak on Cloud Run's in-memory filesystem);
`requirements.txt` split into runtime-only + `requirements-dev.txt`, four genuinely-unused
packages dropped (`openai`, `typer`, `rich`, `shellingham`) and one missing one added
(`huggingface-hub`, a real `tokenizers` dependency a drifted local venv had silently gone
without — only surfaced by testing a *genuinely fresh* install, not `pip show` on an
already-populated venv); `Dockerfile` + `.dockerignore` + a CI `docker` job that builds, sizes,
and smoke-tests `/health` on every push.

**GCP setup and deploy, done 2026-10-01.** Project `autonomous-data-agent`, region
`us-central1`, standard pay-as-you-go billing (no 90-day trial-expiry risk), $100 INR (~$1)
budget alert. Artifact Registry repo `images`, Secret Manager secret `groq-api-key`, dedicated
runtime service account `agent-runtime` scoped to `secretmanager.secretAccessor` on that one
secret only. Deployed to Cloud Run (`--max-instances=1 --concurrency=1 --min-instances=0
--memory=1Gi --allow-unauthenticated`).

**Live URL: `https://agent-1079929934435.us-central1.run.app`.** All done-when checks passed:
`/health` responds, `/upload` answers a real question, a chart question returns a working
`chart_url`, `/ask` 404s. A `PUBLIC_BASE_URL` env var was added post-deploy so `chart_url`
returns a full clickable link instead of a bare path. An Artifact Registry cleanup policy
(keep the 2 most recent image versions) was applied, schema verified against Google's own docs
rather than written from memory.

**Image size result: 211 MB compressed, 599 MB uncompressed** — well under the 0.5 GB zero-cost
target.

**Accepted, not fixed:** an expired session's files (CSV, DB, chart PNGs) can sit for up to
`TTL + 5 minutes` before deletion — `session_cleanup_loop()`'s periodic sweep is a separate
path from the lazy delete-on-access, and nothing in this phase changed its 5-minute interval.
Noted as a known, low-priority limitation, not a bug — revisit only if Cloud Run memory
pressure becomes a real problem.

**Done when:** public HTTPS URL answers a question; image <0.5 GB (revised from <1.5 GB per the
zero-cost mandate); `/ask` rejects traversal (already true).

---

## Phase 6 — Ingestion ⬜

**Branch:** `claude/v8-ingestion` · **1 weekend**

### Source: SEC EDGAR Financial Statement Data Sets (decided 2026-10-03)

Quarterly ZIPs (SUB/NUM/TAG/PRE files) covering every XBRL financial-statement filing since
2009 — chosen over NYC TLC, Citi Bike, Cricsheet, GH Archive, Binance and others for real
messiness (restatements, inconsistent concept names, fiscal-year misalignment, a 2024
full-history republication with a schema change), long-term stability, and the owner's
interest in finance. Kickoff also replaced dbt with PySpark + Delta Lake for Phases 7–10.
**Full decisions, alternatives and architecture: `docs/PHASE6_DESIGN.md`** — the reference for
this phase.

Volume control still applies: BigQuery's free tier is 10 GB storage / 1 TB queried per month,
so the warehouse holds only the compact gold layer, and ingestion is incremental — one quarter
at a time.

**Added:** `ingestion/` (`cli.py`, `storage.py`, `sources/sec_fsds.py`),
`requirements-ingestion.txt`, and unit tests with mocked HTTP and a fixture ZIP (never the
network). CLI: `python -m ingestion --source sec_fsds --quarter 2026q2`, plus `--check`.

**Done when:** the CLI ingests one quarter to partitioned local bronze Parquet, idempotently,
with a row-count assertion; one real quarter's row counts and sizes are recorded in
`docs/PHASE6_DESIGN.md`.

---

## Phase 7 — Spark + Delta Lake lakehouse ⬜

**Branch:** `claude/v9-spark-lakehouse` · **2 weekends** (may need a third — learning Spark and
the SEC format at the same time)

Weekend 1: local Spark setup on the personal laptop (WSL2, Java 17, PySpark, `delta-spark`),
fundamentals, bronze → silver on one quarter. Weekend 2: full-history backfill, gold star
schema, Delta MERGE for restatements, PySpark unit tests in CI, and gold exported to BigQuery
with free batch loads.

**Added (names settled in the phase):** the silver and gold Spark jobs, a concept-mapping seed
(SEC concept → standard metric), PySpark tests using `pyspark.testing.assertDataFrameEqual`, and
`docs/DATA_MODEL.md` explaining the star schema (`fct_financials` plus company, metric and
period dimensions, with company name history as SCD Type 2).

Silver does the cleanup SEC data demands: explicit types, schema differences across years
(`segments`), the latest-filed value per company/metric/period, standard metric names, and a
derived Q4.

**⚠️ This is the phase where money is possible.** BigQuery free tier: 10 GB storage, **1 TB
queried/month**. Before the first load: confirm the **$1 budget alert** is still active, set a
BigQuery custom quota (e.g. 50 GB/day), partition gold by period and cluster by company, and set
`maximum_bytes_billed` on every query.

**Done when:** the full history runs through bronze → silver → gold; gold is queryable in
BigQuery; Spark tests pass in CI.

---

## Phase 8 — Warehouse serving ⬜

**Branch:** `claude/v10-warehouse-serving` · **2 weekends**

The deepest architectural change. **The entire request pipeline is file-path-shaped:**
`analyse(question, file_path)` reads a CSV for `row_count`, then hands `file_path` to agents
that each re-read it. `python_agent.execute_code` does `pd.read_csv(file_path)` then
`exec(code, {"df": df})` — the Python agent *fundamentally assumes one in-memory dataframe*.
Against a multi-million-row gold table, "load it all into `df`" is invalid. This is not a
`database_service` swap; it is a rethink.

**Added:** `src/datasources/` — `base.py` (`DataSource` protocol: `get_schema()`,
`run_query(sql)`, `load_sample(limit)`, `row_count()`), `csv_source.py`, `bigquery_source.py`,
`factory.py`.

**Modified:** `analyse()` takes a `DataSource`, not a `file_path`. `sql_agent` executes via
`source.run_query()` — SQLite for CSV, BigQuery SQL for marts. `python_agent` gets the
**bounded loader** `source.load_sample(limit=settings.max_pandas_rows)` (default 50k), and its
prompt states plainly that `df` is a bounded sample so the model never reports a sample
statistic as a population one. `routes/ask.py` gains `POST /warehouse/ask`.

`database_service.py` largely dissolves into `csv_source.py`. **Phases 1–3's test suite is what
makes this refactor safe** — say that in an interview.

**Cost guard — non-negotiable:** every agent-issued BigQuery query must set
`maximum_bytes_billed`. An LLM emitting `SELECT * FROM fct_financials` is exactly how a free tier
gets breached.

**Done when:** the same question is answered against both a CSV source and the marts source.

---

## Phase 9 — Orchestration ⬜

**Branch:** `claude/v11-orchestration` · **2 weekends**

**Added:** `airflow/dags/sec_fsds_weekly.py` (check and ingest → Spark job → BigQuery load →
DQ), `airflow/docker-compose.yml`, `airflow/README.md` with screenshots for the portfolio;
`deploy/` with the Cloud Run Job definitions (ingestion image in Artifact Registry, Spark image
on GHCR), a Cloud Workflows definition chaining them, and a Cloud Scheduler setup script;
`tests/unit/test_dags.py` (~6 — DAG imports, no cycles, dependencies, retries set).

**Windows caveat:** Airflow does not run natively — use **WSL2 + Docker Compose** (already set
up on the personal laptop in Phase 5). Cloud Composer has **no free tier** (~$300+/mo) — do not
touch it.

Deployed path: Cloud Scheduler (weekly) → Cloud Workflows → Cloud Run Jobs, all inside free tiers
(Scheduler: 3 jobs; Workflows: 5,000 steps/month). Airflow is the local, demonstrable artifact.

**Interview answer:** "I built the DAG in Airflow because it's the industry standard and I
wanted to learn its execution model, but deployed on Cloud Scheduler because Composer costs
$300/month for a portfolio project. Here's the DAG, and here's the same dependency graph
running serverless."

---

## Phase 10 — Data quality ⬜

**Branch:** `claude/v12-data-quality` · **1 weekend**

**Added:** Great Expectations suites run inside the Spark job at each layer — bronze (schema
contract: did the SEC change shape?), silver and gold (`not_null`, uniqueness of company ×
metric × period, accepted values, and the assets = liabilities + equity identity) — plus
`docs/DATA_QUALITY.md` and a freshness check (latest quarter ingested vs the SEC's latest
posting).

**Division of labor to defend:** checks at the **raw boundary** catch source changes (did the
SEC change shape?); checks on **silver and gold** catch broken transformation assumptions.

---

## Phase 11 — LangGraph dual implementation ⬜

**Branch:** `claude/v13-langgraph` · **1 weekend** · **load-bearing, do not cut**

**Do not replace the hand-rolled orchestrator.** LangChain/LangGraph appears in target job
descriptions, so absence is a keyword-screen risk — but rewriting turns the interview question
from "how did you design this?" into "so you followed a tutorial?" A dual implementation solves
both.

**Added:** `src/orchestration/native.py` (today's `analyse()` logic extracted),
`src/orchestration/langgraph_impl.py` (the *same* graph as a `StateGraph`),
`src/orchestration/__init__.py` (selects on `settings.orchestrator`),
`docs/ORCHESTRATION_COMPARISON.md`, `tests/integration/test_orchestrator_parity.py` (~10 — both
implementations must return equivalent `AnalysisResponse` for identical fake-LLM inputs; **this
test is the argument**).

**Modified:** `src/config.py` gains `orchestrator: Literal["native", "langgraph"] = "native"`.
`requirements.txt` adds `langgraph` + `langchain-groq` **only** — not full `langchain`, given
the Docker weight problem.

The comparison doc must contain **real measured numbers**: p50/p95 latency, LOC, dependency size
delta, cold-start delta. A comparison without measurements is an opinion.

---

## Phase 12 — Streamlit demo ⬜

**Branch:** `claude/v14-streamlit` · **1 weekend** · **cut this first if time runs short**

`streamlit_app/app.py` — no auth, no tiers. Upload a CSV *or* query the warehouse; question
box; result + chart; and a sidebar showing which agents ran, the complexity tier, model used,
and attempt count. **That sidebar is the demo's real value** — it makes the orchestration
visible.

**Open idea to revisit here — Plotly instead of matplotlib for charts, if the chart-spec
redesign (Phase 4) lands first:** matplotlib only produces static PNGs, matching today's
JSON-API `chart_path` contract (`AnalysisResponse.chart_path: Optional[str]`) — there's no
browser rendering surface for interactivity anywhere in the app before this phase. Streamlit
*can* natively render an interactive Plotly figure (`st.plotly_chart()`), so once a real UI
surface exists, swapping the chart-spec renderer from matplotlib to Plotly becomes a
low-risk, purely mechanical choice — by that point the LLM only ever produces a small chart
spec (chart type + columns + title), not charting code, so which library actually draws the
chart is an implementation detail, not something exposed to LLM variance. Decide at this
phase whether the interactivity (hover tooltips, zoom, pan) is worth the extra dependency
weight for a portfolio demo; not worth pursuing for the current PNG-returning API alone.

---

## Phase 13 — Polish ⬜

**Branch:** `claude/v15-polish` · **1 weekend**

Architecture diagram, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md` (an ADR log — rare and
genuinely impressive), README rewrite with both framings, CI + coverage badges. Move
`src/groq_all_models.py` to `scripts/` or delete it.

**Optional extras (decided 2026-10-03):** a thin dbt or Dataform layer on top of gold, and a
one-off run of the Spark jobs on Databricks Free Edition to show portability.

**Docs reframe (decided 2026-10-05):** rewrite the docs to focus on the project itself rather
than career or interview framing — e.g. the "Career framing" section and the "Interview answer"
notes.

**Candidate pickup — chart data fidelity (deferred from Phase 4, escalated in Phase 5):**
`docs/BUGS_FOUND.md` findings #10-#12, #15, #16, and **#18** — chart aggregation can silently
render sums where an average was correctly computed, SQL-filtered chart questions can silently
mix in rows the filter excluded, scatter and line/trend charts over-aggregate or show
non-chronological garbage, and (found live during Phase 5, #18) `python_agent` and
`chart_agent` can flatly contradict each other in the same response — a RAG business-context
instruction honored in the text answer is silently ignored in the chart, because chart_agent's
plotted values never pass through an LLM at all, by design. All share one root cause
(`chart_agent` re-derives data from the raw file instead of consuming what python/sql already
computed). The originally-proposed cheap fix (reuse the SQL agent's query string for SQL-routed
charts; stop the duplicate-detection fallback from overriding an explicit aggregation choice;
skip grouping for scatter) still fixes #10-#12/#15/#16, but **does not fix #18** — no partial
fix reaches #18, since the issue isn't a wrong aggregation choice but chart_agent's numbers
being structurally unreachable by any LLM-facing instruction (RAG-derived or otherwise).
**#18 should be this pickup's acceptance criterion**, not just the earlier aggregation-fidelity
cases: the fix isn't done until python_agent and chart_agent are structurally incapable of
disagreeing about the same computed value in the same response — which likely means the
bigger-footprint fix `docs/BUGS_FOUND.md` #10-12 already named as considered-but-deferred
(threading python_agent's actual computed value into chart_agent, not just its printed text) is
no longer optional to skip. Deliberately deferred past Phase 4 and kept out of Phase 5's
Docker+security scope since it's a secondary/decorative feature and neither phase's actual
done-criterion depends on it — revisit at Phase 13, or sooner if convenient.

---

## Candidate Phase 14 — eval harness

Not scheduled. Golden-question sets, routing-accuracy measurement, regression detection on
agent output. **Evals are QA for non-deterministic systems**, and a QA background is a real
differentiator for agent roles where almost no candidate thinks rigorously about test design.
Strong addition if time allows.

---

## Candidate Phase 15+ — multi-user SaaS (post-portfolio only, not scheduled)

**Not part of the 13-phase plan. Does not reopen locked decision #6** ("Monetization killed.
No SaaS tiers, no usage tracking, no API-key billing, no white-label"). That decision still
governs everything through Phase 13. This section exists only so a future idea raised in
conversation (2026-08-28) doesn't get lost — it is explicitly **out of scope until the
portfolio (Phases 1–13) is fully complete**, and picking it up at all is a separate future
decision, not a commitment.

**The idea:** evolve the finished portfolio project into a real multi-user product — user
accounts/login, each user uploading their own CSVs and asking questions, isolated from other
users' data and sessions.

**Why this waits for all 13 phases to finish, not just some of them:** the current
architecture makes assumptions a multi-user product can't share:

- **`session_service.py`'s in-memory `_sessions` dict** has no concept of a user, only an
  ephemeral `session_id` with a 30-minute TTL and no auth — it assumes one operator (you)
  running local demos, not persistent per-user accounts.
- **`exec()` of LLM-generated code is unsandboxed** (`PHASES.md` risk #2) and has a
  process-wide `sys.stdout` concurrency race even after the Phase 3 `redirect_stdout` fix (see
  `docs/BUGS_FOUND.md` #3) — both are dormant under single-operator use and become active
  risks the moment multiple people can hit the server concurrently. A real multi-user login
  product is exactly the scenario that turns "dormant" into "actively exploited," so the
  subprocess/sandboxing isolation discussed for Phase 5 would need to be fully solved first,
  not just documented as deferred.
- **The `/ask` path-traversal hole** (risk #3) is deferred to Phase 5 on the assumption of a
  single trusted operator during the portfolio's demo life; a public multi-tenant login
  product raises the stakes on every unresolved security item in the risk register, not just
  this one.
- **The data platform work itself (Phases 6–10: ingestion, Spark, BigQuery, orchestration, data
  quality)** is the actual point of the portfolio pivot — building a login/billing layer before
  that exists would be building SaaS scaffolding around a project that doesn't have its core
  differentiator yet.

**What it would concretely need, at minimum, whenever it's picked up:** user accounts +
authentication (e.g. a real auth provider, not hand-rolled), per-user data isolation in
storage (not just filename-prefixing, which is what session_id does today), the subprocess/
sandboxing fix for `exec()` (see the concurrency discussion in this phase's chat history —
each code execution needs its own isolated process, which also happens to be the fix for the
`sys.stdout` race), usage limits/rate limiting per user (cost control against the free-tier
ceilings in the Cost Summary table below), and a real decision on hosting cost model since
"free tier" assumptions throughout this plan are sized for one operator's demo traffic, not
multiple concurrent users.

**Status:** parked idea, not a plan. Revisit only after Phase 13 ships, and only as a
deliberate new decision at that time — not something to start pulling forward piece by piece
during Phases 1–13.

---

## Risk register

| # | Risk | Severity | Phase |
|---|---|---|---|
| 1 | App fully dead — all 3 model IDs absent from Groq | Critical | 1 |
| 2 | `exec()` of LLM code = RCE once public; `safe_environment` is not a sandbox | Critical | 5 |
| 3 | `/ask` path traversal — unvalidated caller-supplied `file_path`. **Confirmed live** during Phase 3 (browser reproduction against a real running server read `.env`, including the real `GROQ_API_KEY` — key rotated afterward). Still deferred to Phase 5, but no longer theoretical. | High | 5 |
| 4 | Pipeline is file-path-shaped; Python agent assumes one in-memory df | High | 8 |
| 5 | BigQuery cost blowout — LLM `SELECT *` with no `maximum_bytes_billed` | High | 7, 8 |
| 6 | Docker image ~3.5 GB from torch/faiss for a 3 KB corpus | High | 5 |
| 7 | ~~RAG hardcoded to TechMart — other datasets rejected out-of-scope~~ — **Resolved in Phase 4** via `data_context_service.py`; the underlying hardcoded RAG docs were removed entirely and replaced with Phase 4b's per-session RAG | High | 4 ✅ |
| 8 | ~~Import-time singletons block clean testing~~ — **Resolved in Phase 2.** Confirmed 2026-08-26: `tests/unit/test_cleaners.py` took 35s because instantiating any agent imported `rag_service`, which loaded a real SentenceTransformer at import. Fixed by deferring the `SentenceTransformer`/`faiss` imports and model construction into `RagIndex` methods. Full suite time dropped 32.82s → 2.09s. | High | 2 ✅ |
| 9 | Groq 8K TPM ceiling vs 131k-context models; needs backoff | Medium | 1, 4 |
| 10 | Airflow on Windows needs WSL2; Composer has no free tier | Medium | 9 |
| 11 | ~~`sys.stdout` reassignment corrupts pytest capture~~ — **Partially resolved in Phase 3.** Replaced with `contextlib.redirect_stdout`, which guarantees restoration even on `BaseException`. The underlying concurrency issue (shared process-wide `sys.stdout` under simultaneous requests) is **not** fixed — same root cause as risk #2, deferred there. See `docs/BUGS_FOUND.md` #3. | Medium | 3 ✅ (partial) |
| 12 | 7 redundant `pd.read_csv` calls — up to 6 reads per request | Medium | 4, 8 |
| 13 | ~~Shared-singleton `self.model` mutation — races under concurrency~~ — **Resolved in Phase 2** via `build_agents()` constructing fresh agent instances per request instead of four import-time module singletons | Medium | 2 ✅ |
| 14 | ~~Chart-only plan → `result=None` → Pydantic `ValidationError` → 500~~ — **Resolved in Phase 3.** A chart-only plan is now coerced to `["python", "chart"]` right after the planner call. See `docs/BUGS_FOUND.md` #2. | Medium | 3 ✅ |
| 15 | ~~POSIX-only `split("/")` breaks on Windows paths~~ — **Resolved in Phase 2**, replaced with `os.path.basename()` in `analyst_service.py` | Low | 2 ✅ |
| 16 | Table name interpolated into `to_sql` with only `-`/space sanitized | Low | 1 |
| 17 | `requests` imported by `groq_all_models.py` but not in requirements | Low | 1 |
| 18 | `HTTPException` raised from the service layer — HTTP coupling in domain code | Low | 2 |
| 19 | Global exception handler logs without `exc_info` — opaque failures | Low | 1 |
| 20 | `TEST_RESULTS.md` documents 31 unreproducible results | Low | 3 |
| 21 | `clean_code` is duplicated verbatim in `python_agent.py` and `chart_agent.py`, plus a near-identical `clean_sql` in `sql_agent.py` — found while writing `test_cleaners.py` (2026-08-26). Candidate for a shared helper, not urgent | Low | 2 |
| 22 | `openai/gpt-oss-20b`/`120b` (the only free-tier Groq text models — see Phase 1) are not at parity with current frontier models. Fine for this portfolio's purposes (routing/codegen quality is adequate, and the point is demonstrating architecture, not chasing SOTA benchmarks), but worth revisiting once the project is no longer constrained to a single free-tier provider — e.g. if Phase 5+'s deploy step ever adds a paid-tier or alternate-provider option. Raised 2026-09-11 during Phase 4b manual verification — not a defect, a noted future upgrade candidate. | Low | Candidate — revisit post-13 |
| 23 | RAG's embedding model, `all-MiniLM-L6-v2` (see Phase 5's ONNX swap), is a mid-tier, 2021-era model on MTEB retrieval benchmarks — newer small models (`bge-small-en-v1.5`, `e5-small-v2`, etc.) generally score higher at similar or smaller size. Kept for Phase 5 because (a) this app's actual retrieval task — matching a question against short, plainly-worded, user-authored glossary sentences — is close to the easiest realistic case for any embedding model (validated: the one genuinely-related sentence pair scored 0.76 similarity vs. ~0.0-0.18 for unrelated pairs, an unambiguous gap), (b) it has well-established, battle-tested pre-converted ONNX exports, lowering conversion risk for a phase already doing something new, and (c) model choice is orthogonal to Phase 5's actual done-criteria (deployability/security), so churning it now would mean redoing the quality-parity validation for a change unlikely to be observable in this app's real inputs. Raised 2026-09-17 during the ONNX swap — not a defect, a candidate low-cost upgrade (reuse `scripts/validate_onnx_embedder.py`/`validate_retrieval_threshold.py` against a new candidate model) once the deploy itself is stable. | Low | Candidate — revisit post-5 or ad hoc |
| 24 | `rag_service.py`'s `split_into_chunks()` splits only on blank-line paragraph breaks and drops anything under 5 words — a single unformatted paragraph becomes one giant chunk, and short-but-real definitions (e.g. "Won = closed-won deal.") can be silently dropped. Raised 2026-10-03 as an idea to have an LLM do the chunking instead; recommendation after discussion: a deterministic improvement (sentence-aware splitting, a small overlap between adjacent chunks, a lower/smarter word floor) fits this project's established "LLM for judgment calls, deterministic code for mechanical work" pattern (see Phase 4's chart-agent redesign) better than an LLM chunking call would. Not a defect — this hasn't caused an observed bad result yet — a candidate improvement. | Low | Candidate — revisit ad hoc |
| 25 | Phase 7 overrun — learning Spark and the SEC format at the same time | Medium | 7 |
| 26 | Full-history size (estimated 30–40 GB unzipped) is unverified; measured in Phase 6 Step 6. If it fits comfortably in memory, revisit the larger-than-memory reasoning for Spark (`docs/PHASE6_DESIGN.md` D2) | Low | 6, 7 |
| 27 | GCP Professional Data Engineer exam (Dec 2026/Jan 2027) competes for the same ~10 hrs/week as Phases 7–9; timeline to be aligned once the exam plan is shared | Medium | 7–9 |

## Cost summary — free tiers only

| Service | Free tier | Risk |
|---|---|---|
| Groq | 30 RPM, 1k RPD, 8K TPM, 200k TPD | None — throttled, not billed |
| BigQuery | 10 GB storage, **1 TB query/month** | **Real** — guard with partitioning, `maximum_bytes_billed`, custom quota, $1 budget alert |
| GCS | 5 GB-months, `us-central1`/`us-east1`/`us-west1` only | Low — lake bucket in `us-central1`; `VACUUM` old Delta files; full history stays local |
| Cloud Run | 2M requests, 360k GB-s/mo | Low — cap `max-instances` |
| Cloud Scheduler | 3 jobs | None |
| Artifact Registry | **0.5 GB** | Low — app image (211 MB) plus a small ingestion image; the Spark image goes on GHCR instead |
| Cloud Workflows | 5,000 internal steps/month | None — about 10 per run |
| GitHub Container Registry | Free for public images | None — Spark image is public and holds no secrets |
| Cloud Composer | **NO FREE TIER** | **Avoid entirely** (~$300+/mo) |
| Managed Spark (Dataproc), Dataflow | **NO FREE TIER** | **Avoid** — Spark runs on one machine inside Cloud Run Jobs |
| GitHub Actions | 2,000 min/mo private, unlimited public | None if the repo stays public |

**Set a GCP billing budget alert at $1 before touching BigQuery.** Non-negotiable.
