import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from tgdoctor.models import now
from tgdoctor.sources import SourceError

SCHEMA = """
CREATE TABLE IF NOT EXISTS allowed_users (
    user_id INTEGER PRIMARY KEY,
    added_by INTEGER NOT NULL,
    added_at TEXT NOT NULL
);
PRAGMA user_version = 1;
"""


@dataclass(frozen=True)
class AllowlistEntry:
    user_id: int
    added_by: int
    added_at: str


class Allowlist:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise SourceError("unsupported-database-version")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)

    def is_allowed(self, user_id: int) -> bool:
        with self.connect() as db:
            row = db.execute("SELECT 1 FROM allowed_users WHERE user_id = ?", (user_id,)).fetchone()
        return row is not None

    def add(self, user_id: int, added_by: int) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO allowed_users"
                " (user_id, added_by, added_at) VALUES (?, ?, ?)",
                (user_id, added_by, now().isoformat()),
            )
            db.commit()
        return cursor.rowcount > 0

    def remove(self, user_id: int) -> bool:
        with self.connect() as db:
            cursor = db.execute("DELETE FROM allowed_users WHERE user_id = ?", (user_id,))
            db.commit()
        return cursor.rowcount > 0

    def entries(self) -> list[AllowlistEntry]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT user_id, added_by, added_at FROM allowed_users ORDER BY user_id"
            ).fetchall()
        return [AllowlistEntry(user_id=row[0], added_by=row[1], added_at=row[2]) for row in rows]
