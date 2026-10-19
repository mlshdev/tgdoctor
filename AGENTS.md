# tgdoctor

Polish pharmaceutical registry search for Telegram. Python 3.14 (uv, hatchling),
SQLite catalog, Polish UI. Docker and CI are **linux/amd64 only** (explicit project
requirement; takes precedence over the usual multi-arch rule).

## Layout

Python — PyPA src layout, unit tests mirror the package under `tests/unit/`:

```
src/tgdoctor/                        # importable code only; flat modules, no subpackages
src/tgdoctor/cli.py                  # [project.scripts] entry point: tgdoctor = tgdoctor.cli:main
tests/conftest.py                    # shared fixtures + helpers: tmp_path SQLite catalog, captured HTML/JSON payloads
tests/unit/tgdoctor/test_<name>.py   # mirrors src/tgdoctor/ (test_httpserver.py ↔ httpserver.py)
scripts/update_tools.py              # dev script (PEP 723 inline metadata), updates tool pins; not shipped
```

- New module → `src/tgdoctor/<name>.py`; its test → `tests/unit/tgdoctor/test_<name>.py` (one file per module, name mirrors the module).
- pytest config in `pyproject.toml`: `testpaths = ["tests"]`, `addopts = "-q --import-mode=importlib"`, async tests via `@pytest.mark.asyncio` (pytest-asyncio strict mode). Run via `uv run pytest`; no `sys.path` hacks, no `__init__.py` in tests.
- Tests import shared helpers via `from tests.conftest import …` (absolute, never relative).
- Pins live in `pyproject.toml` + `uv.lock` (`[tool.uv] required-version`, dev group: pytest, pytest-asyncio, ruff, ty, types-defusedxml).
- Docs at root: `README.md` (user guide, Polish), `TECHNICAL.md` (architecture/design, Polish), `HANDOFF.md` (work-in-progress state, Polish).

Containers — single-service repo at root:

```
Dockerfile                # root; stages: uv → builder → validation → runtime; fails on non-amd64
compose.yml               # local stack: one bot service, loopback 127.0.0.1:3000
.dockerignore
.dive-ci                   # dive thresholds: efficiency ≥ 0.95, user-wasted ≤ 10% (base-image debconf/dpkg churn dominates)
bot/config  bot/data  bot/logs   # bind-mount sources (.gitkeep) — runtime state, never source
```

- `src/tgdoctor/bot.py` is the bot source; the root `bot/` directory is only Compose bind-mount state (`bot/data` → `/data` SQLite, `bot/config` → `/config` read-only).
- The `validation` stage runs `ruff check` / `ruff format --check` / `ty check` / `pytest` in-container; the runtime image carries none of it.
- CI: `.github/workflows/validate.yml` (PR/push/schedule gate + image build, push on non-PR) and `update-tools.yml` (twice-weekly tool-pin updates → PR); container smoke + dive via `.github/actions/validate-image/action.yml`.

## Testing

- Unit: `uv run pytest` · one file: `uv run pytest tests/unit/tgdoctor/test_bot.py` — 67 offline tests (no network; SQLite in `tmp_path`). On the pre-synced local `.venv`, prefix `--no-sync`: `uv run --no-sync pytest tests/unit/tgdoctor/test_ktomalek.py`.
- Integration: `docker build --platform linux/amd64 --target validation .` — runs the full gate in-container (needs: Docker context `homevm`, network for base images). `TELEGRAM_BOT_TOKEN=validation-only ALLOWED_USER_IDS=123456789 docker compose -p dev-tgdoctor config --quiet` (needs: real values in `.env` only for `up`). No DB service — SQLite lives under `/data`.
- E2E: none automated. Manual: real token in `.env`, `docker compose -p tgdoctor up -d --build --wait`, then a Telegram conversation (`/start` → „Sprawdź w aptece” → product → miejscowość). Closest automated check: non-root read-only container smoke in `.github/actions/validate-image` (`serve`, `/health/live`, `/api/v1/status`, `tgdoctor healthcheck`, `tgdoctor backup`).
- Before finishing: `uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest` — exactly the CI gate in `validate.yml`.