import sqlite3
from pathlib import Path

import pytest

from tgdoctor.allowlist import Allowlist
from tgdoctor.sources import SourceError


def test_initialize_creates_schema(tmp_path: Path) -> None:
    allowlist = Allowlist(tmp_path / "allowlist.sqlite3")
    allowlist.initialize()
    assert (tmp_path / "allowlist.sqlite3").exists()
    assert allowlist.entries() == []


def test_add_is_allowed_remove(tmp_path: Path) -> None:
    allowlist = Allowlist(tmp_path / "allowlist.sqlite3")
    allowlist.initialize()
    assert not allowlist.is_allowed(42)
    assert allowlist.add(42, 1)
    assert allowlist.is_allowed(42)
    assert not allowlist.add(42, 2)
    entries = allowlist.entries()
    assert len(entries) == 1
    assert entries[0].user_id == 42
    assert entries[0].added_by == 1
    assert entries[0].added_at
    assert allowlist.remove(42)
    assert not allowlist.is_allowed(42)
    assert not allowlist.remove(42)


def test_entries_ordered_by_user_id(tmp_path: Path) -> None:
    allowlist = Allowlist(tmp_path / "allowlist.sqlite3")
    allowlist.initialize()
    allowlist.add(7, 1)
    allowlist.add(3, 2)
    assert [entry.user_id for entry in allowlist.entries()] == [3, 7]


def test_initialize_rejects_unsupported_version(tmp_path: Path) -> None:
    path = tmp_path / "allowlist.sqlite3"
    allowlist = Allowlist(path)
    allowlist.initialize()
    db = sqlite3.connect(path)
    db.execute("PRAGMA user_version = 99")
    db.close()
    with pytest.raises(SourceError) as excinfo:
        allowlist.initialize()
    assert excinfo.value.code == "unsupported-database-version"
