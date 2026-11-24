"""Runtime settings, loaded from environment variables (and `.env`)."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

PROVIDERS = ("gemini", "ollama")


@dataclass(frozen=True)
class ProviderModels:
    light: str  # cheaper/faster model for analysis, triage, summaries
    code: str  # stronger model for code generation

    def for_tier(self, tier: str) -> str:
        return self.code if tier == "code" else self.light


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path
    workspace_dir: Path
    policy_path: Path
    pricing_path: Path
    default_provider: str
    failover: bool
    cache_enabled: bool
    gemini: ProviderModels
    ollama: ProviderModels
    ollama_base_url: str
    ollama_num_ctx: int
    ollama_timeout_s: float
    gemini_rpm: int
    ollama_rpm: int
    request_timeout_s: float
    dev_output_mode: str = "edits"  # edits | full

    @property
    def db_path(self) -> Path:
        return self.data_dir / "forgeline.db"

    @property
    def checkpoint_path(self) -> Path:
        return self.data_dir / "checkpoints.db"

    @property
    def gemini_key_present(self) -> bool:
        return bool(os.getenv("GOOGLE_API_KEY"))

    def models(self, provider: str) -> ProviderModels:
        return {"gemini": self.gemini, "ollama": self.ollama}[provider]

    def with_overrides(self, **changes: object) -> Settings:
        return replace(self, **changes)

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(os.getenv("FORGELINE_DATA_DIR", ROOT / "data"))
        provider = os.getenv("FORGELINE_DEFAULT_PROVIDER", "gemini").strip().lower()
        if provider not in PROVIDERS:
            raise ValueError(f"FORGELINE_DEFAULT_PROVIDER must be one of {PROVIDERS}, got {provider!r}")
        return cls(
            root=ROOT,
            data_dir=data_dir,
            workspace_dir=Path(os.getenv("FORGELINE_WORKSPACE_DIR", ROOT / "workspace")),
            policy_path=Path(os.getenv("FORGELINE_POLICY", ROOT / "policy.yaml")),
            pricing_path=Path(os.getenv("FORGELINE_PRICING", ROOT / "pricing.yaml")),
            default_provider=provider,
            failover=_bool("FORGELINE_FAILOVER", True),
            cache_enabled=_bool("FORGELINE_CACHE", True),
            gemini=ProviderModels(
                light=os.getenv("GEMINI_MODEL_LIGHT", "gemini-3.5-flash-lite"),
                code=os.getenv("GEMINI_MODEL_CODE", "gemini-3.6-flash"),
            ),
            ollama=ProviderModels(
                light=os.getenv("OLLAMA_MODEL_LIGHT", "qwen2.5-coder:3b"),
                code=os.getenv("OLLAMA_MODEL_CODE", "qwen2.5-coder:7b"),
            ),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            # Prompt + full-file output for the developer is ~10k tokens; Ollama silently truncates the
            # START of the prompt (the instructions) when the context overflows, so keep this generous.
            ollama_num_ctx=int(os.getenv("OLLAMA_NUM_CTX", "16384")),
            # Local 7B generation runs at a few tokens/s on a 4 GB GPU; a full-file answer can take minutes.
            ollama_timeout_s=float(os.getenv("OLLAMA_TIMEOUT_S", "1200")),
            gemini_rpm=int(os.getenv("GEMINI_RPM", "10")),
            ollama_rpm=int(os.getenv("OLLAMA_RPM", "0")),
            request_timeout_s=float(os.getenv("FORGELINE_REQUEST_TIMEOUT_S", "180")),
            dev_output_mode=os.getenv("FORGELINE_DEV_OUTPUT", "edits").strip().lower(),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
