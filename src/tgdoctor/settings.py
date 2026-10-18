import os
from dataclasses import dataclass
from pathlib import Path


def parse_allowed_user_ids(raw: str) -> frozenset[int]:
    ids: list[int] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            identifier = int(entry)
        except ValueError as exc:
            raise ValueError(f"ALLOWED_USER_IDS: nieprawidłowy identyfikator {entry!r}") from exc
        if identifier <= 0:
            raise ValueError(f"ALLOWED_USER_IDS: identyfikator {entry!r} musi być dodatni")
        ids.append(identifier)
    return frozenset(ids)


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    token: str
    interval_hours: int
    stale_hours: int
    allowed_user_ids: frozenset[int]
    http_host: str
    http_port: int
    environment: str

    @classmethod
    def load(cls) -> Settings:
        interval = int(os.environ.get("IMPORT_INTERVAL_HOURS", "24"))
        stale = int(os.environ.get("STALE_AFTER_HOURS", "72"))
        port = int(os.environ.get("HTTP_PORT", "3000"))
        if not 1 <= interval <= 168 or not 24 <= stale <= 720 or not 1 <= port <= 65535:
            raise ValueError("Nieprawidłowy interwał importu, próg aktualności lub port")
        return cls(
            data_dir=Path(os.environ.get("DATA_DIR", "bot/data")),
            token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            interval_hours=interval,
            stale_hours=stale,
            allowed_user_ids=parse_allowed_user_ids(os.environ.get("ALLOWED_USER_IDS", "")),
            http_host=os.environ.get("HTTP_HOST", "127.0.0.1"),
            http_port=port,
            environment=os.environ.get("ENVIRONMENT", "development"),
        )
