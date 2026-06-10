"""Runtime configuration, loaded from environment variables.

Everything Azure-specific is optional so the app can run in MOCK mode locally
(and in CI) with no cloud credentials. This also gives us a clean offline demo
path and keeps secrets out of the repo.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Azure AI Vision (Image Analysis 4.0 / Read) — verbatim OCR.
    vision_endpoint: str = ""
    vision_key: str = ""

    # Azure OpenAI (GPT-4o vision) — semantic field extraction.
    aoai_endpoint: str = ""
    aoai_key: str = ""
    aoai_deployment: str = "gpt-4o"
    aoai_api_version: str = "2024-10-21"
    # Image detail sent to the VLM. "low" (single 512px tile) is faster and, when
    # paired with the verbatim OCR text as grounding, is also MORE accurate on
    # small print than "high" vision alone (OCR reads fine text the VLM misses).
    aoai_image_detail: str = "low"

    # When true (or when credentials are absent), use deterministic stub
    # responses instead of calling Azure. Lets the whole app run offline.
    mock_vision: bool = False

    # Production guard: when true, the app refuses to start in mock mode. This
    # prevents a misconfigured deploy (missing/typo'd key) from silently serving
    # fabricated PASS verdicts instead of failing loudly. Set REQUIRE_AZURE=true
    # in any real environment.
    require_azure: bool = False

    # Batch concurrency — how many labels we process in parallel. Tuned to hit
    # the per-label latency target without hammering Azure rate limits.
    batch_concurrency: int = 8

    # Resource limits (abuse / DoS protection).
    max_upload_mb: int = 10          # per-image upload cap
    max_batch_files: int = 400       # cap files per batch request
    rate_limit_per_min: int = 60     # per-IP request budget on /api/*
    daily_call_cap: int = 1500       # global/day cost circuit breaker (per process)

    # Timeouts so a stalled Azure call fails cleanly instead of hanging.
    aoai_timeout_s: float = 25.0     # per gpt-4o request
    request_timeout_s: float = 45.0  # whole-bottle verification budget

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def vision_ready(self) -> bool:
        return bool(self.vision_endpoint and self.vision_key)

    @property
    def aoai_ready(self) -> bool:
        return bool(self.aoai_endpoint and self.aoai_key)

    @property
    def use_mock(self) -> bool:
        # Fall back to mock if either backend is unconfigured.
        return self.mock_vision or not (self.vision_ready and self.aoai_ready)


settings = Settings()
