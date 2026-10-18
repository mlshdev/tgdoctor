from collections.abc import Generator
from datetime import date
from pathlib import Path
from typing import cast
from xml.etree.ElementTree import Element

from defusedxml.ElementTree import iterparse

from tgdoctor.models import Dataset, Package, Product, SourceNode, Substance
from tgdoctor.sources import Contract, SourceError, public_link


def node(element: Element, depth: int = 0) -> SourceNode:
    if depth > 30:
        raise SourceError("xml-too-deep")
    return SourceNode(
        name=str(element.tag).rsplit("}", 1)[-1],
        attributes=dict(element.attrib),
        text=(element.text or "").strip(),
        children=[node(child, depth + 1) for child in element],
    )


def parse_product(element: Element, contract: Contract) -> Product:
    ns = {"r": contract.namespace}
    attrs = element.attrib
    if not attrs.get("id", "") or not attrs.get("nazwaProduktu", ""):
        raise SourceError("missing-product-identity")
    packages: list[Package] = []
    for pack in element.findall("r:opakowania/r:opakowanie", ns):
        a = pack.attrib
        if a.get("skasowane", "") not in ("TAK", "NIE", ""):
            raise SourceError("unknown-deleted-value")
        if not a.get("id", ""):
            raise SourceError("missing-package-identity")
        packages.append(
            Package(
                id=a["id"],
                gtin=a.get("kodGTIN", ""),
                availability=a.get("kategoriaDostepnosci", ""),
                deleted=a.get("skasowane") == "TAK",
                eu_number=a.get("numerEu", ""),
                parallel_distributor=a.get("dystrybutorRownolegly", ""),
                units=[dict(unit.attrib) for unit in pack.findall("r:jednostkiOpakowania/*", ns)],
                approvals=[node(approval) for approval in pack.findall("r:zgodyPrezesa/*", ns)],
            )
        )
    if len({p.id for p in packages}) != len(packages):
        raise SourceError("duplicate-package-id")
    substances = [
        Substance(
            name=sub.get("nazwaSubstancji", ""),
            amount=sub.get("iloscSubstancji", ""),
            unit=sub.get("jednostkaMiaryIlosciSubstancji", ""),
            preparation_amount=sub.get("iloscPreparatu", ""),
            preparation_unit=sub.get("jednostkaMiaryIlosciPreparatu", ""),
            description=sub.get("innyOpisIlosci", ""),
        )
        for sub in element.findall("r:substancjeCzynne/r:substancjaCzynna", ns)
    ]
    return Product(
        id=attrs["id"],
        dataset=contract.dataset,
        product_type=(
            "pharmaceutical-raw-material" if contract.dataset == Dataset.RAW else "medicine"
        ),
        name=attrs["nazwaProduktu"],
        common_name=attrs.get("nazwaPowszechnieStosowana", ""),
        previous_name=attrs.get("nazwaPoprzedniaProduktu", ""),
        kind=attrs.get("rodzajPreparatu", ""),
        strength=attrs.get("moc", ""),
        pharmaceutical_form=attrs.get("nazwaPostaciFarmaceutycznej", ""),
        authorization_holder=attrs.get("podmiotOdpowiedzialny", ""),
        permit_number=attrs.get("numerPozwolenia", ""),
        permit_validity=attrs.get("waznoscPozwolenia", ""),
        procedure=attrs.get("typProcedury", ""),
        atc_codes=[
            (atc.text or "").strip()
            for atc in element.findall("r:kodyATC/r:kodATC", ns)
            if atc.text is not None
        ],
        routes=[
            route.get("drogaPodaniaNazwa", "") for route in element.findall("r:drogiPodania/*", ns)
        ],
        substances=substances,
        packages=packages,
        package_count=len(packages),
        active_package_count=sum(not package.deleted for package in packages),
        documents={
            key: attrs[key]
            for key in ("ulotka", "charakterystyka")
            if public_link(attrs.get(key, ""))
        },
        source=node(element),
        source_url=contract.url,
    )


def records(path: Path, contract: Contract) -> Generator[date | Product]:
    if path.stat().st_size > 150 * 1024 * 1024:
        raise SourceError("xml-too-large")
    context = iterparse(path, events=("start", "end"), forbid_dtd=True, forbid_entities=True)
    root: Element | None = None
    depth = 0
    for event, raw_element in context:
        candidate: object = raw_element
        if not isinstance(candidate, Element):
            raise SourceError("invalid-xml-element")
        if not isinstance(candidate.tag, str):
            raise SourceError("invalid-xml-tag")
        element = cast("Element[str]", candidate)
        if event == "start":
            depth += 1
            if depth > 32:
                raise SourceError("xml-too-deep")
            if root is None:
                root = element
                if root.tag != f"{{{contract.namespace}}}produktyLecznicze":
                    raise SourceError("schema-version-changed")
                yield date.fromisoformat(root.attrib["stanNaDzien"])
            elif not str(element.tag).startswith(f"{{{contract.namespace}}}"):
                raise SourceError("foreign-xml-namespace")
        else:
            if depth == 2:
                if element.tag != f"{{{contract.namespace}}}produktLeczniczy":
                    raise SourceError("unexpected-root-child")
                yield parse_product(element, contract)
                if root is not None:
                    root.remove(element)
                element.clear()
            depth -= 1
    if root is None:
        raise SourceError("empty-xml")
