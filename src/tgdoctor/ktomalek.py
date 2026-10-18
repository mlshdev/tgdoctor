import asyncio
import html
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import time
from typing import TypeVar

import httpx

BASE_URL = "https://ktomalek.pl"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
)
ACCEPT_LANGUAGE = "pl-PL,pl;q=0.9,en;q=0.8"
CONNECT_TIMEOUT = 8.0
READ_TIMEOUT = 15.0
FLOW_ATTEMPTS = 2
MAX_CONCURRENT_FLOWS = 2

T = TypeVar("T")


class KtomalekError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


# Przestarzała sesja lub uszkodzona odpowiedź mogą być przejściowe; ponawiamy
# cały przepływ raz. Wyniki „nie znaleziono” są deterministyczne — nie ponawiamy.
RETRYABLE_CODES = frozenset({"transport", "status", "protocol", "cart"})


@dataclass(frozen=True)
class KtomalekProduct:
    bloz: str
    group_id: str
    search_input: str
    report_id: str
    name: str
    dose_line: str
    info: str


@dataclass(frozen=True)
class KtomalekLocation:
    name: str
    city: str
    voivodeship: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class KtomalekMedication:
    name: str
    info: str
    status: str


@dataclass(frozen=True)
class KtomalekPharmacy:
    id: int
    name: str
    distance: str
    address: str
    phone: str
    open_status: str
    medications: tuple[KtomalekMedication, ...]


def _text(value: str | None) -> str:
    return html.unescape(value or "").strip()


def parse_products(fragment: str) -> list[KtomalekProduct]:
    products: list[KtomalekProduct] = []
    for block in fragment.split('<div class="cart-item"')[1:]:
        bloz = re.search(r'data-bl="(\d+)"', block)
        group = re.search(r'data-gr="(\d+)"', block)
        report = re.search(r'data-ri="(\d+)"', block)
        search = re.search(r'data-si="([^"]*)"', block)
        name = re.search(r'class="cart-item-name">\s*<a[^>]*>([^<]+)</a>', block)
        if not (bloz and group and report and search and name):
            continue
        dose = re.search(r'class="cart-item-dawka">([^<]+)</', block)
        info = re.search(r'class="cart-item-info">([^<]+)</', block)
        products.append(
            KtomalekProduct(
                bloz=bloz.group(1),
                group_id=group.group(1),
                search_input=_text(search.group(1)),
                report_id=report.group(1),
                name=_text(name.group(1)),
                dose_line=_text(dose.group(1)) if dose else "",
                info=_text(info.group(1)) if info else "",
            )
        )
    return products


def parse_locations(payload: str) -> list[KtomalekLocation]:
    try:
        data: object = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise KtomalekError("protocol") from exc
    if not isinstance(data, list):
        raise KtomalekError("protocol")
    locations: list[KtomalekLocation] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        try:
            locations.append(
                KtomalekLocation(
                    name=_text(str(entry.get("name", ""))),
                    city=_text(str(entry.get("city", ""))),
                    voivodeship=_text(str(entry.get("voivodeship", ""))),
                    latitude=float(entry["latitude"]),
                    longitude=float(entry["longitude"]),
                )
            )
        except KeyError, TypeError, ValueError:
            continue
    return locations


def _medication(chunk: str) -> KtomalekMedication | None:
    name = re.search(r'class="la-lek-nazwa">\s*([^<]+)', chunk)
    if not name:
        return None
    info = re.search(r'class="la-lek-info">\s*([^<]+)', chunk)
    status = re.search(r'class="la-lek-ilosc">(.*?)</div>', chunk, re.DOTALL)
    status_text = ""
    if status:
        status_text = re.sub(
            r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", status.group(1)))
        ).strip()
    return KtomalekMedication(
        name=_text(name.group(1)),
        info=_text(info.group(1)) if info else "",
        status=status_text,
    )


def parse_pharmacies(fragment: str) -> list[KtomalekPharmacy]:
    pharmacies: list[KtomalekPharmacy] = []
    for block in fragment.split('<div class="la-apteka">')[1:]:
        ident = (
            re.search(r"offers\.ulubionaApteka\(\s*(\d+)", block)
            or re.search(r'data-pharmacy="apteka(\d+)"', block)
            or re.search(r"pharmacyOfferAddressOnclick\(\s*(\d+)", block)
        )
        if not ident:
            continue
        name = re.search(r'id="apteka-aria".*?<span[^>]*>\s*(.*?)\s*</span>', block, re.DOTALL)
        distance = re.search(r'class="c:orange[^"]*"[^>]*>\s*([^<]+?)\s*</b>', block)
        address = re.search(
            r"pharmacyOfferAddressOnclick\(\s*\d+\s*,\s*this\s*\)[^>]*>([^<]+)</a>", block
        )
        phone = re.search(r'href="tel:[^"]*"[^>]*>([^<]+)</a>', block)
        open_status = re.search(r"<span>\s*((?:Otwarta|Zamknięta)[^<]*)</span>", block)
        medications = tuple(
            medication
            for medication in (
                _medication(chunk) for chunk in block.split('<div class="la-lek">')[1:]
            )
            if medication is not None
        )
        pharmacies.append(
            KtomalekPharmacy(
                id=int(ident.group(1)),
                name=_text(name.group(1)) if name else "",
                distance=_text(distance.group(1)) if distance else "",
                address=_text(address.group(1)) if address else "",
                phone=_text(phone.group(1)) if phone else "",
                open_status=_text(open_status.group(1)) if open_status else "",
                medications=medications,
            )
        )
    return pharmacies


