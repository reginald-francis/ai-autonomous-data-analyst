# Project Plan — Autonomous Data Analyst

## Project Vision

A data platform with an AI agent as its serving layer. SEC EDGAR financial statement data is
checked for updates on a schedule, landed as Parquet, transformed with PySpark and Delta Lake,
served from a BigQuery warehouse, and checked for data quality — and the multi-agent system in this repo (hand-rolled planner, complexity-based
routing, retry logic) answers natural-language questions against it, alongside its original
CSV-upload mode.

## Current Status

The multi-agent serving layer (V1 through V5 below) is complete. **Phase 1 of the data
platform build is in progress.**

The full 13-phase plan, current status, locked architectural decisions, risk register, and
cost limits live in **[`PHASES.md`](PHASES.md)** — that file is the source of truth going
forward. This document stays as the project's vision and history; it does not duplicate the
phase table, to avoid the two documents drifting out of sync with each other.

## History — the multi-agent serving layer

### V1 — LLM + FastAPI (Weeks 1-2) ✅
- [x] Project structure setup
- [x] FastAPI server running
- [x] Health endpoint
- [x] LLM integration with Groq
- [x] /ask endpoint working
- [x] Code execution layer

### V2 — Code Execution Layer (Weeks 3-5) ✅
- [x] Execute LLM generated Python code
- [x] Capture output
- [x] Error handling
- [x] Retry logic
- [x] Fix code function
- [x] Analyst service orchestration
- [x] Structured outputs with AnalysisResponse
- [x] Logging improvements
- [x] Temperature tuning
- [x] Global exception handler

### V3 — RAG Integration (Weeks 6-8) ✅
- [x] Business context documents created
- [x] Data dictionary created
- [x] Sentence transformer embedding model integrated
- [x] Document loading and chunk splitting
- [x] FAISS index built on startup
- [x] Context retrieval with distance threshold
- [x] RAG context injected into LLM prompt

### V4 — Multi Agent Orchestration (Weeks 9-12) ✅
- [x] Planner agent with LLM based routing
- [x] Python agent for data analysis
- [x] SQL agent with SQLite integration
- [x] Chart agent with matplotlib
- [x] Agent orchestration in analyst service
- [x] Structured response with agent metadata
- [x] RAG assisted planner routing rules

### V4.1 — Refactoring & Testing ✅
- [x] Move ask_llm and fix_code into PythonAgent
- [x] Move execute_code and clean_code into PythonAgent
- [x] Keep llm_service.py for shared client and model only
- [x] Delete execution_service.py
- [x] Fix attempts field to reflect actual agent attempts
- [x] Test with different CSV files and schemas
- [x] Test edge cases - empty CSV, missing values, special characters
- [x] Test all agent routing with various question types
- [x] Test retry logic under failure conditions
- [x] Test RAG with questions that have no relevant context
- [x] Add routing_rules.txt to docs for RAG

### V4.2 — Dynamic Model Routing ✅
- [x] Two-call PlannerAgent: Call 1 routing, Call 2 complexity classifier
- [x] MODEL_ROUTING dict + get_model_for_complexity() in llm_service.py
- [x] All agents accept complexity param and return (result, attempts, model) tuple
- [x] High complexity capped at medium for datasets under 500 rows
- [x] Complexity rules added to docs/routing_rules.txt (RAG-retrieved)
- [x] Pandas freq='ME' fix in python agent prompt
- [x] Large test dataset added (tests/data/large_sales.csv, 1000 rows)
- [x] All 9 test scenarios passing at the time — documented in `tests/TEST_RESULTS.md`

### V5 — File Upload & Session Management ✅
- [x] `POST /upload` endpoint with multipart/form-data support
- [x] File validation (CSV only, 10MB max, non-empty, parseable)
- [x] Session-based file retention — upload once, ask many questions
- [x] Session TTL (30 min inactivity) with background cleanup task
- [x] UUID chart filenames — fixed hardcoded `chart.png` collision
- [x] UUID-prefixed DB filenames — fixed SQLite collision
- [x] `session_id` returned in all responses
- [x] Backward compat — `/ask` with `file_path` still works
- [x] 17/17 manually-run test scenarios passing at the time (documented in
      `tests/TEST_RESULTS.md`)

## What comes next

See [`PHASES.md`](PHASES.md) for the full 13-phase plan: resurrecting and testing the existing
system (Phase 1, in progress), a config/DI refactor and CI (Phases 2-3), replacing the
hardcoded RAG context and adding a real, user-supplied RAG use case (Phases 4-4b), Docker +
Cloud Run deployment (Phase 5), then the platform work itself — ingestion, Spark + Delta Lake, BigQuery,
Airflow, data quality (Phases 6-10) — followed by a LangGraph comparison, a Streamlit demo, and
final polish (Phases 11-13).

## Out of scope

No SaaS subscription tiers, no per-user usage tracking or billing, no API key access for
developers, no white-label offering. A minimal Streamlit demo UI is planned (Phase 12) — no
auth, no tiers — purely so the project is demoable in an interview.

## Tech Stack

Current (serving layer): Python, FastAPI, Groq (`openai/gpt-oss-20b` / `openai/gpt-oss-120b`),
Pandas, FAISS, Sentence Transformers, SQLite, Matplotlib, Tabulate, pytest.

Planned (data platform, see `PHASES.md`): PySpark + Delta Lake, BigQuery, Airflow, Cloud
Workflows, Great Expectations, GCS/Parquet, LangGraph, Streamlit.
