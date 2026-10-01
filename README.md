# Autonomous Data Analyst

[![CI](https://github.com/reginald-francis/ai-autonomous-data-analyst/actions/workflows/ci.yml/badge.svg)](https://github.com/reginald-francis/ai-autonomous-data-analyst/actions/workflows/ci.yml)

A data platform with an AI agent as its serving layer. A public dataset is ingested on a
schedule, landed in raw storage, modeled through dbt into a warehouse, and checked for data
quality — and the multi-agent system in this repo answers natural-language questions against
it, alongside its original CSV-upload mode. The agent side is hand-rolled — a planner,
complexity-based routing, and a retry loop — rather than built on a framework, with built-in
SQL querying, chart generation, and structured responses.

## What it does
- Accepts CSV file input, or (once the platform lands) queries a warehouse directly
- Generates a dataset-agnostic context (row/column counts, dtypes, categorical values, numeric
  ranges) from whatever file is actually uploaded — works on any CSV, not just the sample data
- Accepts an optional business-context document alongside the CSV (a column glossary,
  terminology, fiscal-calendar notes — anything a developer couldn't pre-write) and retrieves
  the relevant parts of it per question via a session-scoped RAG index
- Routes questions to specialized agents using an LLM powered planner
- Answers analytical questions using a Python agent with pandas
- Queries data using a SQL agent with SQLite
- Generates charts using an LLM-chosen chart spec (type + columns + aggregation) rendered by
  deterministic Python — the LLM never writes charting code, only picks parameters
- Retries automatically if generated code fails, with a complexity-scaled retry budget
- Returns structured responses with full metadata

## Live Demo

`https://agent-1079929934435.us-central1.run.app` — deployed on Cloud Run (see `PHASES.md`
Phase 5). Scales to zero when idle, so the first request after a quiet period may take a few
extra seconds (cold start).

## Tech Stack
- Python, FastAPI
- Groq (LLM provider) — `openai/gpt-oss-20b` (fast/cheap) and `openai/gpt-oss-120b`
  (strongest), dynamically routed by question complexity
- Pandas, SQLite
- FAISS, onnxruntime + tokenizers (quantized ONNX embeddings — see `PHASES.md` Phase 5) —
  per-session RAG (Phase 4b) over an optional user-uploaded business-context document; the
  original static `docs/` corpus was removed in Phase 4 once it turned out redundant with
  hardcoded prompt rules (see `PHASES.md` Phase 4)
- Matplotlib, Tabulate
- pydantic-settings — centralized config (Phase 2)
- pytest — 200+ automated tests as of Phase 5 (see `PHASES.md`)
- ruff (lint) + GitHub Actions CI — matrix Python 3.11/3.13 (Phase 3)
- Docker + Google Cloud Run — deployed, Phase 5 (see Live Demo above)

Planned as the platform builds out: dbt, BigQuery, Airflow, Great Expectations,
LangGraph, Streamlit. See [`PHASES.md`](PHASES.md) for the full plan.

## Project Phases

The multi-agent serving layer (V1 through V5) is complete — see
[`PROJECT_PLAN.md`](PROJECT_PLAN.md) for that history. The data platform build is now
in progress; see [`PHASES.md`](PHASES.md) for the current phase, the full 13-phase plan, and
locked architectural decisions.

## Setup

1. Clone the repository

2. Create a virtual environment and activate it
```
source venv/Scripts/activate
```

3. Install dependencies (this pulls in `requirements.txt` too, plus pytest/ruff for
   running the test suite below — the deployed image installs only `requirements.txt`)
```
pip install -r requirements-dev.txt
```

4. Create a `.env` file and add your Groq API key
```
GROQ_API_KEY=your_api_key_here
```

5. Run the server
```
uvicorn src.main:app --reload
```

## Tests

```bash
pytest                    # unit tests only (default) — no real API calls
pytest -m live_api        # the handful of tests that hit Groq for real
pytest --cov=src --cov-report=term-missing
```

## API Endpoints

### GET /
Welcome message

### GET /health
Health check endpoint

### POST /upload
Upload a CSV file and ask a question. Returns a `session_id` for follow-up questions.

**First request (upload file):**
```bash
curl -X POST http://localhost:8000/upload \
  -F "question=What is total revenue by region?" \
  -F "file=@sample_data.csv"
```

**Follow-up request (reuse session, no re-upload needed):**
```bash
curl -X POST http://localhost:8000/upload \
  -F "question=Show me a bar chart of that" \
  -F "session_id=<session_id_from_response>"
```

**With an optional business-context document** (a column glossary, terminology, or similar —
text describing this specific dataset that a developer couldn't have pre-written):
```bash
curl -X POST http://localhost:8000/upload \
  -F "question=What does won mean in this data?" \
  -F "file=@sample_data.csv" \
  -F "context_file=@business_context.txt"
```
The context document is scoped to that session only — it's retrieved for later questions in the
same session without needing to be re-uploaded, and never affects any other session.

Sessions expire after 30 minutes of inactivity.

### POST /ask
Ask a question using a local file path. Kept for development and local testing — disabled by
default (`Settings.enable_ask_endpoint`); the path-traversal fix landed in Phase 5
(see `PHASES.md`).

```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "what is total revenue by region?", "file_path": "sample_data.csv"}'
```

**Response (both endpoints):**
```json
{
    "question": "what is total revenue by region?",
    "result": "...",
    "status": "success",
    "attempts": 1,
    "time_taken": "2.1s",
    "model_used": "openai/gpt-oss-120b",
    "row_count": 12,
    "column_count": 4,
    "file_name": "sample_data.csv",
    "timestamp": "2026-03-30 10:10:10",
    "agents_used": ["python"],
    "task_type": "analysis",
    "reasoning": "question asks for calculation so python agent is used",
    "complexity": "low",
    "chart_path": null,
    "chart_url": null,
    "session_id": "7455452f-f6be-4770-93d9-f24186779432"
}
```

## Architecture (High Level)
```
POST /upload (multipart: file + question)
  │
  ├─ Validate file (CSV, ≤10MB, non-empty, parseable) → 400 on failure
  ├─ Save to data/uploads/{session_id}.csv
  ├─ Register session (30-min TTL, background cleanup every 5 min)
  │
POST /upload (multipart: session_id + question)  ← follow-up, no re-upload
  │
  └─ Lookup session → reuse saved file path
  │
POST /ask (JSON: file_path + question)  ← local dev/testing only
  │
  └─────────────────────────────────────┐
                                        ↓
                                 Analyst Service
                                        │
                                        ├─ Empty CSV? → 400 Bad Request
                                        │
                                        ↓
                              Data Context Service
                       generates dataset-agnostic context
                    (row/col counts, dtypes, categorical values,
                      numeric ranges) — pure pandas, no LLM call
                                        │
                                        ↓
                                Planner Agent (Two-Call)
                         Call 1: routing → agents + task_type
                         Call 2: complexity → low/medium/high
                          (capped at medium if row_count < 500)
                                        │
                        ┌───────────────┼──────────┬─────────────┐
                        ↓               ↓          ↓             ↓
                   Python Agent     SQL Agent  Chart Agent  Out of Scope
                   LLM+Pandas     LLM+SQLite  LLM picks a    → clear message
                        │               │      chart spec;
                        │               │      deterministic
                        │               │      code renders it
                        └───────────────┴──────────┘
                  Dynamic Model per Complexity:
                  low         → openai/gpt-oss-20b
                  medium/high → openai/gpt-oss-120b
                  Complexity also scales retry budget (2/3/5 attempts)
                  DB files named with session_id; chart PNGs get a random
                  UUID filename instead, served at /charts/{uuid}.png
                  (session_id-named charts would leak a session's access
                  credential into a shared/logged URL — see THREAT_MODEL.md)
                                        │
                                        ↓
                                 AnalysisResponse
                          (result, status, attempts, model_used,
                           agents_used, chart_path, chart_url, session_id, ...)
```

Once the data platform lands (see `PHASES.md`), a second path queries a BigQuery warehouse
directly instead of an uploaded CSV — the SQL agent runs against the warehouse, and the Python
agent works against a bounded sample pulled from it, behind a shared `DataSource` abstraction.
