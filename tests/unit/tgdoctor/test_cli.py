from pathlib import Path

import pytest

from tgdoctor.cli import require_run_settings
from tgdoctor.settings import Settings


def settings(
    tmp_path: Path,
    token: str = "123456:TEST_TOKEN_NOT_REAL",
    allowed: frozenset[int] = frozenset({42}),
) -> Settings:
    return Settings(
        data_dir=tmp_path,
        token=token,
        interval_hours=24,
        stale_hours=72,
        allowed_user_ids=allowed,
        http_host="127.0.0.1",
        http_port=3000,
        environment="test",
    )


def test_require_run_settings_accepts_token_and_allowlist(tmp_path: Path) -> None:
    assert require_run_settings(settings(tmp_path)) is None


def test_require_run_settings_requires_real_token(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        require_run_settings(settings(tmp_path, token="replace-with-token-from-botfather"))
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        require_run_settings(settings(tmp_path, token=""))


def test_require_run_settings_requires_non_empty_allowlist(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ALLOWED_USER_IDS"):
        require_run_settings(settings(tmp_path, allowed=frozenset()))
