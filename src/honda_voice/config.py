"""Settings loaded from the environment / `.env` (SPEC §13). Secrets stay as SecretStr."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LiveKit
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: SecretStr = SecretStr("")

    # Sarvam STT/TTS
    sarvam_api_key: SecretStr = SecretStr("")
    stt_model: str = "saaras:v4"
    stt_mode: str = "codemix"
    stt_language: str = "unknown"
    tts_model: str = "bulbul:v4-flash"
    tts_speaker: str = "aarti_hi_customer"
    sarvam_tts_dict_id: str | None = None
    supported_languages: Annotated[list[str], NoDecode] = Field(
        default=["hi-IN", "en-IN", "ta-IN", "te-IN", "ml-IN", "kn-IN"]
    )
    greeting_language: str = "ta-IN"

    # LLM
    llm_provider: str = "azure"
    llm_model: str = "gpt-5.4-mini"
    llm_reasoning_effort: str = "none"
    llm_max_tokens: int = 150
    azure_openai_endpoint: str = ""
    azure_openai_api_key: SecretStr = SecretStr("")
    azure_openai_deployment: str = ""
    azure_openai_deployment_alt: str = ""
    azure_openai_api_version: str = "2024-12-01-preview"
    # Resilience (Phase 5+): a stalled request (no bytes for this long) is abandoned and retried; normal first
    # token is ~1 s. A hang on 2026-10-16 waited the 10 s default.
    llm_attempt_timeout_s: float = 4.0
    llm_max_retry: int = 1  # quick retries on the same deployment before failing over
    # Optional fallback deployment: on errors, 429s or stalls the session switches to it, and back to the
    # primary once a background check sees it healthy again. Endpoint/key default to the primary's.
    azure_openai_fallback_deployment: str = ""
    azure_openai_fallback_model: str = ""  # e.g. gpt-5.4-mini; decides whether reasoning_effort is sent
    azure_openai_fallback_endpoint: str = ""
    azure_openai_fallback_api_key: SecretStr = SecretStr("")

    # Google Calendar mirror (SPEC §11): write-only copy of bookings, never blocks a booking (rule 8)
    calendar_sync: str = "off"  # on | off
    google_calendar_id: str = ""
    google_service_account_json: str = ""  # path to the key file (git-ignored secrets/)

    # Development log file (until development is complete): INFO and above, JSON lines, rotated. Phone
    # numbers are masked at INFO; LiveKit logs tool arguments (which can hold them) only at DEBUG. Empty: off.
    log_file: str = "logs/worker.log"

    # Call limits (Phase 5): every call costs LiveKit, Sarvam and Azure usage; a forgotten tab mustn't run on
    max_call_minutes: float = 10
    # Calls one worker process accepts at once; beyond this LiveKit sends new calls elsewhere (or they wait),
    # so a burst can't exceed the Azure/Sarvam quotas.
    max_concurrent_calls: int = 3

    # Hold line: after this much "thinking", a short fixed line ("One moment please, let me check.").
    hold_after_s: float = 2.0
    noise_cancellation: Literal["bvc", "nc", "off"] = "bvc"  # caller audio filter, worker.room_options

    # Sales advisor (SPEC §20): per-turn caller signals in parallel with the reply, never delaying audio.
    # on (default since step 7.3): + one suggested move per reply; shadow: recorded only, call unchanged.
    advisor: Literal["off", "shadow", "on"] = "on"
    signal_provider: Literal["nano", "laya"] = "nano"
    signal_deployment: str = ""  # Azure deployment for nano; empty: AZURE_OPENAI_FALLBACK_DEPLOYMENT
    signal_reasoning_effort: str = "minimal"
    signal_timeout_s: float = 8.0  # a late answer is dropped; the next turn asks again
    laya_base_url: str = "http://127.0.0.1:8100"  # SIGNAL_PROVIDER=laya only (SPEC §23)
    laya_model: str = "multilingual"

    # Database / showroom
    database_url: SecretStr = SecretStr("")
    database_ca_cert: str = "certs/supabase-prod-ca-2021.crt"  # verify the DB's TLS cert; empty: don't
    showroom_id: str = "demo-showroom"
    business_tz: str = "Asia/Kolkata"

    @field_validator("supported_languages", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [part.strip() for part in v.split(",") if part.strip()]
        return v

    @field_validator("sarvam_tts_dict_id", mode="before")
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        return v or None

    @model_validator(mode="after")
    def _greeting_language_supported(self) -> "Settings":
        if self.greeting_language not in self.supported_languages:
            raise ValueError(f"GREETING_LANGUAGE {self.greeting_language!r} not in SUPPORTED_LANGUAGES")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _azure_llm(settings: Settings, *, model: str, deployment: str, endpoint: str, api_key: str):
    from livekit.plugins import openai

    kwargs = {}
    if model.startswith("gpt-5"):  # gpt-4.1-* rejects reasoning_effort
        kwargs["reasoning_effort"] = settings.llm_reasoning_effort
    return openai.LLM.with_azure(
        model=model,
        azure_endpoint=endpoint,
        azure_deployment=deployment,
        api_version=settings.azure_openai_api_version,
        api_key=api_key,
        max_completion_tokens=settings.llm_max_tokens,
        **kwargs,
    )


def build_llm(
    settings: Settings,
):  # -> livekit.agents.llm.LLM (imported lazily: config stays cheap to import)
    """The live-path LLM (SPEC §4): Azure OpenAI, lowest reasoning effort, short replies, no temperature.

    With AZURE_OPENAI_FALLBACK_DEPLOYMENT set, a FallbackAdapter: each attempt is abandoned after
    LLM_ATTEMPT_TIMEOUT_S without bytes and retried LLM_MAX_RETRY times, then the fallback takes over; the
    primary is probed in the background and used again once it answers. Rate limits (429) count as errors.
    """
    if settings.llm_provider != "azure":
        raise ValueError(f"LLM_PROVIDER={settings.llm_provider!r} is not wired up yet; use 'azure'")
    primary = _azure_llm(
        settings,
        model=settings.llm_model,
        deployment=settings.azure_openai_deployment,
        endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key.get_secret_value(),
    )
    if not settings.azure_openai_fallback_deployment:
        return primary
    from livekit.agents.llm import FallbackAdapter

    fallback = _azure_llm(
        settings,
        model=settings.azure_openai_fallback_model or settings.llm_model,
        deployment=settings.azure_openai_fallback_deployment,
        endpoint=settings.azure_openai_fallback_endpoint or settings.azure_openai_endpoint,
        api_key=(
            settings.azure_openai_fallback_api_key.get_secret_value()
            or settings.azure_openai_api_key.get_secret_value()
        ),
    )
    return FallbackAdapter(
        [primary, fallback],
        attempt_timeout=settings.llm_attempt_timeout_s,
        max_retry_per_llm=settings.llm_max_retry,
        retry_interval=0.3,
    )


def llm_conn_options(settings: Settings):  # -> livekit.agents.APIConnectOptions
    """Per-attempt stall timeout and quick retries for a single deployment (no fallback configured)."""
    from livekit.agents import APIConnectOptions

    return APIConnectOptions(
        max_retry=settings.llm_max_retry + 1, retry_interval=0.3, timeout=settings.llm_attempt_timeout_s
    )
