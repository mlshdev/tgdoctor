import json

import httpx
import pytest

from tests.conftest import LOCATIONS_PAYLOAD, OFFERS_FRAGMENT, PRODUCTS_FRAGMENT
from tgdoctor.ktomalek import (
    KtomalekClient,
    KtomalekError,
    parse_locations,
    parse_pharmacies,
    parse_products,
)


def test_parse_products() -> None:
    products = parse_products(PRODUCTS_FRAGMENT)
    assert len(products) == 2
    assert products[0].bloz == "5855801"
    assert products[0].group_id == "100005855801"
    assert products[0].search_input == "apap"
    assert products[0].report_id == "715439626"
    assert products[0].name == "Apap"
    assert products[0].dose_line == "500 mg | 6 tabl. | Paracetamolum"
    assert products[0].info == "lek dostępny bez recepty"
    assert products[1].info == ""
    assert parse_products("<div></div>") == []


def test_parse_locations() -> None:
    locations = parse_locations(LOCATIONS_PAYLOAD)
    assert len(locations) == 1
    assert locations[0].name == "Warszawa, mazowieckie"
    assert locations[0].city == "Warszawa"
    assert locations[0].voivodeship == "mazowieckie"
    assert locations[0].latitude == pytest.approx(52.2319581)
    assert locations[0].longitude == pytest.approx(21.0067249)
    assert parse_locations("[]") == []
    with pytest.raises(KtomalekError) as rejected:
        parse_locations("nie json")
    assert rejected.value.code == "protocol"
    with pytest.raises(KtomalekError) as non_list:
        parse_locations('{"name": "x"}')
    assert non_list.value.code == "protocol"
    assert parse_locations('[{"city": "Brak"}]') == []


def test_parse_pharmacies() -> None:
    pharmacies = parse_pharmacies(OFFERS_FRAGMENT)
    assert len(pharmacies) == 2
    first = pharmacies[0]
    assert first.id == 877828
    assert first.name == "APTEKA WILANOWSKA"
    assert first.distance == "8,5 km"
    assert first.address == "Warszawa,Kosiarzy 37"
    assert first.phone == "22 651 69 39"
    assert first.open_status == "Zamknięta, zapraszamy jutro 08:00 - 20:00"
    assert len(first.medications) == 1
    assert first.medications[0].name == "Apap"
    assert first.medications[0].status == "ostatnie sztuki"
    second = pharmacies[1]
    assert second.id == 877829
    assert second.distance == ""
    assert second.address == 'Warszawa,Gen.Antoniego Chruściela "Montera" 103'
    assert second.phone == ""
    assert second.open_status == "Otwarta do 22:00"
    assert second.medications[0].status == ""
    assert parse_pharmacies("<div></div>") == []


def transport(
    paths: list[str],
    cookies: list[str | None],
    bodies: list[dict[str, object] | None],
    failures: int = 0,
) -> httpx.MockTransport:
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        if len(seen) <= failures:
            raise httpx.ConnectError("connection refused")
        paths.append(request.url.path)
        cookies.append(request.headers.get("cookie"))
        bodies.append(json.loads(request.content) if request.content else None)
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"set-cookie": "JSESSIONID=abc123.t_leon20_1; path=/"},
            )
        if request.url.path == "/leon-www/produkty/szukajAjax":
            return httpx.Response(200, text=PRODUCTS_FRAGMENT)
        if request.url.path == "/leon-www/localization":
            return httpx.Response(200, text=LOCATIONS_PAYLOAD)
        if request.url.path == "/leon-www/serwispacjenta/cart/add":
            return httpx.Response(200, json={"type": "SUCCESS", "message": "Produkt dodany"})
        if request.url.path == "/leon-www/oferty/KLEK/szukaj/ajax":
            return httpx.Response(200, text=OFFERS_FRAGMENT)
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_client_products_flow() -> None:
    paths: list[str] = []
    cookies: list[str | None] = []
    bodies: list[dict[str, object] | None] = []
    client = KtomalekClient(transport=transport(paths, cookies, bodies))
    products = await client.products("apap")
    assert [p.name for p in products] == ["Apap", "Apap Max"]
    assert paths == ["/", "/leon-www/produkty/szukajAjax"]
    assert cookies[1] is not None and "JSESSIONID=abc123.t_leon20_1" in cookies[1]