class KtomalekClient:
    def __init__(
        self, base_url: str = BASE_URL, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self.timeout = httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_FLOWS)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": ACCEPT_LANGUAGE,
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=self.timeout,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        )

    @staticmethod
    def _timestamp() -> str:
        return str(int(time() * 1000))

    @staticmethod
    async def _mint(client: httpx.AsyncClient) -> None:
        response = await client.get("/")
        if response.status_code != 200:
            raise KtomalekError("status")
        if "JSESSIONID" not in client.cookies:
            raise KtomalekError("protocol")

    @staticmethod
    def _fragment(response: httpx.Response) -> str:
        if response.status_code != 200:
            raise KtomalekError("status")
        return response.text

    async def _run(self, flow: Callable[[], Awaitable[T]]) -> T:
        async with self.semaphore:
            for attempt in range(FLOW_ATTEMPTS):
                try:
                    return await flow()
                except KtomalekError as exc:
                    if exc.code not in RETRYABLE_CODES or attempt == FLOW_ATTEMPTS - 1:
                        raise
                except httpx.HTTPError:
                    if attempt == FLOW_ATTEMPTS - 1:
                        raise KtomalekError("transport") from None
            raise KtomalekError("transport")

    async def products(self, query: str) -> list[KtomalekProduct]:
        async def flow() -> list[KtomalekProduct]:
            async with self._client() as client:
                await self._mint(client)
                response = await client.get(
                    "/leon-www/produkty/szukajAjax",
                    params={"searchInput": query},
                    headers={"Referer": f"{self.base_url}/"},
                )
                return parse_products(self._fragment(response))

        return await self._run(flow)

    async def locations(self, city: str) -> list[KtomalekLocation]:
        async def flow() -> list[KtomalekLocation]:
            async with self._client() as client:
                await self._mint(client)
                response = await client.get(
                    "/leon-www/localization",
                    params={"timestamp": self._timestamp(), "address": city},
                    headers={"Referer": f"{self.base_url}/"},
                )
                return parse_locations(self._fragment(response))

        return await self._run(flow)

    async def offers(
        self, product: KtomalekProduct, location: KtomalekLocation
    ) -> list[KtomalekPharmacy]:
        async def flow() -> list[KtomalekPharmacy]:
            async with self._client() as client:
                await self._mint(client)
                response = await client.get(
                    "/leon-www/produkty/szukajAjax",
                    params={"searchInput": product.search_input},
                    headers={"Referer": f"{self.base_url}/"},
                )
                fresh = next(
                    (
                        item
                        for item in parse_products(self._fragment(response))
                        if item.bloz == product.bloz
                    ),
                    None,
                )
                if fresh is None:
                    raise KtomalekError("produkt-zmienil-sie")
                response = await client.post(
                    "/leon-www/serwispacjenta/cart/add",
                    json={
                        "bloz": fresh.bloz,
                        "groupId": fresh.group_id,
                        "searchInput": fresh.search_input,
                        "reportId": fresh.report_id,
                        "source": "WPR",
                        "pharmacyId": None,
                    },
                    headers={"Origin": self.base_url, "Referer": f"{self.base_url}/"},
                )
                if response.status_code != 200:
                    raise KtomalekError("status")
                try:
                    payload: object = response.json()
                except ValueError:
                    raise KtomalekError("protocol") from None
                if not isinstance(payload, dict) or payload.get("type") != "SUCCESS":
                    raise KtomalekError("cart")
                # Pinujemy współrzędnymi z wybranego geokodowania (odwrotne
                # geokodowanie, jak cookie cookie_wspolrzedne_gps); etykieta
                # „name” nie jest odwracalna przez parametr address.
                response = await client.get(
                    "/leon-www/localization",
                    params={
                        "timestamp": self._timestamp(),
                        "latitude": str(location.latitude),
                        "longitude": str(location.longitude),
                    },
                    headers={"Referer": f"{self.base_url}/"},
                )
                if not parse_locations(self._fragment(response)):
                    raise KtomalekError("miasto-nieznalezione")
                response = await client.get(
                    "/leon-www/oferty/KLEK/szukaj/ajax",
                    params={
                        "timestamp": self._timestamp(),
                        "czyTylkoWSieciAptek": "false",
                        "osozdemo": "true",
                        "opened": "false",
                        "czyTylkoOfertyAptek": "true",
                    },
                    headers={"Referer": f"{self.base_url}/oferty/szukaj"},
                )
                fragment = self._fragment(response)
                if not fragment.strip():
                    raise KtomalekError("protocol")
                return parse_pharmacies(fragment)

        return await self._run(flow)
