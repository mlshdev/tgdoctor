from tests.conftest import product
from tgdoctor.catalog import Catalog
from tgdoctor.models import Package
from tgdoctor.presentation import product_text


def test_detail_shows_active_packages_before_deleted(populated: Catalog) -> None:
    current = product()
    packages = [
        Package(id=str(index), gtin=f"059099900000{index:02d}", deleted=True) for index in range(7)
    ] + [current.packages[0]]
    current = current.model_copy(
        update={"packages": packages, "package_count": len(packages), "active_package_count": 1}
    )

    first = product_text(current, populated.snapshot())
    assert "05909991023652" in first
    assert "SKASOWANE" in first
    assert first.index("05909991023652") < first.index("SKASOWANE")
