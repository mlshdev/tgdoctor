# tgdoctor — stan końcowy, 2026-10-18 (funkcja apteczna wdrożona)

## Bieżące zadanie — /apteka (ktomalek.pl), zakończone

Użytkownik wdrożył funkcję sprawdzenia dostępności leku (wyłącznie
**nazwa handlowa**) w aptekach w miejscowości podanej przez użytkownika
(komenda `/apteka [nazwa]` lub przycisk „Sprawdź w aptece” w menu). Zgodnie
z badaniem (`ktomalek-api-reference.md`) integracja używa wyłącznie zwykłych
żądań HTTP do wewnętrznych endpointów XHR ktomalek.pl — **bez scrapingu**
wyrenderowanych stron. Na wyraźne życzenie użytkownika **bez linków URL**
w przyciskach i treściach (rezerwacja u użytkownika, nie w bocie).

## Cel i ustalenia

Katalog początkowo pusty, bez `.git`. Interfejs polski, Docker i CI wyłącznie
**linux/amd64** (jawne wymaganie użytkownika ma pierwszeństwo przed multi-arch).
Leki i surowce z RPL, osobny import wyrobów. Nie utożsamiać rejestru
z faktyczną dostępnością ani wnioskami klinicznymi.

## Zaimplementowano (funkcja apteczna)

- `src/tgdoctor/ktomalek.py` (nowy): klient `KtomalekClient` — trzy partnie
  sesyjne (produkty / lokalizacje / oferty), parser produktów
  (`data-bl/gr/si/ri`), JSON lokalizacji i parser kart aptek (ID, nazwa,
  odległość, adres z encjami, telefon, status otwarcia, statusy leków).
  Kody błędów: `produkt-nieznaleziony`, `miasto-nieznalezione`,
  `produkt-zmienil-sie` (deterministyczne — bez retry) oraz
  `transport/status/protocol/cart` (jeden retry całej partii, nowa sesja).
  Semafor 2 równoległych przepływów, timeout 15 s / connect 8 s,
  `follow_redirects=False`, `trust_env=False`, browserowy UA.
- `bot.py`/`presentation.py`: `/apteka`, przycisk menu, klawiature wyboru
  produktu (`aprod:{nonce}:{i}`) i lokalizacji (`aloc:{nonce}:{i}`),
  placeholder „Sprawdzam…” edytowany na wynik, stronicowanie aptek
  (`apage:{nonce}:{offset}`, 5/strona, render z RAM — bez ponownych żądań),
  cooldown 5 s na nowe wyszukiwanie nazwy, teksty polskie, brak linków
  wychodzących. Komunikat prywatności uzupełniony o przekazanie nazwy leku
  i miejscowości do ktomalek.pl.
- `tests/unit/tgdoctor/test_ktomalek.py` (nowy, 11 testów) + przepisany test dispatchu
  aptecznego w `test_bot.py`; fixtury HTML/JSON z realnych przechwyceń
  w `tests/conftest.py` (współdzielone). Regresja: stary callback
  `pharmacies` nadal ignorowany, przyciski bez URL.
- README.md/TECHNICAL.md: sekcje użycia, przepływu, etyki i prywatności.

## Kluczowe ustalenia techniczne z live weryfikacji (2026-10-18)

1. **Koszyk musi pochodzić z tej samej sesji co wyszukiwanie** — produkt
   (reportId) z innej sesji: `cart/add` odpowiada SUCCESS, ale `oferty`
   zwraca **302**. Dlatego partia ofertowa powtarza `szukajAjax`
   w swojej sesji (mint → szukaj → cart/add → localization → oferty).
2. **Lokalizację ofert pinuje się współrzędnymi** (`latitude`/`longitude`
   z wybranego geokodowania — odwrotne geokodowanie, jak cookie
   `cookie_wspolrzedne_gps`). Etykieta `name` z listy lokalizacji
   („Powiat, kod, województwo”) nie jest odwracalna przez `address`;
   kanoniczne „Miasto, województwo” geokoduje się błędnie do poziomu
   ulicy i oferty odpowiadają wtedy 302.
