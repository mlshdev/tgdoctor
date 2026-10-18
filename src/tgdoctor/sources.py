from dataclasses import dataclass
from urllib.parse import urlsplit

from tgdoctor.models import Dataset

COMMON_TYPES = "https://rejestry.ezdrowie.gov.pl/api/rpl/xsd/2.0.0/commonTypes.xsd"
COMMON_NAMESPACE = "http://rejestry.ezdrowie.gov.pl/api/rpl/xsd/2.0.0/commonTypes"


@dataclass(frozen=True)
class Contract:
    dataset: Dataset
    resource_id: str
    parent_id: str
    url: str
    version: str
    minimum: int

    @property
    def metadata_url(self) -> str:
        return f"https://api.dane.gov.pl/1.4/resources/{self.resource_id}"

    @property
    def namespace(self) -> str:
        return f"http://rejestry.ezdrowie.gov.pl/rpl/eksport-danych-v{self.version}"


CONTRACTS: tuple[Contract, ...] = (
    Contract(
        Dataset.MEDICINAL,
        "65522",
        "397",
        "https://rejestry.ezdrowie.gov.pl/api/rpl/medicinal-products/public-pl-report/6.0.0/overall.xml",
        "6.0.0",
        10000,
    ),
    Contract(
        Dataset.RAW,
        "52341",
        "2001",
        "https://rejestry.ezdrowie.gov.pl/api/rpl/medicinal-products/pharmaceutical-raw-materials/get-xml",
        "5.0.0",
        100,
    ),
)


class SourceError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def official_url(url: str) -> None:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.netloc not in ("rejestry.ezdrowie.gov.pl", "api.dane.gov.pl")
        or parts.fragment
        or parts.username is not None
    ):
        raise SourceError("untrusted-origin")


def public_link(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return bool(
            parts.scheme == "https"
            and bool(parts.hostname)
            and parts.username is None
            and parts.password is None
            and parts.port in (None, 443)
        )
    except ValueError:
        return False
