from datetime import UTC, date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel


def now() -> datetime:
    return datetime.now(UTC)


class Dataset(StrEnum):
    MEDICINAL = "medicinal-products"
    RAW = "raw-materials"
    DEVICES = "medical-devices"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, populate_by_name=True)


class SourceNode(Model):
    name: str
    attributes: dict[str, str]
    text: str = ""
    children: list[SourceNode] = Field(default_factory=list)


class Package(Model):
    id: str
    gtin: str = ""
    availability: str = ""
    deleted: bool = False
    eu_number: str = ""
    parallel_distributor: str = ""
    units: list[dict[str, str]] = Field(default_factory=list)
    approvals: list[SourceNode] = Field(default_factory=list)


class Substance(Model):
    name: str
    amount: str = ""
    unit: str = ""
    preparation_amount: str = ""
    preparation_unit: str = ""
    description: str = ""


class ProductSummary(Model):
    id: str
    dataset: Dataset
    product_type: str
    name: str
    common_name: str = ""
    previous_name: str = ""
    kind: str = ""
    strength: str = ""
    pharmaceutical_form: str = ""
    authorization_holder: str = ""
    permit_number: str = ""
    permit_validity: str = ""
    procedure: str = ""
    atc_codes: list[str] = Field(default_factory=list)
    routes: list[str] = Field(default_factory=list)
    substances: list[Substance] = Field(default_factory=list)
    package_count: int = 0
    active_package_count: int = 0
    source_url: str = ""


class Product(ProductSummary):
    packages: list[Package] = Field(default_factory=list)
    documents: dict[str, str] = Field(default_factory=dict)
    source: SourceNode

    def summary(self) -> ProductSummary:
        return ProductSummary.model_validate(
            self.model_dump(exclude={"packages", "documents", "source"})
        )


class Snapshot(Model):
    dataset: Dataset
    as_of: date
    imported_at: datetime
    checked_at: datetime
    product_count: int
    package_count: int
    xml_sha256: str
    common_types_sha256: str = ""
    source_url: str
    common_types_url: str = ""
    discovery: str = "dane.gov.pl"
    schema_version: str
    metadata_url: str = ""
    metadata_date: date | None = None
    license: str = ""
    reviewed_by: str = ""


class Attempt(Model):
    attempted_at: datetime
    finished_at: datetime | None = None
    success: bool = False
    error_code: str | None = None


class DatasetStatus(Model):
    snapshot: Snapshot | None
    stale: bool
    last_attempt: Attempt | None


class CatalogSnapshot(Model):
    revision: str
    product_count: int
    package_count: int
    datasets: dict[Dataset, DatasetStatus]


class Search(Model):
    q: str = Field(default="", max_length=160)
    dataset: Dataset | None = None
    name: str = Field(default="", max_length=160)
    substance: str = Field(default="", max_length=160)
    atc: str = Field(default="", max_length=7)
    gtin: str = Field(default="", max_length=14)
    form: str = Field(default="", max_length=160)
    strength: str = Field(default="", max_length=160)
    holder: str = Field(default="", max_length=160)
    route: str = Field(default="", max_length=160)
    availability: str = Field(default="", max_length=8)
    kind: str = Field(default="ludzki", max_length=40)
    active_only: bool = True
    limit: int = Field(default=8, ge=1, le=50)
    offset: int = Field(default=0, ge=0, le=10000)

    @field_validator("gtin")
    @classmethod
    def validate_gtin(cls, value: str) -> str:
        if value and (
            not value.isascii() or not value.isdigit() or len(value) not in (8, 12, 13, 14)
        ):
            raise ValueError("GTIN musi zawierać 8, 12, 13 lub 14 cyfr")
        return value

    @field_validator("atc")
    @classmethod
    def validate_atc(cls, value: str) -> str:
        value = value.upper()
        if value and (not value.isascii() or not value.isalnum()):
            raise ValueError("Nieprawidłowy kod ATC")
        return value


class Results(Model):
    items: list[ProductSummary]
    total: int
    limit: int
    offset: int
    snapshot: CatalogSnapshot


class Detail(Model):
    item: Product
    snapshot: CatalogSnapshot


class Device(Model):
    id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=255)
    manufacturer: str = Field(min_length=1, max_length=255)
    form: str = Field(default="", max_length=255)
    gtins: list[str] = Field(default_factory=list, max_length=100)
    source_url: str
    poland_evidence: str = Field(min_length=10, max_length=1000)
    country: str = Field(pattern="^PL$")

    @field_validator("gtins")
    @classmethod
    def validate_gtins(cls, values: list[str]) -> list[str]:
        for value in values:
            if not Search(gtin=value).gtin:
                raise ValueError("Pusty GTIN")
        return values


class DeviceFeed(Model):
    as_of: date
    source_url: str
    license: str = Field(min_length=5, max_length=1000)
    reviewed_by: str = Field(min_length=3, max_length=255)
    devices: list[Device] = Field(min_length=1, max_length=100000)
