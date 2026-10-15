"""Settings loaded from the environment / `.env` (SPEC §13). Secrets stay as SecretStr."""

from functools import lru_cache
from typing import Annotated

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

    # Database / showroom
    database_url: SecretStr = SecretStr("")
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


def build_llm(
    settings: Settings,
):  # -> livekit.agents.llm.LLM (imported lazily: config stays cheap to import)
    """The live-path LLM (SPEC §4): Azure OpenAI, lowest reasoning effort, short replies, no temperature."""
    from livekit.plugins import openai

    if settings.llm_provider != "azure":
        raise ValueError(f"LLM_PROVIDER={settings.llm_provider!r} is not wired up yet; use 'azure'")
    kwargs = {}
    if settings.llm_model.startswith("gpt-5"):  # gpt-4.1-* rejects reasoning_effort
        kwargs["reasoning_effort"] = settings.llm_reasoning_effort
    return openai.LLM.with_azure(
        model=settings.llm_model,
        azure_endpoint=settings.azure_openai_endpoint,
        azure_deployment=settings.azure_openai_deployment,
        api_version=settings.azure_openai_api_version,
        api_key=settings.azure_openai_api_key.get_secret_value(),
        max_completion_tokens=settings.llm_max_tokens,
        **kwargs,
    )
