from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from tgdoctor.allowlist import Allowlist
from tgdoctor.catalog import Catalog
from tgdoctor.models import Attempt, Dataset, Package, Product, Snapshot, SourceNode, Substance, now

PRODUCTS_FRAGMENT = """
<div class="cart-item"
     data-bl="5855801"
     data-gr="100005855801"
     data-si="apap"
     data-ri="715439626">
    <div class="cart-item-name">
        <a href="#0" data-source-symbol="WPR">Apap</a>
    </div>
    <div class="cart-item-dawka">500 mg | 6 tabl. | Paracetamolum</div>
    <div class="cart-item-info">lek dostępny bez recepty</div>
</div>
<div class="cart-item"
     data-bl="5855802"
     data-gr="100005855802"
     data-si="apap"
     data-ri="715439627">
    <div class="cart-item-name">
        <a href="#0">Apap Max</a>
    </div>
    <div class="cart-item-dawka">500 mg | 10 tabl. | Paracetamolum</div>
</div>
"""

LOCATIONS_PAYLOAD = """[
    {
        "name": "Warszawa, mazowieckie",
        "city": "Warszawa",
        "street": null,
        "voivodeship": "mazowieckie",
        "county": null,
        "postalCode": null,
        "longitude": 21.0067249,
        "latitude": 52.2319581,
        "village": false
    }
]
"""

OFFERS_FRAGMENT = """
<div class="la-apteka">
  <header>
    <div class="flex flex-w flex-r a-i:c">
      <div class="flex-g">
        <p class="h3 m:0" id="apteka-aria">
          <b class="c:orange m-r:s">8,5 km</b>
          <span data-sens="1">APTEKA WILANOWSKA</span>
        </p>
      </div>
      <button type="button" class="switcher b-s:s" aria-pressed="false"
              onclick="offers.ulubionaApteka(877828, event)"
              data-pharmacy="apteka877828"></button>
    </div>
    <p class="p-icon"><i class="icon-16 icon-pin-thin"></i>
      <a href="#" onclick="pharmacyOfferAddressOnclick(877828, this)"
         data-sens="1">Warszawa,Kosiarzy 37</a>
    </p>
    <p class="p-icon"><i class="icon-16 icon-phone-thin"></i>
      <a href="tel:226516939" data-sens="1" style="display:none">22 651 69 39</a>
    </p>
    <p class="p-icon c:red">
      <button class="switcher chevron c:i a-s:e" type="button">
        <span>Zamknięta, zapraszamy jutro 08:00 - 20:00</span>
      </button>
    </p>
  </header>
  <div class="box:t">
    <div class="la-leki">
      <div class="la-lek">
        <div class="la-lek-nazwa">Apap</div>
        <div class="la-lek-info">500 mg | 6 tabl. (blist.)</div>
        <div class="la-lek-ilosc">
          <span class="piece-ending">ostatnie sztuki</span>
        </div>
      </div>
    </div>
  </div>
</div>
<div class="la-apteka">
  <header>
    <div class="flex-g">
      <p class="h3 m:0" id="apteka-aria">
        <span data-sens="1">APTEKA NARODOWA</span>
      </p>
    </div>
    <button type="button" class="switcher b-s:s" aria-pressed="false"
            onclick="offers.ulubionaApteka(877829, event)"
            data-pharmacy="apteka877829"></button>
    <p class="p-icon">
      <a href="#" onclick="pharmacyOfferAddressOnclick(877829, this)"
         data-sens="1">Warszawa,Gen.Antoniego Chruściela &quot;Montera&quot; 103</a>
    </p>
    <p class="p-icon c:red">
      <button class="switcher chevron c:i a-s:e" type="button">
        <span>Otwarta do 22:00</span>
      </button>
    </p>
  </header>
  <div class="box:t">
    <div class="la-leki">
      <div class="la-lek">
        <div class="la-lek-nazwa">Apap</div>
        <div class="la-lek-info">500 mg | 6 tabl. (blist.)</div>
        <div class="la-lek-ilosc"></div>
      </div>
    </div>
  </div>
</div>
"""


def product(dataset: Dataset = Dataset.MEDICINAL, identity: str = "001") -> Product:
    return Product(
        id=identity,
        dataset=dataset,
        product_type="medicine",
        name="Żółć Lek Test",
        common_name="Alprazolamum",
        kind="ludzki",
        strength="0,5 mg",
        pharmaceutical_form="Tabletki",
        authorization_holder="Test producent",
        atc_codes=["N05BA12"],
        routes=["doustna"],
        substances=[Substance(name="Alprazolamum", amount="1,54", unit="mg")],
        package_count=2,
        active_package_count=1,
        packages=[
            Package(id="01", gtin="05909991023652", availability="Rp"),
            Package(id="02", gtin="5909991023669", availability="Rpw", deleted=True),
        ],
        source=SourceNode(name="produktLeczniczy", attributes={"id": identity, "moc": "0,5 mg"}),
    )


def snapshot(dataset: Dataset = Dataset.MEDICINAL) -> Snapshot:
    return Snapshot(
        dataset=dataset,
        as_of=now().date(),
        imported_at=now(),
        checked_at=now(),
        product_count=0,
        package_count=0,
        xml_sha256="a" * 64,
        source_url="https://rejestry.ezdrowie.gov.pl/",
        schema_version="6.0.0",
    )


def publish(catalog: Catalog, items: list[Product], snap: Snapshot | None = None) -> Snapshot:
    return catalog.publish(
        iter(items), snap or snapshot(items[0].dataset), 1, Attempt(attempted_at=now())
    )


@pytest.fixture
def catalog(tmp_path: Path) -> Catalog:
    result = Catalog(tmp_path / "catalog.sqlite3")
    result.initialize()
    return result


@pytest.fixture
def allowlist(tmp_path: Path) -> Allowlist:
    result = Allowlist(tmp_path / "allowlist.sqlite3")
    result.initialize()
    return result


@pytest.fixture
def populated(catalog: Catalog) -> Catalog:
    publish(catalog, [product()])
    return catalog


@pytest.fixture
def xml_factory(tmp_path: Path) -> Callable[..., Path]:
    def make(
        namespace: str = "http://rejestry.ezdrowie.gov.pl/rpl/eksport-danych-v6.0.0",
        as_of: date | None = None,
        suffix: str = "",
    ) -> Path:
        path = tmp_path / "fixture.xml"
        path.write_text(
            f'''<?xml version="1.0" encoding="UTF-8"?>
<produktyLecznicze xmlns="{namespace}" stanNaDzien="{as_of or now().date()}">
 <produktLeczniczy id="001" nazwaProduktu="Żółć Lek Test" rodzajPreparatu="ludzki"
  nazwaPowszechnieStosowana="Alprazolamum" moc="0,5 mg"
  ulotka="https://rejestry.ezdrowie.gov.pl/api/rpl/medicinal-products/1/leaflet">
  <kodyATC><kodATC>N05BA12</kodATC></kodyATC>
  <substancjeCzynne><substancjaCzynna nazwaSubstancji="Alprazolamum"
    iloscSubstancji="1,54" jednostkaMiaryIlosciSubstancji="mg"/></substancjeCzynne>
  <opakowania>
   <opakowanie id="01" kodGTIN="05909991023652" kategoriaDostepnosci="Rp" skasowane="NIE"/>
   <opakowanie id="02" kodGTIN="5909991023669" kategoriaDostepnosci="Rpw" skasowane="TAK"/>
  </opakowania>
 </produktLeczniczy>
</produktyLecznicze>{suffix}''',
            encoding="utf-8",
        )
        return path

    return make
