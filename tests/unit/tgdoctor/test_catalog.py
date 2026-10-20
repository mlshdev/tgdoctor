import sqlite3
import stat
from datetime import timedelta
from pathlib import Path

import pytest

from tests.conftest import product, publish, snapshot
from tgdoctor.catalog import Catalog
from tgdoctor.models import Attempt, Dataset, Search, now
from tgdoctor.normalize import alternatives, normalize
from tgdoctor.sources import SourceError


def test_initialize_restricts_existing_data_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o755)
    data_dir.chmod(0o755)
    catalog = Catalog(data_dir / "catalog.sqlite3")

    catalog.initialize()

    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700
    assert catalog.path.exists()


@pytest.mark.parametrize(
    "query",
    [
        Search(q="zolc"),
        Search(q="ŻÓŁĆ"),
        Search(name="zolc"),
        Search(substance="alprazolam"),
        Search(substance="alprazolamum"),
        Search(q="alprazolam"),
        Search(atc="n05ba12"),
        Search(atc="N05"),
        Search(gtin="5909991023652"),
        Search(q="05909991023652"),
        Search(form="tabletki", strength="0,5 mg", holder="producent", route="doustna"),
    ],
)
def test_search(populated: Catalog, query: Search) -> None:
    result = populated.search(query)
    assert result.total == 1
    assert result.items[0].id == "001"


def test_no_wildcards_or_sql_injection(populated: Catalog) -> None:
    for value in ("%", "_", "' OR 1=1 --", "nieistniejący"):
        assert populated.search(Search(q=value)).total == 0


def test_deleted_package_filters_must_match_same_package(populated: Catalog) -> None:
    assert populated.search(Search(gtin="5909991023669")).total == 0
    assert populated.search(Search(availability="Rpw")).total == 0
    assert populated.search(Search(gtin="5909991023669", active_only=False)).total == 1
    assert (
        populated.search(Search(gtin="5909991023652", availability="Rpw", active_only=False)).total
        == 0
    )


def test_preserve_identifiers_and_decimal(populated: Catalog) -> None:
    detail = populated.detail(Dataset.MEDICINAL, "001")
    assert detail is not None
    assert detail.packages[0].gtin == "05909991023652"
    assert detail.packages[0].id == "01"
    assert detail.substances[0].amount == "1,54"
    assert detail.source.attributes["moc"] == "0,5 mg"


def test_datasets_are_separate(populated: Catalog) -> None:
    publish(populated, [product(Dataset.RAW)])
    assert populated.search(Search()).total == 2
    assert populated.search(Search(dataset=Dataset.RAW)).total == 1
    assert populated.detail(Dataset.RAW, "001") is not None
    assert populated.snapshot().datasets[Dataset.DEVICES].snapshot is None


def test_fail_closed_and_rollback(populated: Catalog) -> None:
    previous = populated.snapshot()
    with pytest.raises(SourceError, match="too-few"):
        populated.publish(iter([]), snapshot(), 1, Attempt(attempted_at=now()))
    assert populated.snapshot() == previous
    with pytest.raises(sqlite3.IntegrityError):
        publish(populated, [product(), product()])
    assert populated.search(Search()).total == 1


def test_reject_regression_future_and_drop(populated: Catalog) -> None:
    for delta, code in ((-1, "regressed"), (1, "future")):
        with pytest.raises(SourceError, match=code):
            publish(
                populated,
                [product()],
                snapshot().model_copy(
                    update={
                        "as_of": now().date() + timedelta(days=delta),
                    }
                ),
            )
    publish(populated, [product(identity=str(i)) for i in range(10)])
    with pytest.raises(SourceError, match="count-drop"):
        publish(populated, [product()])
    assert populated.search(Search()).total == 10


def test_package_drop(populated: Catalog) -> None:
    with pytest.raises(SourceError, match="package-count-drop"):
        publish(
            populated,
            [
                product().model_copy(
                    update={
                        "packages": [],
                        "package_count": 0,
                        "active_package_count": 0,
                    }
                )
            ],
        )


def test_stale_snapshot(populated: Catalog) -> None:
    assert not populated.snapshot().datasets[Dataset.MEDICINAL].stale
    old = now() - timedelta(hours=100)
    with populated.connect() as db, db:
        current = populated.snapshot(db).datasets[Dataset.MEDICINAL].snapshot
        assert current is not None
        db.execute(
            "UPDATE snapshots SET payload=? WHERE dataset=?",
            (
                current.model_copy(update={"checked_at": old}).model_dump_json(),
                Dataset.MEDICINAL.value,
            ),
        )
    assert populated.snapshot().datasets[Dataset.MEDICINAL].stale


def test_backup_live_wal(populated: Catalog, tmp_path: Path) -> None:
    path = tmp_path / "backup.sqlite3"
    populated.backup(path)
    restored = Catalog(path)
    restored.initialize()
    assert restored.search(Search()).total == 1
    assert restored.snapshot().revision == populated.snapshot().revision
    with pytest.raises(ValueError):
        populated.backup(path)


def test_no_naive_latin_stemming() -> None:
    assert alternatives("Alprazolamum") == alternatives("alprazolam")
    assert alternatives("Calcii carbonas") == ("calcii carbonas",)
    assert normalize("ŻÓŁĆ Łódź") == "zolc lodz"


def test_pagination(populated: Catalog) -> None:
    publish(populated, [product(identity=str(i)) for i in range(10)])
    first = populated.search(Search(limit=5))
    second = populated.search(Search(limit=5, offset=5))
    assert first.total == second.total == 10
    assert {p.id for p in first.items}.isdisjoint(p.id for p in second.items)