3. Live PASS z kontenera walidacyjnego: `apap` × Warszawa → 3 produkty,
   1 lokalizacja, **30 aptek**; pola (nazwa, „8,5 km”, adresy z encjami,
   telefon, status otwarcia, „ostatnie sztuki”) parsują się poprawnie.

## Walidacja — rzeczywiste wyniki

- `docker build --platform linux/amd64 --target validation …` na `homevm`:
  PASS; ruff/format/ty clean, **67 testów PASS (8,31 s)** (również lokalnie
  na Macu te same 67 przez `uv run --no-sync pytest`).
- `docker build --platform linux/amd64 --tag docker.io/local/tgdoctor:0.1.0 .`:
  PASS; runtime image 141 597 162 B, dive efficiency 97,23%
  (141 597 162 / 0,9722797 / 5 658 533 B).
- `TELEGRAM_BOT_TOKEN=smoke-only docker compose -p dev-tgdoctor-apteka
  config --quiet`: clean.
- Smoke kontenera `serve` (UID 1000, cap_drop ALL, no-new-privileges,
  tmpfs `/data`): `/health/live` 200; kontener usunięty. Uwaga: tmpfs
  `/data` bez `uid=1000` → `sqlite3.OperationalError` (własność katalogu);
  w Compose `/data` jest bind-mountem z `bot/data` o UID 1000.
- Nie uruchamiano prawdziwego bota z tokenem BotFather; nie zmieniono
  workflow na GitHub, nie publikowano do GHCR.

## Stan zasobów po testach

- Smoke-kontenery usunięte (`docker ps -a --filter name=dev-tgdoctor-apteka`
  puste). Nie uruchomiono żadnych trwałych kontenerów ani harmonogramów.
- Pozostawiono `homevm:dev/tgdoctor-apteka-20261018/` (checkout testowy
  tej zmiany) oraz lokalne obrazy testowe na `homevm`. Nie usuwać szeroko.
- `bot/data` na Macu dalej puste poza `.gitkeep`; testy lokalne biegają
  w istniejącym `.venv` projektu (utworzonym przed zasadą Linux-target-only);
  walidacją odniesienia pozostaje build dockerowy linux/amd64.

## Następny krok użytkownika

Utworzyć bota u BotFather, ustawić `TELEGRAM_BOT_TOKEN` w `.env`
(Linux VM wg README), `docker compose -p tgdoctor up -d --build --wait`,
wykonać rozmowę testową: `/start` → „Sprawdź w aptece” → nazwa handlowa →
wybór produktu → miejscowość → wynik; przetestować `/apteka Apap`
z miastem spoza Warszawy i nieistniejącą nazwą (obsługa błędów).

## Restrukturyzacja układu (2026-10-18)

Przeniesiono `tools/` → `scripts/`, płaskie testy → `tests/unit/tgdoctor/`
(lustro `src/tgdoctor/`), `test_http.py` → `test_httpserver.py`; usunięto
`tests/__init__.py` — pytest działa w trybie `--import-mode=importlib`
(bez `__init__.py`, bez sys.path). Importy pomocnicze przepisano na
`from tests.conftest import …` (bezwzględne). Aktualizacja: Dockerfile
(`COPY scripts`, lint `src tests scripts`), `.dockerignore`,
`update-tools.yml`, AGENTS.md, TECHNICAL.md, `.gitignore`
(`*/logs/*` odzyskuje `!*/logs/.gitkeep`; ignor ` .playwright-mcp/`).
Bramka lokalna po zmianach: ruff/format/ty czyste, 67 testów PASS.
Kontenerowy etap `validation` (build linux/amd64 na `homevm`): PASS —
ta sama bramka w kontenerze po przeniesieniu `scripts/` i `tests/unit/`.