import fcntl
import hashlib
import json
import logging
import tempfile
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from time import monotonic
from xml.etree.ElementTree import Element

import httpx
from defusedxml.ElementTree import fromstring
from pydantic import BaseModel, ConfigDict

from tgdoctor.catalog import Catalog
from tgdoctor.models import (
    Attempt,
    Dataset,
    DeviceFeed,
    Package,
    Product,
    Snapshot,
    SourceNode,
    now,
)
from tgdoctor.sources import (
    COMMON_NAMESPACE,
    COMMON_TYPES,
    CONTRACTS,
    Contract,
    SourceError,
    official_url,
    public_link,
)
from tgdoctor.xmlfeed import records

LOGGER = logging.getLogger(__name__)


class MetadataAttributes(BaseModel):
    link: str
    format: str
    data_date: date


class Identity(BaseModel):
    id: str
    type: str = "dataset"


class Relationship(BaseModel):
    data: Identity


class Relationships(BaseModel):
    dataset: Relationship


class Resource(BaseModel):
    id: str
    type: str
    attributes: MetadataAttributes
    relationships: Relationships


class Metadata(BaseModel):
    model_config = ConfigDict(extra="ignore")
    data: Resource


@contextmanager
def exclusive(path: Path) -> Generator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SourceError("writer-already-running") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def download(client: httpx.Client, url: str, target: Path, maximum: int) -> str:
    official_url(url)
    digest = hashlib.sha256()
    size = 0
    started = monotonic()
    with client.stream("GET", url, follow_redirects=False) as response:
        if response.is_redirect:
            raise SourceError("upstream-redirect")
        response.raise_for_status()
        with target.open("wb") as output:
            for chunk in response.iter_bytes(65536):
                if monotonic() - started > 300:
                    raise SourceError("download-deadline-exceeded")
                size += len(chunk)
                if size > maximum:
                    raise SourceError("download-too-large")
                output.write(chunk)
                digest.update(chunk)
    if size == 0:
        raise SourceError("empty-download")
    return digest.hexdigest()


def validate_common(path: Path) -> None:
    root: Element = fromstring(path.read_bytes(), forbid_dtd=True)
    if (
        root.tag != "{http://www.w3.org/2001/XMLSchema}schema"
        or root.get("version") != "2.0.0"
        or root.get("targetNamespace") != COMMON_NAMESPACE
    ):
        raise SourceError("common-types-version-changed")


class Importer:
    def __init__(self, catalog: Catalog, client: httpx.Client | None = None) -> None:
        self.catalog = catalog
        self.client = client

    def run(self) -> bool:
        with exclusive(self.catalog.path.parent / "import.lock"):
            if self.client:
                return self._run(self.client)
            with httpx.Client(timeout=httpx.Timeout(120, connect=15), trust_env=False) as client:
                return self._run(client)

    def _run(self, client: httpx.Client) -> bool:
        successful = True
        for contract in CONTRACTS:
            attempt = Attempt(attempted_at=now())
            self.catalog.attempt(contract.dataset, attempt)
            try:
                with tempfile.TemporaryDirectory(
                    prefix="rpl-", dir=self.catalog.path.parent
                ) as tmp:
                    self._one(client, contract, Path(tmp), attempt)
                LOGGER.info("import_succeeded", extra={"dataset": contract.dataset.value})
            except Exception as exc:
                code = exc.code if isinstance(exc, SourceError) else type(exc).__name__
                self.catalog.attempt(
                    contract.dataset,
                    attempt.model_copy(
                        update={
                            "finished_at": now(),
                            "error_code": code,
                        }
                    ),
                )
                LOGGER.error(
                    "import_failed", extra={"dataset": contract.dataset.value, "code": code}
                )
                successful = False
        return successful

    def _one(self, client: httpx.Client, contract: Contract, tmp: Path, attempt: Attempt) -> None:
        metadata_path = tmp / "metadata.json"
        download(client, contract.metadata_url, metadata_path, 2 * 1024 * 1024)
        resource = Metadata.model_validate_json(metadata_path.read_bytes()).data
        if (
            resource.id != contract.resource_id
            or resource.type != "resource"
            or resource.relationships.dataset.data.id != contract.parent_id
            or resource.relationships.dataset.data.type != "dataset"
            or resource.attributes.format.lower() != "xml"
            or resource.attributes.link != contract.url
        ):
            raise SourceError("metadata-contract-changed")
        common = tmp / "commonTypes.xsd"
        common_hash = download(client, COMMON_TYPES, common, 1024 * 1024)
        validate_common(common)
        xml_path = tmp / "source.xml"
        xml_hash = download(client, contract.url, xml_path, 150 * 1024 * 1024)
        stream = records(xml_path, contract)
        try:
            as_of = next(stream)
            if not isinstance(as_of, date):
                raise SourceError("missing-as-of")
            snapshot = Snapshot(
                dataset=contract.dataset,
                as_of=as_of,
                imported_at=now(),
                checked_at=now(),
                product_count=0,
                package_count=0,
                xml_sha256=xml_hash,
                common_types_sha256=common_hash,
                source_url=contract.url,
                common_types_url=COMMON_TYPES,
                schema_version=contract.version,
                metadata_url=contract.metadata_url,
                metadata_date=resource.attributes.data_date,
            )
            self.catalog.publish(self._products(stream), snapshot, contract.minimum, attempt)
        finally:
            stream.close()

    @staticmethod
    def _products(stream: Iterator[date | Product]) -> Iterator[Product]:
        for product in stream:
            if not isinstance(product, Product):
                raise SourceError("unexpected-date-in-stream")
            yield product


def import_devices(catalog: Catalog, path: Path) -> Snapshot:
    if path.stat().st_size > 30 * 1024 * 1024:
        raise SourceError("device-feed-too-large")
    raw = path.read_bytes()
    feed = DeviceFeed.model_validate_json(raw)
    if not public_link(feed.source_url) or any(not public_link(d.source_url) for d in feed.devices):
        raise SourceError("device-source-must-be-https")
    products = (
        Product(
            id=device.id,
            dataset=Dataset.DEVICES,
            product_type="medical-device",
            name=device.name,
            kind="ludzki",
            authorization_holder=device.manufacturer,
            pharmaceutical_form=device.form,
            packages=[Package(id=str(index), gtin=gtin) for index, gtin in enumerate(device.gtins)],
            package_count=len(device.gtins),
            active_package_count=len(device.gtins),
            source_url=device.source_url,
            source=SourceNode(
                name="reviewedDevice",
                attributes={
                    "sourceUrl": device.source_url,
                    "country": device.country,
                    "polandEvidence": device.poland_evidence,
                    "reviewedBy": feed.reviewed_by,
                    "license": feed.license,
                    "original": json.dumps(device.model_dump(mode="json"), ensure_ascii=False),
                },
            ),
        )
        for device in feed.devices
    )
    with exclusive(catalog.path.parent / "import.lock"):
        return catalog.publish(
            products,
            Snapshot(
                dataset=Dataset.DEVICES,
                as_of=feed.as_of,
                imported_at=now(),
                checked_at=now(),
                product_count=0,
                package_count=0,
                xml_sha256=hashlib.sha256(raw).hexdigest(),
                source_url=feed.source_url,
                schema_version="reviewed-devices-1",
                discovery="reviewed-local-feed",
                license=feed.license,
                reviewed_by=feed.reviewed_by,
            ),
            1,
            Attempt(attempted_at=now()),
        )
