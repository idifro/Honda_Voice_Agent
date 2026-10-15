import pytest
from pydantic import ValidationError

from honda_voice.config import Settings


def test_defaults_without_env_file():
    s = Settings(_env_file=None)
    assert s.stt_model == "saaras:v4"
    assert s.greeting_language in s.supported_languages


def test_supported_languages_parsed_from_csv(monkeypatch):
    monkeypatch.setenv("SUPPORTED_LANGUAGES", "hi-IN, ta-IN")
    monkeypatch.setenv("GREETING_LANGUAGE", "hi-IN")
    assert Settings(_env_file=None).supported_languages == ["hi-IN", "ta-IN"]


def test_greeting_language_must_be_supported(monkeypatch):
    monkeypatch.setenv("SUPPORTED_LANGUAGES", "hi-IN,en-IN")
    monkeypatch.setenv("GREETING_LANGUAGE", "ta-IN")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_secrets_are_not_printed(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "super-secret-value")
    assert "super-secret-value" not in repr(Settings(_env_file=None))
