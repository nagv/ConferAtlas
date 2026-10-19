"""Application settings read from the environment (and an optional .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "synthetic"
DEFAULT_INDEX_DIR = PROJECT_ROOT / "data" / "index"

_PROVIDER_KEY_VARS = {
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google_genai": "GOOGLE_API_KEY",  # Google AI Studio (Gemini API), which has a free tier
    "nebius": "NEBIUS_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
}
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
FIREWORKS_BASE_URL = "https://api.fireworks.ai/inference/v1"
# Providers that speak the OpenAI API at their own base URL: env var for the URL, and its default.
# Nebius has no default: copy the base URL your Nebius console shows.
OPENAI_COMPATIBLE = {
    "openrouter": ("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL),
    "nebius": ("NEBIUS_BASE_URL", ""),
    "fireworks": ("FIREWORKS_BASE_URL", FIREWORKS_BASE_URL),
}
# Embedding providers for RAG. "fake" is a deterministic offline embedding used by tests.
EMBEDDING_PROVIDERS = ("nebius", "fireworks", "openai", "google_genai", "fake")


def _provider_key(provider: str) -> str:
    var = _PROVIDER_KEY_VARS.get(provider)
    return os.getenv(var, "").strip() if var else ""


def _provider_base_url(provider: str) -> str | None:
    if provider not in OPENAI_COMPATIBLE:
        return None
    var, default = OPENAI_COMPATIBLE[provider]
    return (os.getenv(var, "").strip() or default).rstrip("/") or None
DEFAULT_PROVIDER = "openrouter"
DEFAULT_MODEL = "google/gemini-2.5-flash"


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    llm_model: str
    data_dir: Path
    llm_timeout_s: float = 60.0            # per request
    llm_max_retries: int = 2               # extra attempts after the first, with exponential backoff
    llm_max_output_tokens: int = 1000      # cap on each response (OpenRouter reserves this much credit)
    llm_temperature: float = 0.0
    input_price_per_mtok: float | None = None   # USD per million input tokens, for cost estimates
    output_price_per_mtok: float | None = None  # USD per million output tokens
    embedding_provider: str = ""           # "" = RAG off; see EMBEDDING_PROVIDERS
    embedding_model: str = ""
    index_dir: Path = DEFAULT_INDEX_DIR

    @property
    def api_key_env_var(self) -> str | None:
        return _PROVIDER_KEY_VARS.get(self.llm_provider)

    @property
    def api_key(self) -> str:
        return _provider_key(self.llm_provider)

    @property
    def base_url(self) -> str | None:
        """OpenAI-compatible endpoint for providers that need one (OpenRouter, Nebius, Fireworks)."""
        return _provider_base_url(self.llm_provider)

    @property
    def llm_configured(self) -> bool:
        value = self.api_key
        if self.llm_provider in OPENAI_COMPATIBLE and not self.base_url:
            return False
        return bool(value) and not value.startswith("your-")

    # --- embeddings (RAG) ---
    @property
    def embedding_api_key(self) -> str:
        return _provider_key(self.embedding_provider)

    @property
    def embedding_base_url(self) -> str | None:
        return _provider_base_url(self.embedding_provider)

    @property
    def embeddings_configured(self) -> bool:
        """True when RAG can embed: the fake provider, or a known provider with model, key and base URL."""
        if self.embedding_provider == "fake":
            return True
        if self.embedding_provider not in EMBEDDING_PROVIDERS or not self.embedding_model:
            return False
        if self.embedding_provider in OPENAI_COMPATIBLE and not self.embedding_base_url:
            return False
        key = self.embedding_api_key
        return bool(key) and not key.startswith("your-")


def _env_number(name: str, default, cast=float):
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:  # a typo in .env should not crash the app; fall back to the default
        return default


def _guard_tracing() -> None:
    """LangChain sends traces to LangSmith when LANGSMITH_TRACING=true. Without a key every traced call
    would log an upload error, so tracing is switched off unless the key is set too."""
    if os.getenv("LANGSMITH_TRACING", "").strip().lower() == "true" and not os.getenv("LANGSMITH_API_KEY", "").strip():
        os.environ["LANGSMITH_TRACING"] = "false"


def tracing_enabled() -> bool:
    return (os.getenv("LANGSMITH_TRACING", "").strip().lower() == "true"
            and bool(os.getenv("LANGSMITH_API_KEY", "").strip()))


def _project_path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else PROJECT_ROOT / path


def get_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / "backend" / ".env", override=False)
    load_dotenv(PROJECT_ROOT / ".env", override=False)  # older single-app setups kept .env at the root
    _guard_tracing()
    data_dir = _project_path(os.getenv("CONFERATLAS_DATA_DIR", str(DEFAULT_DATA_DIR)))
    return Settings(
        llm_provider=(os.getenv("LLM_PROVIDER", "").strip() or DEFAULT_PROVIDER).lower(),
        llm_model=os.getenv("LLM_MODEL", "").strip() or DEFAULT_MODEL,
        data_dir=data_dir,
        llm_timeout_s=max(5.0, _env_number("LLM_TIMEOUT_SECONDS", 60.0)),
        llm_max_retries=max(0, _env_number("LLM_MAX_RETRIES", 2, int)),
        llm_max_output_tokens=max(256, _env_number("LLM_MAX_OUTPUT_TOKENS", 1000, int)),
        llm_temperature=min(2.0, max(0.0, _env_number("LLM_TEMPERATURE", 0.0))),
        input_price_per_mtok=_env_number("LLM_INPUT_PRICE_PER_MTOK", None),
        output_price_per_mtok=_env_number("LLM_OUTPUT_PRICE_PER_MTOK", None),
        embedding_provider=os.getenv("EMBEDDING_PROVIDER", "").strip().lower(),
        embedding_model=os.getenv("EMBEDDING_MODEL", "").strip(),
        index_dir=_project_path(os.getenv("CONFERATLAS_INDEX_DIR", str(DEFAULT_INDEX_DIR))),
    )
