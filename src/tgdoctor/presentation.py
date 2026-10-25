from tgdoctor.ktomalek import (
    KtomalekLocation,
    KtomalekMedication,
    KtomalekPharmacy,
    KtomalekProduct,
)
from tgdoctor.models import CatalogSnapshot, Dataset, Product, ProductSummary, Search

LABELS: dict[Dataset, str] = {
    Dataset.MEDICINAL: "Leki (RPL)",
    Dataset.RAW: "Surowce farmaceutyczne (RPL)",
    Dataset.DEVICES: "Wyroby medyczne (katalog zweryfikowany)",
}
NOTICE = "Wpis w rejestrze nie potwierdza dostępności w aptece. To nie jest porada medyczna."


def short(value: str, limit: int = 220) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def filters_text(query: Search) -> str:
    labels = {
        "q": "fraza",
        "name": "nazwa",
        "substance": "substancja",
        "atc": "ATC",
        "gtin": "GTIN",
        "form": "postać",
        "strength": "moc",
        "holder": "podmiot",
        "route": "droga",
        "availability": "kategoria",
        "kind": "rodzaj",
    }
    active = [
        f"{label}: {getattr(query, key)}" for key, label in labels.items() if getattr(query, key)
    ]
    active.append(LABELS[query.dataset] if query.dataset is not None else "wszystkie zbiory")
    active.append("opakowania nieskasowane" if query.active_only else "także skasowane opakowania")
    return " | ".join(active)


def freshness(snapshot: CatalogSnapshot, datasets: set[Dataset]) -> str:
    parts: list[str] = []
    for dataset in sorted(datasets):
        status = snapshot.datasets[dataset]
        stamp = status.snapshot.as_of.isoformat() if status.snapshot else "brak importu"
        warning = " — UWAGA: dane nieaktualne" if status.stale else ""
        parts.append(f"{LABELS[dataset]}: {stamp}{warning}")
    return "\n".join(parts)


def summary_line(product: ProductSummary) -> str:
    return short(f"{product.name} · {product.strength} · {product.pharmaceutical_form}", 180)


def product_text(product: Product, snapshot: CatalogSnapshot, package_offset: int = 0) -> str:
    lines = [product.name, LABELS[product.dataset]]
    for label, value in (
        ("Nazwa powszechna", product.common_name),
        ("Moc", product.strength),
        ("Postać", product.pharmaceutical_form),
        ("Rodzaj", product.kind),
        ("Podmiot / producent", product.authorization_holder),
        ("Pozwolenie", product.permit_number),
        ("Ważność pozwolenia (z rejestru)", product.permit_validity),
        ("ATC", ", ".join(product.atc_codes)),
        ("Drogi podania", ", ".join(product.routes)),
    ):
        if value:
            lines.append(f"{label}: {short(value)}")
    if product.substances:
        lines.append(
            "Substancje: "
            + short(
                "; ".join(f"{s.name} {s.amount} {s.unit}".strip() for s in product.substances), 600
            )
        )
    lines.append(
        f"Opakowania: {product.package_count}, nieskasowane: {product.active_package_count}"
    )
    packages = sorted(product.packages, key=lambda package: package.deleted)
    for package in packages[package_offset : package_offset + 5]:
        units = "; ".join(
            " ".join(
                filter(
                    None,
                    (
                        unit.get("liczbaOpakowan", ""),
                        unit.get("rodzajOpakowania", ""),
                        unit.get("pojemnosc", ""),
                        unit.get("jednostkaPojemnosci", ""),
                    ),
                )
            )
            for unit in package.units
        )
        lines.append(
            short(
                f"• GTIN: {package.gtin or 'brak'} | {package.availability or 'brak kategorii'}"
                f"{' | SKASOWANE' if package.deleted else ''} | {units}",
                240,
            )
        )
    lines.extend(("", freshness(snapshot, {product.dataset}), NOTICE))
    if product.dataset == Dataset.DEVICES:
        lines.append(
            "Źródło wyrobu: zweryfikowany import operatora, nie RPL ani kompletny rejestr."
        )
    return short("\n".join(lines), 3900)


APTEKA_PAGE = 5
APTEKA_ERRORS: dict[str, str] = {
    "produkt-nieznaleziony": "Nie znaleziono leku o takiej nazwie handlowej.",
    "miasto-nieznalezione": "Nie znaleziono takiej miejscowości.",
}


def apteka_error_text(code: str) -> str:
    return APTEKA_ERRORS.get(
        code, "Sprawdzenie dostępności nie powiodło się. Spróbuj ponownie za chwilę."
    )


def apteka_location_label(location: KtomalekLocation) -> str:
    return location.name or location.city or f"{location.latitude},{location.longitude}"


def apteka_choice_label(location: KtomalekLocation) -> str:
    label = location.name or location.city
    return label if label else f"{location.latitude},{location.longitude}"


def apteka_place_line(location: KtomalekLocation) -> str:
    place = location.city or location.name
    place = place or f"{location.latitude},{location.longitude}"
    return f"{place} ({location.voivodeship})" if location.voivodeship else place


def apteka_product_text(product: KtomalekProduct) -> str:
    lines = [f"Lek: {product.name}"]
    if product.dose_line:
        lines.append(f"Postać i opakowanie: {product.dose_line}")
    if product.info:
        lines.append(product.info)
    return "\n".join(lines)


def apteka_medication_line(medication: KtomalekMedication) -> str:
    status = medication.status or "dostępny"
    marker = "🟡" if medication.status == "ostatnie sztuki" else "🟢"
    line = f"{marker} {status} — {medication.name}"
    if medication.info:
        line += f" ({medication.info})"
    return line


def apteka_pharmacy_line(pharmacy: KtomalekPharmacy) -> str:
    title = pharmacy.name or f"Apteka {pharmacy.id}"
    if pharmacy.distance:
        title += f" — {pharmacy.distance}"
    lines = [f"🏥 {title}"]
    if pharmacy.address:
        lines.append(f"📍 {pharmacy.address}")
    lines.extend(apteka_medication_line(medication) for medication in pharmacy.medications)
    if pharmacy.open_status:
        lines.append(f"🕐 {pharmacy.open_status}")
    if pharmacy.phone:
        lines.append(f"📞 {pharmacy.phone}")
    return "\n".join(lines)


def apteka_results_text(
    product: KtomalekProduct,
    location: KtomalekLocation,
    pharmacies: list[KtomalekPharmacy],
    offset: int,
) -> str:
    pages = (len(pharmacies) + APTEKA_PAGE - 1) // APTEKA_PAGE
    lines = [
        "Dostępność w aptekach — dane z ktomalek.pl",
        apteka_product_text(product),
        f"Miejscowość: {apteka_place_line(location)}",
        f"Apteki z tym lekiem: {len(pharmacies)}."
        + (f" Strona {offset // APTEKA_PAGE + 1} z {pages}." if pages > 1 else ""),
        "",
    ]
    if pharmacies:
        lines.extend(
            f"{number}. {apteka_pharmacy_line(pharmacy)}"
            for number, pharmacy in enumerate(
                pharmacies[offset : offset + APTEKA_PAGE],
                start=offset + 1,
            )
        )
    else:
        lines.append("Brak aptek z tym lekiem w okolicy wybranej miejscowości.")
    lines.extend(
        (
            "",
            "Dostępność może być nieaktualna — potwierdź telefonicznie lub w aptece.",
            NOTICE,
        )
    )
    return short("\n".join(lines), 3900)
