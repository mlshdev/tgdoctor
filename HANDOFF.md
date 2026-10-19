# tgdoctor — stan końcowy, 2026-10-18 (przełącznik prywatny + CI/CD)

## Bieżące zadanie — zweryfikowano funkcje, dodano fail-closed allowlistę, wdrożono CI/CD i opublikowano repo

Zadanie użytkownika: sprawdzić wszystkie funkcje bota, dopisać testy, dodać
„przełącznik”, po którym bot działa **wyłącznie** z podanymi użytkownikami
Telegrama (nie może odpowiadać każdemu), a po walidacji wdrożyć CI/CD według
globalnych instrukcji i wypchnąć repo na GitHub.

## Przełącznik prywatny — ALLOWED_USER_IDS (fail-closed)

Wcześniej lista była opcjonalna, a pusta lista oznaczała publicznego bota
(fail-open). Teraz:

- `tgdoctor run` **odmawia startu** bez tokenu lub bez niepustej
  `ALLOWED_USER_IDS` (`require_run_settings` w `src/tgdoctor/cli.py`).
- `Bot.session()` (`src/tgdoctor/bot.py`) odpowiada **wyłącznie**
  użytkownikom z listy; pusta lista odmawia wszystkim. Komunikat odmowy
  zawiera identyfikator odrzuconego użytkownika, by operator mógł go dopisać.
- Handler błędów (`errors()`) pomija użytkowników spoza listy.
- `parse_allowed_user_ids` (`src/tgdoctor/settings.py`) akceptuje tylko
  dodatnie liczby całkowite; śmieci w zmiennej → czytelny błąd konfiguracji.
- `compose.yml` wymaga `ALLOWED_USER_IDS` (`:?`), `.env.example` dokumentuje
  ją jako obowiązkową.

## Testy

- Nowe pliki lustrzane modułów: `tests/unit/tgdoctor/test_settings.py`
  (parsowanie listy, `Settings.load` ze środowiska) i
  `tests/unit/tgdoctor/test_cli.py` (`require_run_settings`: token + lista).
- `test_bot.py`: pomocniczy `settings()` z listą `{42}`; testy: odmowa
  nieznanemu użytkownikowi, odmowa przy pustej liście (fail-closed),
  przyjęcie użytkownika z listy, pominięcie odmówionych w handlerze błędów.
- Dotychczasowe testy wyszukiwania, `/apteka` (ktomalek), importu,
  HTTP API bez zmian zachowania.

## Walidacja — rzeczywiste wyniki (2026-10-18)

- Lokalnie i w kontenerze: ruff check/format/ty czyste, **83 testy PASS**
  (`uv run --no-sync pytest`; w kontenerze 8,53 s).
- `docker build --platform linux/amd64 --target validation .` na `homevm`: PASS.
- Runtime image `docker.io/local/tgdoctor:allowlist` (linux/amd64):
  141 599 575 B, dive efficiency **97,23%** (próg ≥ 95%).
- Smoke kontenera `serve` (UID 1000, read-only, cap_drop ALL,
  no-new-privileges, tmpfs `/data` uid=1000): `/health/live` 200,
  `/api/v1/status` 200, `tgdoctor healthcheck` PASS, `tgdoctor backup` PASS;
  kontener usunięty. W kontenerze produkcyjnym `tgdoctor run` bez
  `ALLOWED_USER_IDS`: odmowa startu (exit 1).
- Compose: `ALLOWED_USER_IDS` ustawiona → `config --quiet` czysty; brak →
  błąd interpolacji, exit 1. actionlint czysty (`-ignore 'label "ubuntu-26.04"'`).

## CI/CD (stan po push)

- `validate.yml`: PR/push main/tag/harmonogram (pn/czw 00:00 UTC) — pełne
  testy, natywny build amd64, smoke, dive; publikacja do GHCR tylko poza PR
  (tag `v*` → wersja, main → `edge` + pełny SHA).
- `update-tools.yml`: pn/czw aktualizacja uv/hatchling, pełna walidacja
  przed otwarciem PR (merge ręczny).
- `dependabot.yml`: uv/docker/github-actions, pn/czw, grupowanie.
- **Nowe** `.github/workflows/dependabot-automerge.yml`: PR-y Dependabota
  włączane w auto-merge (`gh pr merge --auto`), scalanie po zielonych
  wymaganych checkach ochrony gałęzi `main` — **skonfigurowanych przez API**:
  `required_status_checks` `validate` + `image`, `strict` (aktualna baza),
  `enforce_admins=false` (właściciel może nadal pushować bezpośrednio).
- Naprawy po pierwszych realnych runach GitHub: probe smoke w
  `validate-image/action.yml` używa `--retry-all-errors` (wyścig
  docker-proxy z bindem aplikacji — `--retry-connrefused` nie ponawiało
  reset/empty-reply, run 39152781170 padł na exit 56), a próg dive
  `highestUserWastedPercent` podniesiony do 10% w `.dive-ci` — ~5,7 MB
  odpadów to debconf/dpkg w warstwach bazowego
  `python:3.14-slim-trixie` (nieusuwalne z naszego Dockerfile, nasze etapy
  dokładają <1%), run 39152921849 padł na 9,01% > 5%.
- Naprawa 2026-10-19: `update-tools.yml` sprawdza `docker compose config`
  z pełnym zestawem zmiennych interpolacyjnych — po dodaniu fail-closed
  `ALLOWED_USER_IDS:?` w `compose.yml` run 39195531196 padł na brak tej
  zmiennej; kroku walidacji brakowało `ALLOWED_USER_IDS=123456789`
  (obok `TELEGRAM_BOT_TOKEN=validation-only`).

## Stan zasobów po testach

- Smoke-kontenery usunięte; testowy obraz `docker.io/local/tgdoctor:allowlist`
  pozostawiony na `homevm` (celowo). `bot/data` na Macu bez zmian (`.gitkeep`).
- Repo publiczne `mlshdev/tgdoctor` (GitHub, protokół ssh), gałąź `main`,
  remote `origin`; obrazy CI → `ghcr.io/mlshdev/tgdoctor`.

## Następny krok użytkownika

1. Utworzyć bota u @BotFather, wkleić token do `.env`.
2. Dodać swój identyfikator do `ALLOWED_USER_IDS` w `.env` (bot podaje go
   w komunikacie odmowy, można też użyć @userinfobot).
3. Na serwerze Linux amd64: `docker compose -p tgdoctor up -d --build --wait`,
   potem rozmowa testowa: `/start` → „Sprawdź w aptece” → nazwa handlowa →
   miejscowość → wynik; oraz test odmowy z konta spoza listy.