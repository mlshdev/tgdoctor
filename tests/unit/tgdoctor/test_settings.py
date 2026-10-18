from pathlib import Path

import pytest

from tgdoctor.settings import Settings, parse_allowed_user_ids


def test_parse_allowed_user_ids_accepts_comma_list() -> None:
    assert parse_allowed_user_ids("42, 43") == frozenset({42, 43})


def test_parse_allowed_user_ids_empty_entries_and_duplicates() -> None:
    assert parse_allowed_user_ids("") == frozenset()
    assert parse_allowed_user_ids(" , , ") == frozenset()
    assert parse_allowed_user_ids("42,42") == frozenset({42})


@pytest.mark.parametrize("value", ["abc", "42,x", "0", "-7", "12.5", "42 43"])
def test_parse_allowed_user_ids_rejects_invalid(value: str) -> None:
    with pytest.raises(ValueError, match="ALLOWED_USER_IDS"):
        parse_allowed_user_ids(value)


def test_settings_load_reads_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TEST_TOKEN_NOT_REAL")
    monkeypatch.setenv("ALLOWED_USER_IDS", "42, 43")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("IMPORT_INTERVAL_HOURS", "12")
    loaded = Settings.load()
    assert loaded.allowed_user_ids == frozenset({42, 43})
    assert loaded.environment == "test"
    assert loaded.interval_hours == 12
    assert loaded.token == "123456:TEST_TOKEN_NOT_REAL"


def test_settings_load_defaults_to_empty_allowlist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ALLOWED_USER_IDS", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    loaded = Settings.load()
    assert loaded.allowed_user_ids == frozenset()
    assert loaded.token == ""
    assert loaded.environment == "development"
