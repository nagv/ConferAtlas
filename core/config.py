"""Application settings read from the environment (and an optional .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "synthetic"

_PROVIDER_KEY_VARS = {
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
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

    @property
    def api_key_env_var(self) -> str | None:
        return _PROVIDER_KEY_VARS.get(self.llm_provider)

    @property
    def api_key(self) -> str:
        var = self.api_key_env_var
        return os.getenv(var, "").strip() if var else ""

    @property
    def base_url(self) -> str | None:
        """OpenAI-compatible endpoint for providers that need one (OpenRouter)."""
        if self.llm_provider != "openrouter":
            return None
        return (os.getenv("OPENROUTER_BASE_URL", "").strip() or OPENROUTER_BASE_URL).rstrip("/")

    @property
    def llm_configured(self) -> bool:
        value = self.api_key
        return bool(value) and not value.startswith("your-")


def _env_number(name: str, default, cast=float):
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:  # a typo in .env should not crash the app; fall back to the default
        return default


def get_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    data_dir = Path(os.getenv("CONFERATLAS_DATA_DIR", str(DEFAULT_DATA_DIR)))
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir
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
    )
