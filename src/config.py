"""
Centralized configuration. Owns every value that was previously hardcoded
across llm_service, session_service, rag_service, database_service, the
agents, and routes/ask.py — model IDs, retry/prompt tiering, TTLs, file size
limits, paths, embedding model, RAG tuning, and LLM temperatures.

Settings() reads from environment variables (and a .env file, via
pydantic-settings' built-in dotenv support) with defaults matching current
behavior, so existing deployments keep working with zero env changes.
Use get_settings() rather than constructing Settings() directly — it caches
a single instance per process, mirroring the old module-level constants.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Groq API ---
    groq_api_key: str = ""

    # --- Model routing ---
    # Only two usable free-tier text models exist on Groq (see llm_service.py
    # history) — "medium" and "high" intentionally share a model ID, with
    # retry budget and prompt richness carrying the rest of the distinction.
    model_low: str = "openai/gpt-oss-20b"
    model_medium: str = "openai/gpt-oss-120b"
    model_high: str = "openai/gpt-oss-120b"
    default_model: str = "openai/gpt-oss-120b"

    retry_budget_low: int = 2
    retry_budget_medium: int = 3
    retry_budget_high: int = 5

    # Minimum rows required to justify the high complexity model/tier
    high_complexity_row_threshold: int = 500

    # --- LLM call temperatures ---
    temperature_routing: float = 0.1      # planner: agent routing call
    temperature_complexity: float = 0.0   # planner: complexity classifier call
    temperature_codegen: float = 0.1      # python/sql/chart code generation

    # --- Sessions ---
    session_ttl_minutes: int = 30

    # --- /ask endpoint ---
    # /ask takes a caller-supplied file_path with no auth — fine for local
    # dev/testing (see routes/ask.py's docstring), a live arbitrary-file-read
    # risk on a public URL (see PHASES.md risk #3, confirmed live in Phase 3).
    # Off by default so a deployed instance doesn't expose it without an
    # explicit opt-in; the path-traversal allowlist in routes/ask.py applies
    # regardless of this flag, as defense in depth.
    enable_ask_endpoint: bool = False

    # --- Uploads ---
    max_file_size: int = 10 * 1024 * 1024  # 10MB
    max_context_doc_size: int = 2 * 1024 * 1024  # 2MB — business-context text/markdown doc

    # --- Public URL (optional) ---
    # Empty by default — local dev, CI, and tests all leave this unset, so
    # chart_url (see analyst_service.py) stays a relative path exactly as
    # before. Set to the deployed Cloud Run URL (e.g. via `gcloud run
    # deploy --update-env-vars PUBLIC_BASE_URL=https://...`) to make
    # chart_url an absolute, directly-fetchable link instead — lets a
    # caller use it as-is (an <img src>, a shared link) without having to
    # know or prepend the server's own base URL itself.
    public_base_url: str = ""

    # --- Paths ---
    data_dir: str = "data"
    uploads_dir: str = "data/uploads"
    charts_dir: str = "data/charts"
    db_dir: str = "data"

    # --- RAG ---
    # Phase 5: swapped from sentence-transformers (torch + transformers,
    # ~2.5GB installed) to onnxruntime + tokenizers + a quantized ONNX
    # export of the same model, bundled into the image at build time —
    # never downloaded at runtime. Quality-parity verified against the
    # original model in scripts/validate_onnx_embedder.py and
    # scripts/validate_retrieval_threshold.py: quantization shifts raw
    # similarity values slightly but changes zero retrieve/don't-retrieve
    # decisions at this app's actual rag_distance_threshold.
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_model_path: str = "models/all-MiniLM-L6-v2-onnx"
    rag_top_k: int = 3
    rag_distance_threshold: float = 1.5

    @property
    def model_routing(self) -> dict:
        return {"low": self.model_low, "medium": self.model_medium, "high": self.model_high}

    @property
    def retry_budget(self) -> dict:
        return {"low": self.retry_budget_low, "medium": self.retry_budget_medium, "high": self.retry_budget_high}


@lru_cache
def get_settings() -> Settings:
    return Settings()
