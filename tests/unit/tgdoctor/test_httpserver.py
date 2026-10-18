from collections.abc import Generator

import httpx
import pytest

from tests.conftest import product, publish
from tgdoctor.catalog import Catalog
from tgdoctor.httpserver import start_server
from tgdoctor.models import Dataset, Detail, Results


@pytest.fixture
def api(populated: Catalog) -> Generator[httpx.Client]:
    server, thread = start_server("127.0.0.1", 0, populated)
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False
        ) as client:
            yield client
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_health(api: httpx.Client, populated: Catalog) -> None:
    assert api.get("/health/live").status_code == 200
    assert api.get("/health/ready").status_code == 503
    publish(populated, [product(Dataset.RAW)])
    assert api.get("/health/ready").status_code == 200
    assert api.get("/api/v1/status").status_code == 200


def test_search_and_detail(api: httpx.Client) -> None:
    response = api.get("/api/v1/medications", params={"substance": "alprazolam"})
    assert response.status_code == 200
    result = Results.model_validate_json(response.content)
    assert result.total == 1
    response = api.get("/api/v1/medications/001", params={"dataset": "medicinal-products"})
    assert response.status_code == 200
    detail = Detail.model_validate_json(response.content)
    assert detail.item.packages[0].id == "01"
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "query",
    [
        "?limit=0",
        "?offset=-1",
        "?limit=51",
        "?dataset=bad",
        "?gtin=12",
        "?q=a&q=b",
        "?unknown=1",
        "?activeOnly=bad",
        "?atc=%25",
    ],
)
def test_bad_queries(api: httpx.Client, query: str) -> None:
    assert api.get("/api/v1/medications" + query).status_code == 400


def test_missing_or_ambiguous_identity(api: httpx.Client) -> None:
    assert api.get("/api/v1/medications/001").status_code == 400
    assert api.get("/api/v1/medications/999?dataset=raw-materials").status_code == 404
    assert api.get("/absent").status_code == 404
    assert api.post("/api/v1/medications").status_code == 501
