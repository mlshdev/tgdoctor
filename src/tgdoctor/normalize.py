import unicodedata

ALIASES: tuple[tuple[str, ...], ...] = (
    ("alprazolam", "alprazolamum"),
    ("paracetamol", "paracetamolum"),
    ("ibuprofen", "ibuprofenum"),
    ("diazepam", "diazepamum"),
    ("lorazepam", "lorazepamum"),
    ("clonazepam", "clonazepamum", "klonazepam"),
    ("metformin", "metforminum", "metformina"),
    ("omeprazole", "omeprazol", "omeprazolum"),
)


def normalize(value: str) -> str:
    value = value.casefold().translate(str.maketrans({"ł": "l"}))
    return " ".join(
        "".join(
            char for char in unicodedata.normalize("NFKD", value) if not unicodedata.combining(char)
        ).split()
    )


def alternatives(value: str) -> tuple[str, ...]:
    value = normalize(value)
    return next((group for group in ALIASES if value in group), (value,))


def substance_index(names: list[str]) -> str:
    return normalize(" | ".join(alias for name in names for alias in alternatives(name)))


def pattern(value: str) -> str:
    return (
        "%" + normalize(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    )
