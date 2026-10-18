# /// script
# requires-python = ">=3.14"
# ///
import json
import re
from pathlib import Path
from urllib.request import urlopen


def latest(package: str) -> str:
    with urlopen(f"https://pypi.org/pypi/{package}/json", timeout=30) as response:
        payload: object = json.loads(response.read())
    if not isinstance(payload, dict):
        raise ValueError("Invalid package metadata")
    info: object = payload.get("info")
    if not isinstance(info, dict):
        raise ValueError("Invalid package info")
    version: object = info.get("version")
    if not isinstance(version, str) or re.fullmatch(r"\d+\.\d+\.\d+", version) is None:
        raise ValueError("Expected a stable release")
    return version


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    manifest = root / "pyproject.toml"
    text = manifest.read_text()
    text = re.sub(r"hatchling==\d+\.\d+\.\d+", "hatchling==" + latest("hatchling"), text)
    uv_version = latest("uv")
    text = re.sub(
        r'required-version = "==\d+\.\d+\.\d+"', f'required-version = "=={uv_version}"', text
    )
    manifest.write_text(text)
    dockerfile = root / "Dockerfile"
    dockerfile.write_text(
        re.sub(
            r"ghcr.io/astral-sh/uv:\d+\.\d+\.\d+",
            "ghcr.io/astral-sh/uv:" + uv_version,
            dockerfile.read_text(),
        )
    )
    print(json.dumps({"status": "checked", "sources": ["hatchling", "uv"]}))


if __name__ == "__main__":
    main()
