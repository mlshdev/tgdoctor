import hashlib
import sqlite3
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from tgdoctor.models import (
    Attempt,
    CatalogSnapshot,
    Dataset,
    DatasetStatus,
    Product,
    ProductSummary,
    Results,
    Search,
    Snapshot,
    now,
)
from tgdoctor.normalize import normalize, pattern, substance_index
from tgdoctor.sources import SourceError

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    dataset TEXT NOT NULL, id TEXT NOT NULL, summary TEXT NOT NULL, detail TEXT NOT NULL,
    name TEXT NOT NULL, substance TEXT NOT NULL, atc TEXT NOT NULL, form TEXT NOT NULL,
    strength TEXT NOT NULL, holder TEXT NOT NULL, route TEXT NOT NULL, kind TEXT NOT NULL,
    searchable TEXT NOT NULL, active_count INTEGER NOT NULL,
    PRIMARY KEY (dataset, id)
);
CREATE TABLE IF NOT EXISTS packages (
    dataset TEXT NOT NULL, product_id TEXT NOT NULL, id TEXT NOT NULL,
    gtin TEXT NOT NULL, availability TEXT NOT NULL, deleted INTEGER NOT NULL,
    PRIMARY KEY (dataset, product_id, id),
    FOREIGN KEY (dataset, product_id) REFERENCES products(dataset, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS package_gtin ON packages(gtin);
CREATE TABLE IF NOT EXISTS snapshots (dataset TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attempts (dataset TEXT PRIMARY KEY, payload TEXT NOT NULL);
PRAGMA user_version = 1;
"""


class Catalog:
    def __init__(self, path: Path, stale_hours: int = 72) -> None:
        self.path = path
        self.stale_hours = stale_hours

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA foreign_keys=ON")
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

    def snapshot(self, db: sqlite3.Connection | None = None) -> CatalogSnapshot:
        if db is None:
            with self.connect() as connection:
                return self.snapshot(connection)
        snapshots = {
            Dataset(row[0]): Snapshot.model_validate_json(row[1])
            for row in db.execute("SELECT dataset, payload FROM snapshots")
        }
        attempts = {
            Dataset(row[0]): Attempt.model_validate_json(row[1])
            for row in db.execute("SELECT dataset, payload FROM attempts")
        }
        revision = hashlib.sha256(
            "|".join(
                f"{dataset}:{snap.xml_sha256}" for dataset, snap in sorted(snapshots.items())
            ).encode()
        ).hexdigest()
        return CatalogSnapshot(
            revision=revision,
            product_count=sum(s.product_count for s in snapshots.values()),
            package_count=sum(s.package_count for s in snapshots.values()),
            datasets={
                dataset: DatasetStatus(
                    snapshot=snapshots.get(dataset),
                    last_attempt=attempts.get(dataset),
                    stale=(
                        dataset not in snapshots
                        or now() - snapshots[dataset].checked_at > timedelta(hours=self.stale_hours)
                        or now().date() - snapshots[dataset].as_of
                        > timedelta(hours=self.stale_hours)
                    ),
                )
                for dataset in Dataset
            },
        )

    def attempt(self, dataset: Dataset, attempt: Attempt) -> None:
        with self.connect() as db, db:
            db.execute(
                "INSERT OR REPLACE INTO attempts VALUES (?, ?)",
                (dataset.value, attempt.model_dump_json()),
            )

    def publish(
        self,
        products: Iterator[Product],
        snapshot: Snapshot,
        minimum: int,
        attempt: Attempt,
    ) -> Snapshot:
        with self.connect() as db, db:
            db.execute("BEGIN IMMEDIATE")
            previous = self.snapshot(db).datasets[snapshot.dataset].snapshot
            if snapshot.as_of > now().date():
                raise SourceError("future-snapshot")
            if previous and snapshot.as_of < previous.as_of:
                raise SourceError("snapshot-date-regressed")
            db.execute("DELETE FROM products WHERE dataset = ?", (snapshot.dataset.value,))
            count = 0
            package_count = 0
            for product in products:
                if product.dataset != snapshot.dataset:
                    raise SourceError("mixed-datasets")
                substance = substance_index(
                    [product.common_name, *(s.name for s in product.substances)]
                )
                name = normalize(f"{product.name} | {product.previous_name}")
                atc = normalize(" | ".join(product.atc_codes))
                form = normalize(product.pharmaceutical_form)
                strength = normalize(product.strength)
                holder = normalize(product.authorization_holder)
                route = normalize(" | ".join(product.routes))
                searchable = " | ".join((name, substance, atc, form, strength, holder, route))
                db.execute(
                    "INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        product.dataset.value,
                        product.id,
                        product.summary().model_dump_json(),
                        product.model_dump_json(),
                        name,
                        substance,
                        atc,
                        form,
                        strength,
                        holder,
                        route,
                        normalize(product.kind),
                        searchable,
                        product.active_package_count,
                    ),
                )
                db.executemany(
                    "INSERT INTO packages VALUES (?,?,?,?,?,?)",
                    [
                        (
                            product.dataset.value,
                            product.id,
                            package.id,
                            package.gtin.zfill(14) if package.gtin else "",
                            normalize(package.availability),
                            int(package.deleted),
                        )
                        for package in product.packages
                    ],
                )
                count += 1
                package_count += product.package_count
            if count < minimum:
                raise SourceError("too-few-products")
            if previous and count < previous.product_count * 0.8:
                raise SourceError("unexpected-count-drop")
            if previous and package_count < previous.package_count * 0.8:
                raise SourceError("unexpected-package-count-drop")
            completed = now()
            imported = snapshot.model_copy(
                update={
                    "product_count": count,
                    "package_count": package_count,
                    "checked_at": completed,
                    "imported_at": (
                        previous.imported_at
                        if previous and previous.xml_sha256 == snapshot.xml_sha256
                        else completed
                    ),
                }
            )
            db.execute(
                "INSERT OR REPLACE INTO snapshots VALUES (?,?)",
                (
                    snapshot.dataset.value,
                    imported.model_dump_json(),
                ),
            )
            db.execute(
                "INSERT OR REPLACE INTO attempts VALUES (?,?)",
                (
                    snapshot.dataset.value,
                    attempt.model_copy(
                        update={
                            "finished_at": completed,
                            "success": True,
                        }
                    ).model_dump_json(),
                ),
            )
            return imported

    def search(self, query: Search) -> Results:
        conditions: list[str] = []
        parameters: list[str | int] = []
        for column in ("name", "substance", "form", "strength", "holder", "route", "kind"):
            value = getattr(query, column)
            if value:
                conditions.append(f"p.{column} LIKE ? ESCAPE '\\'")
                parameters.append(pattern(value))
        if query.dataset is not None:
            conditions.append("p.dataset = ?")
            parameters.append(query.dataset.value)
        if query.atc:
            conditions.append(
                "EXISTS (SELECT 1 FROM json_each(p.summary, '$.atc_codes') "
                "WHERE upper(value) LIKE ?)"
            )
            parameters.append(query.atc + "%")
        gtin = query.gtin
        if query.q:
            if query.q.isascii() and query.q.isdigit() and len(query.q) in (8, 12, 13, 14):
                gtin = query.q
            else:
                for token in normalize(query.q).split():
                    conditions.append("p.searchable LIKE ? ESCAPE '\\'")
                    parameters.append(pattern(token))
        if gtin or query.availability:
            package_conditions: list[str] = []
            if query.active_only:
                package_conditions.append("k.deleted=0")
            if gtin:
                package_conditions.append("k.gtin=?")
                parameters.append(gtin.zfill(14))
            if query.availability:
                package_conditions.append("k.availability=?")
                parameters.append(normalize(query.availability))
            conditions.append(
                "(p.dataset,p.id) IN (SELECT k.dataset,k.product_id FROM packages k WHERE "
                + " AND ".join(package_conditions)
                + ")"
            )
        elif query.active_only:
            # Devices need not have a GTIN; this exception never applies to RPL products.
            conditions.append("(p.active_count > 0 OR p.dataset='medical-devices')")
        where = " AND ".join(conditions) or "1"
        with self.connect() as db:
            db.execute("BEGIN")
            total = int(
                db.execute(f"SELECT count(*) FROM products p WHERE {where}", parameters).fetchone()[
                    0
                ]
            )
            rows = db.execute(
                f"SELECT p.summary FROM products p WHERE {where} "
                "ORDER BY p.name,p.dataset,p.id LIMIT ? OFFSET ?",
                [*parameters, query.limit, query.offset],
            )
            return Results(
                items=[ProductSummary.model_validate_json(row[0]) for row in rows],
                total=total,
                limit=query.limit,
                offset=query.offset,
                snapshot=self.snapshot(db),
            )

    def detail(self, dataset: Dataset, product_id: str) -> Product | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT detail FROM products WHERE dataset=? AND id=?", (dataset.value, product_id)
            ).fetchone()
            return Product.model_validate_json(row[0]) if row else None

    def backup(self, destination: Path) -> None:
        if destination.resolve() == self.path.resolve() or destination.exists():
            raise ValueError("Kopia musi wskazywać nowy plik poza bieżącą bazą")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise SourceError("backup-integrity-failed")
            finally:
                target.close()
