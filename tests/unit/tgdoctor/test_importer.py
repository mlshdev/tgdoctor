import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from xml.etree.ElementTree import ParseError

import httpx
import pytest
from defusedxml.common import DefusedXmlException
from pydantic import ValidationError

from tests.conftest import product, publish
from tests.conftest import snapshot as make_snapshot
from tgdoctor.catalog import Catalog
from tgdoctor.importer import Importer, download, exclusive, import_devices
from tgdoctor.models import Dataset, Product, Search, now
from tgdoctor.sources import COMMON_NAMESPACE, COMMON_TYPES, CONTRACTS, SourceError, official_url
from tgdoctor.xmlfeed import records


def test_parse_xml(xml_factory: Callable[..., Path]) -> None:
    items = list(records(xml_factory(), CONTRACTS[0]))
    assert items[0] == now().date()
    item = items[1]
    assert isinstance(item, Product)
    assert item.substances[0].amount == "1,54"
    assert item.packages[0].gtin == "05909991023652"
    assert item.packages[1].deleted
    assert item.source.attributes["moc"] == "0,5 mg"
    assert item.atc_codes == ["N05BA12"]


def test_reject_wrong_namespace(xml_factory: Callable[..., Path]) -> None:
    with pytest.raises(SourceError, match="schema-version"):
        list(records(xml_factory(namespace=CONTRACTS[1].namespace), CONTRACTS[0]))


def test_malformed_tail_rolls_back(
    populated: Catalog,
    xml_factory: Callable[..., Path],
) -> None:
    from tgdoctor.models import Attempt

    revision = populated.snapshot().revision
    stream = records(xml_factory(suffix="<broken>"), CONTRACTS[0])
    assert next(stream) == now().date()
    with pytest.raises(ParseError):
        populated.publish(
            Importer._products(stream), make_snapshot(), 1, Attempt(attempted_at=now())
        )
    assert populated.snapshot().revision == revision
    assert populated.search(Search()).total == 1


def test_forbid_xxe(tmp_path: Path) -> None:
    path = tmp_path / "evil.xml"
    path.write_text("""<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>""")
    with pytest.raises(DefusedXmlException):
        list(records(path, CONTRACTS[0]))


@pytest.mark.parametrize(
    "url",
    [
        "http://api.dane.gov.pl/x",
        "https://api.dane.gov.pl.evil.test/",
        "https://user@api.dane.gov.pl/",
        "https://api.dane.gov.pl:444/",
        "https://127.0.0.1/",
        "https://rejestry.ezdrowie.gov.pl@evil.test/",
    ],
)
def test_origin_allowlist(url: str) -> None:
    with pytest.raises(SourceError):
        official_url(url)


def test_redirect_and_size_rejected(tmp_path: Path) -> None:
    for response, code, maximum in (
        (httpx.Response(302, headers={"Location": "https://127.0.0.1/"}), "redirect", 100),
        (httpx.Response(200, content=b"abc"), "too-large", 2),
        (httpx.Response(200, content=b""), "empty", 10),
    ):

        def respond(request: httpx.Request, result: httpx.Response = response) -> httpx.Response:
            return result

        with (
            httpx.Client(transport=httpx.MockTransport(respond)) as client,
            pytest.raises(SourceError, match=code),
        ):
            download(client, COMMON_TYPES, tmp_path / "x", maximum)


def test_import_failure_independent_and_keeps_previous(
    populated: Catalog,
    xml_factory: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tgdoctor import importer

    contracts = tuple(replace(c, minimum=1) for c in CONTRACTS)
    monkeypatch.setattr(importer, "CONTRACTS", contracts)
    raw = xml_factory(namespace=contracts[1].namespace).read_bytes()

    def respond(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for contract in contracts:
            if url == contract.metadata_url:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "id": contract.resource_id,
                            "type": "resource",
                            "attributes": {
                                "link": contract.url,
                                "format": "xml",
                                "data_date": str(now().date()),
                            },
                            "relationships": {
                                "dataset": {"data": {"id": contract.parent_id, "type": "dataset"}}
                            },
                        }
                    },
                )
        if url == COMMON_TYPES:
            return httpx.Response(
                200,
                text='<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
                f'targetNamespace="{COMMON_NAMESPACE}" version="2.0.0"/>',
            )
        if url == contracts[0].url:
            return httpx.Response(503)
        return httpx.Response(200, content=raw)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert not Importer(populated, client).run()
    state = populated.snapshot()
    assert state.datasets[Dataset.MEDICINAL].snapshot is not None
    assert state.datasets[Dataset.RAW].snapshot is not None
    attempt = state.datasets[Dataset.MEDICINAL].last_attempt
    assert attempt is not None and not attempt.success
    assert populated.search(Search()).total == 2


def test_lock(tmp_path: Path) -> None:
    with (
        exclusive(tmp_path / "writer.lock"),
        pytest.raises(SourceError, match="already-running"),
        exclusive(tmp_path / "writer.lock"),
    ):
        pass


def test_devices_separate_reviewed_import(catalog: Catalog, tmp_path: Path) -> None:
    path = tmp_path / "devices.json"
    manifest = {
        "asOf": str(now().date()),
        "sourceUrl": "https://example.org/catalog",
        "license": "Synthetic permission for testing only",
        "reviewedBy": "Test reviewer",
        "devices": [
            {
                "id": "001",
                "name": "Syntetyczny test diagnostyczny",
                "manufacturer": "Test",
                "country": "PL",
                "polandEvidence": "Synthetic Polish market evidence, testing only",
                "sourceUrl": "https://example.org/device",
                "gtins": [],
            }
        ],
    }
    path.write_text(json.dumps(manifest))
    publish(catalog, [product()])
    import_devices(catalog, path)
    found = catalog.search(Search(dataset=Dataset.DEVICES))
    assert found.total == 1
    assert found.items[0].id == "001"
    assert found.items[0].product_type == "medical-device"
    assert catalog.search(Search()).total == 2
    manifest["devices"][0]["country"] = "DE"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValidationError):
        import_devices(catalog, path)
    assert catalog.search(Search(dataset=Dataset.DEVICES)).total == 1