@pytest.mark.asyncio
async def test_client_locations_flow() -> None:
    paths: list[str] = []
    cookies: list[str | None] = []
    bodies: list[dict[str, object] | None] = []
    client = KtomalekClient(transport=transport(paths, cookies, bodies))
    locations = await client.locations("Warszawa")
    assert locations[0].city == "Warszawa"
    assert paths == ["/", "/leon-www/localization"]
    assert cookies[1] is not None and "JSESSIONID" in cookies[1]


@pytest.mark.asyncio
async def test_client_offers_flow() -> None:
    paths: list[str] = []
    cookies: list[str | None] = []
    bodies: list[dict[str, object] | None] = []
    client = KtomalekClient(transport=transport(paths, cookies, bodies))
    product = (await client.products("apap"))[0]
    locations = await client.locations("Warszawa")
    paths.clear()
    cookies.clear()
    bodies.clear()
    pharmacies = await client.offers(product, locations[0])
    assert [p.id for p in pharmacies] == [877828, 877829]
    assert paths == [
        "/",
        "/leon-www/produkty/szukajAjax",
        "/leon-www/serwispacjenta/cart/add",
        "/leon-www/localization",
        "/leon-www/oferty/KLEK/szukaj/ajax",
    ]
    cart = bodies[2]
    assert cart is not None
    assert cart["bloz"] == "5855801"
    assert cart["groupId"] == "100005855801"
    assert cart["searchInput"] == "apap"
    assert cart["reportId"] == "715439626"
    assert cart["source"] == "WPR"
    assert cart["pharmacyId"] is None
    assert all(cookie and "JSESSIONID" in cookie for cookie in cookies[1:])


@pytest.mark.asyncio
async def test_client_retry_recovers() -> None:
    paths: list[str] = []
    cookies: list[str | None] = []
    bodies: list[dict[str, object] | None] = []
    client = KtomalekClient(transport=transport(paths, cookies, bodies, failures=1))
    products = await client.products("apap")
    assert products[0].name == "Apap"
    assert paths == ["/", "/leon-www/produkty/szukajAjax"]


@pytest.mark.asyncio
async def test_client_retry_exhausted() -> None:
    paths: list[str] = []
    cookies: list[str | None] = []
    bodies: list[dict[str, object] | None] = []
    client = KtomalekClient(transport=transport(paths, cookies, bodies, failures=99))
    with pytest.raises(KtomalekError) as rejected:
        await client.products("apap")
    assert rejected.value.code == "transport"


@pytest.mark.asyncio
async def test_client_cart_rejection() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(
                200, headers={"set-cookie": "JSESSIONID=abc123.t_leon20_1; path=/"}
            )
        if request.url.path == "/leon-www/produkty/szukajAjax":
            return httpx.Response(200, text=PRODUCTS_FRAGMENT)
        if request.url.path == "/leon-www/serwispacjenta/cart/add":
            return httpx.Response(200, json={"type": "ERROR"})
        return httpx.Response(200, text="[]")

    client = KtomalekClient(transport=httpx.MockTransport(handler))
    product = parse_products(PRODUCTS_FRAGMENT)[0]
    location = parse_locations(LOCATIONS_PAYLOAD)[0]
    with pytest.raises(KtomalekError) as rejected:
        await client.offers(product, location)
    assert rejected.value.code == "cart"


@pytest.mark.asyncio
async def test_client_product_vanished_between_steps() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(
                200, headers={"set-cookie": "JSESSIONID=abc123.t_leon20_1; path=/"}
            )
        if request.url.path == "/leon-www/produkty/szukajAjax":
            other = (
                '<div class="cart-item" data-bl="999" data-gr="1" data-si="x" data-ri="2">'
                '<div class="cart-item-name"><a>Inny</a></div></div>'
            )
            return httpx.Response(200, text=other)
        return httpx.Response(200, text="[]")

    client = KtomalekClient(transport=httpx.MockTransport(handler))
    product = parse_products(PRODUCTS_FRAGMENT)[0]
    location = parse_locations(LOCATIONS_PAYLOAD)[0]
    with pytest.raises(KtomalekError) as rejected:
        await client.offers(product, location)
    assert rejected.value.code == "produkt-zmienil-sie"


@pytest.mark.asyncio
async def test_client_missing_session_cookie() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    client = KtomalekClient(transport=httpx.MockTransport(handler))
    with pytest.raises(KtomalekError) as rejected:
        await client.products("apap")
    assert rejected.value.code == "protocol"
