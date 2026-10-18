# Architektura

## Decyzje i zakres

Python 3.14 wybrano ze względu na przetwarzanie XML/JSON i dojrzały stos botów.
`python-telegram-bot` obsługuje asynchroniczny Telegram Bot API; `httpx` pobiera
źródła, `defusedxml` bezpiecznie parsuje XML strumieniowo, Pydantic definiuje
kontrakty, SQLite przechowuje katalog. Wersje i hashe zależności są w `uv.lock`.
Python ograniczono do 3.14 do czasu osobnej walidacji kolejnego runtime.

Pojedynczy proces bota i pojedynczy writer. SQLite WAL znajduje się na lokalnym
dysku pod `/data`, **nie na sieciowym filesystemie ani współdzielonym dysku
kilku hostów**. `bot.lock` wyklucza drugi proces long pollingu, `import.lock`
wyklucza równoległe importy CLI i harmonogramu. Blokady `flock` są zwalniane po
zakończeniu procesu. Do HA/autoskalowania trzeba zmienić bazę i model obsługi
aktualizacji Telegram, a nie uruchamiać kolejnych writerów SQLite.

Docker i CI publikują tylko `linux/amd64`, zgodnie z wymaganiem użytkownika.
Dockerfile odrzuca inne architektury w buildzie, CLI dodatkowo sprawdza architekturę
wewnątrz kontenera. Brak QEMU. Produkcyjny obraz buduje CI, nie `homevm`.

## Moduły

| Plik w `src/tgdoctor/` | Odpowiedzialność |
|---|---|
| `models.py` | Ścisłe modele domenowe i API; camelCase na wyjściu HTTP |
| `sources.py` | Zamknięte kontrakty źródeł i sprawdzanie originów |
| `xmlfeed.py` | Parser strumieniowy, zachowanie źródłowego drzewa elementu |
| `importer.py` | Pobranie, metadane, hashe, niezależne importy, blokada writera |
| `catalog.py` | Transakcje SQLite, wyszukiwarka, snapshoty i Backup API |
| `normalize.py` | Unicode i jawna, ograniczona lista aliasów substancji |
| `bot.py`, `presentation.py` | Prywatne czaty, menu, filtry, strony i opis pochodzenia |
| `ktomalek.py` | Klient dostępności aptecznej ktomalek.pl: sesje, przepływ, parsowanie |
| `httpserver.py` | Wewnętrzne read-only API i healthcheck |
| `settings.py`, `logging.py`, `cli.py` | Konfiguracja, redakcja tokenu, lifecycle |

## Kontrakty importu

| Zbiór | Resource / parent | Namespace | Minimum |
|---|---|---|---|
| medicinal-products | `65522` / `397` | eksport-danych-v6.0.0 | 10 000 |
| raw-materials | `52341` / `2001` | eksport-danych-v5.0.0 | 100 |

1. Sprawdzenie `data.id`, `type`, parent dataset, formatu, daty metadanych i
   dokładnego `attributes.link`. Nie używamy alternatywnego CSV ani linku cache.
2. Pobranie `commonTypes.xsd`, sprawdzenie namespace i wersji `2.0.0`.
3. Pobranie XML, SHA-256, weryfikacja namespace i `stanNaDzien`.
4. Parsowanie i zapis w transakcji `BEGIN IMMEDIATE`; czytelnicy nadal widzą
   poprzednią wersję dzięki WAL. Produkt identyfikuje para `(dataset, id)`.
5. Sprawdzenie minimum i braku spadku liczby produktów/opakowań o ponad 20%
   względem poprzedniego importu. Odrzucenie przyszłej/cofniętej daty XML.
6. Atomowy commit produktów, opakowań, snapshotu i udanej próby; przy błędzie
   rollback i zapis nieudanej próby bez podmiany wcześniejszego snapshotu.

Źródła leków i surowców mają osobne transakcje: sukces jednego nie zależy od
sukcesu drugiego. Daty metadanych i XML nie muszą być równe — obserwowano
metadane 2026-10-17 oraz XML 2026-10-18. Nie łączymy ich semantyki.
Zmiana wersji lub kontraktu zatrzymuje import do przeglądu kodu. Legalny duży
spadek liczebności również wymaga przeglądu; nie obniżaj minimum w produkcji,
aby przepchnąć nieudany import.

Downloady oficjalnych źródeł dopuszczają tylko dokładne HTTPS originy
`rejestry.ezdrowie.gov.pl` i `api.dane.gov.pl`, bez userinfo, niestandardowych
portów i przekierowań. `trust_env=False` wyłącza niejawne proxy środowiskowe.
Limity: metadane 2 MiB, common XSD 1 MiB, XML 150 MiB, głębokość XML 32;
connect timeout 15 s, timeout odczytu 120 s, deadline pobrania 300 s sprawdzany
między blokami danych (oczekiwanie na bieżący blok ogranicza timeout odczytu).
DTD, encje i obce namespace są
odrzucane. To **walidacja kontraktu i struktury**, nie pełna walidacja XML
przez XSD. Nie pobieramy zdalnych schematów wskazanych wewnątrz dokumentu.
Zapisujemy hashe XML i common XSD, nie archiwizujemy całych downloadów.

Do `source` trafiają wszystkie atrybuty i dzieci sparsowanego produktu; liczby
w źródle, w tym GTIN, ID i `1,54`, pozostają tekstem. W indeksie wyszukiwania
GTIN jest uzupełniony zerami do 14 cyfr; JSON zachowuje oryginał. Linki do
dokumentów są tylko linkami, nie klinicznym JSON-em. Brak analiz COA/partii.

## Wyszukiwanie

Parametryzowany SQL, escapowane `%` i `_`, lista kolumn kontrolowana przez kod.
Normalizacja Unicode obejmuje `ł` oraz znaki diakrytyczne, ale nie przelicza
jednostek ani wartości mocy. Wielowyrazowe `q` wymaga wszystkich słów. ATC jest
prefiksem, GTIN dopasowaniem dokładnym po normalizacji, kategoria dopasowaniem
dokładnym. GTIN i kategoria muszą dotyczyć tego samego opakowania.
Domyślny `kind=ludzki`; pusty filtr obejmuje również pozostałe rodzaje.

Aliasowanie jest funkcją wyszukiwarki, nie modyfikacją danych urzędowych ani
stwierdzeniem klinicznej równoważności. Rozszerzając `ALIASES`, dodaj test i
sprawdź nazwy/sole; nie wprowadzaj automatycznego stemmingu łaciny.

Wyszukiwanie używa znormalizowanych pól i indeksu GTIN, nie zewnętrznego silnika
ani LLM. Dla tej wielkości zbioru jest to prosty, mierzalny punkt wyjścia;
dalsza skala może wymagać indeksów ATC/FTS i rozdzielenia pól szczegółowych.

## Telegram i lifecycle

`run` inicjalizuje bazę i blokadę bota, uruchamia HTTP, a następnie PTB long
polling. `post_init` ustawia komendy i uruchamia importer w wątku, więc pobieranie
i parsowanie nie blokują pętli Telegrama. Import powtarza się po zakończeniu
poprzedniego cyklu i odczekaniu skonfigurowanego interwału. `SIGTERM` zatrzymuje
polling, sygnalizuje zatrzymanie harmonogramu, czeka na import i zamyka HTTP.
Compose daje 15 minut na shutdown; ewentualny SIGKILL pozostawia transakcję
SQLite do odzyskania, nie publikuje połowy katalogu.

Prywatne czaty, opcjonalna allowlista, odstęp 0,7 s na użytkownika. Przetwarzanie
aktualizacji jest sekwencyjne. Wyniki i sesje tylko w RAM, TTL 30 minut,
sprzątanie co 60 s, limit 5000. Usuwane są też pomocnicze cache user/chat PTB.
Callbacki stron i produktów używają losowego nonce i indeksu; stare klawiatury
są odrzucane. Dane callbacków mieszczą się w 64 bajtach. Tekst wyświetlany bez
interpretacji HTML, długości wiadomości i stron ograniczone.

### Dostępność apteczna — /apteka (ktomalek.pl)

Klient w `ktomalek.py` korzysta z wewnętrznych endpointów XHR serwisu
ktomalek.pl, zweryfikowanych 2026-10-18: zwykłe żądania HTTP, bez kluczy,
tokenów CSRF ani challenge; jedyny stan to servletowa sesja `JSESSIONID`.
**To nie jest scraping renderowanych stron** — dwie odpowiedzi to fragmenty
HTML generowane z jednego stabilnego szablonu (`.cart-item`, `.la-apteka`),
parsowane defensive regexami z dekodowaniem encji; pozostałe to JSON.

Przepływ użytkownika: nazwa handlowa → wybór produktu → miejscowość →
wybór lokalizacji → lista aptek (stronicowanie z RAM, bez ponownych żądań).
Kroki sieciowe klienta (odpowiednio 2, 2 i 5 żądań, każda partia we
własnej świeżej sesji — sesja nie jest trzymana między aktualizacjami
Telegrama):

1. **Wyszukanie leku**: `GET /leon-www/produkty/szukajAjax?searchInput=…`
   → karty produktów (`data-bl/gr/si/ri`, nazwa, dawka, kategoria OTC/Rx).
2. **Geokodowanie miejscowości**: `GET /leon-www/localization?address=…`
   → 0 wyników („nieznana miejscowość”), 1 (auto) lub >1 (klawiatura
   inline; etykieta `name` zawiera powiat/kod, np. „Płoński, mazowieckie”).
3. **Oferty w jednej sesji**: mint → ponowne `szukajAjax` (świeży
   `reportId` w sesji ofertowej — produkt dodawany do koszyka musi pochodzić
   z tej samej sesji co wyszukiwanie, inaczej `oferty` zwraca 302) →
   `POST /leon-www/serwispacjenta/cart/add` (JSON: bloz, groupId,
   searchInput, reportId, source `"WPR"`, pharmacyId `null`) →
   `localization` z **`latitude`/`longitude` wybranej lokalizacji**
   (odwrotne geokodowanie — dokładnie to trzyma cookie
   `cookie_wspolrzedne_gps`; etykiety `name` nie da się bezpiecznie
   odtworzyć parametrem `address`, a kanoniczny „Miasto, województwo”
   geokoduje się błędnie do poziomu ulicy) →
   `GET /leon-www/oferty/KLEK/szukaj/ajax` → karty aptek: ID, nazwa,
   odległość, adres, telefon, status otwarcia i statusy leków
   (`.la-lek-ilosc`; pusty → „dostępny”, `ostatnie sztuki` → 🟡).

Kod błędów: `produkt-nieznaleziony`, `miasto-nieznalezione`,
`produkt-zmienil-sie` (lek zniknął z katalogu między krokami — wyniki
deterministyczne, bez ponowień) oraz `transport`/`status`/`protocol`/`cart`
(przejściowe — jeden retry całej partii z nową sesją). Limity: globalnie
maks. 2 równoległe przepływy (semafor), 5 s odstępu między nowymi
wyszukaniami nazwy na użytkownika, timeout 15 s odczyt / 8 s connect,
`follow_redirects=False` (302 to błąd protokołu tej integracji, nie
instrukcja), `trust_env=False`, browserowy User-Agent. Przetwarzanie
aktualizacji PTB pozostaje sekwencyjne, więc jeden użytkownik nie
zrównolegla przepływu. Wyniki są tylko w RAM sesji (TTL 30 minut).

Etyka użycia: ktomalek.pl to bezpłatny serwis publiczny bez opublikowanych
warunków API — bot utrzymuje wolumen żądań zbliżony do człowieka (9 żądań
na pełne zapytanie, brak harmonogramów, brak pollingu, brak cacheingu
odpowiedzi na dysku). Zgodnie z decyzją użytkownika z 2026-10-18: **bez**
linków URL w przyciskach i treściach — wyłącznie dane tekstowe (nazwa,
adres, telefon); rezerwacja w aptece należy do użytkownika.

## API

- `GET /health/live`: proces HTTP żyje; nie dowodzi łączności z Telegramem.
- `GET /health/ready`: 200, gdy oba źródła RPL są obecne i nie są stare;
  inaczej 503. Brak importu wyrobów nie blokuje gotowości.
- `GET /api/v1/status`: `CatalogSnapshot`, zawsze 200 przy działającej bazie.
- `GET /api/v1/medications`: `Results` z pełnymi `ProductSummary`.
- `GET /api/v1/medications/{id}?dataset=...`: `Detail`, wymagany jawny zbiór.

Parametry listy: `q`, `dataset`, `name`, `substance`, `atc`, `gtin`, `form`,
`strength`, `holder`, `route`, `availability`, `kind`, `activeOnly`, `limit`,
`offset`. Akceptowany jest też zapis `active_only`. Limit 1–50, offset 0–10000,
frazy do 160 znaków. Nieznane/powtórzone parametry i błędne wartości → 400;
brak produktu → 404. API jest wewnętrzne, bez autoryzacji, i nie powinno być
wystawiane publicznie. Zewnętrzny gateway wymaga osobnej warstwy auth,
rate limitów i świadomie skonfigurowanego prywatnego listenera/proxy.

## Kontener i operacje

Multi-stage build, Debian Trixie slim, UID/GID 1000, read-only rootfs,
`cap_drop: [ALL]`, puste `cap_add`, `no-new-privileges`, loopback ingress.
`/data` jest jedynym trwałym zapisywalnym mountem, `/tmp` to tmpfs. Dla `/data`
celowo użyto długiej składni bind mount z `create_host_path: false`, aby błędna
ścieżka nie tworzyła pustego katalogu na zdalnym hoście. `bot/config` to read-only
pliki wejściowe operatora. Logi aplikacji na stdout jako JSON; rotacja `json-file`
10 MB × 3. Nie zapisujemy kopii logów do `bot/logs` ani nie montujemy engine socket.
Token jest redagowany w formatterze; biblioteczne logi HTTP są wyłączone.

Pojedyncza VM oznacza przerwę przy aktualizacji, awarii hosta/dysku lub strefy.
Monitoruj zewnętrznie `/health/ready`, błędy importów, zużycie dysku i stan
pollingu Telegram (sam HTTP live tego nie potwierdza). `restart: unless-stopped`
odtwarza proces po wyjściu, ale Docker nie restartuje automatycznie kontenera
tylko dlatego, że healthcheck jest czerwony. Nie uruchamiaj dwóch pollerów
z jednym tokenem. Po zastąpieniu hosta odłącz stary writer i dopiero wtedy
podłącz dysk/odtwórz kopię na nowym hoście.

Aktualizacja: spójny backup, nowy zweryfikowany obraz z CI/release, `compose up
-d --wait`, sprawdzenie readiness i rozmowy testowej. Rollback: poprzedni obraz;
przed zmianą schematu zachowaj też odpowiadającą mu kopię bazy. Wersja bazy
jest sprawdzana; nieobsługiwany `user_version` powoduje odmowę startu.

### Backup i odtworzenie

SQLite Backup API kopiuje logiczny, spójny stan wraz ze zmianami WAL:
<https://www.sqlite.org/backup.html>, ograniczenia WAL:
<https://www.sqlite.org/wal.html>. Nie kopiuj samego aktywnego `catalog.sqlite3`
ani nie rób niezależnych snapshotów plików bazy/WAL.

1. `tgdoctor backup --to /data/backups/unikalna-nazwa.sqlite3` przy działającym
   procesie. Polecenie odmawia nadpisania istniejącego pliku i robi integrity check.
2. Zaszyfruj i przenieś kopię poza host; ustal retencję, np. 7 dziennych i
   4 tygodniowe, oraz alert błędu backupu. Harmonogram/remote storage nie są tu
   automatycznie provisionowane — operator musi je skonfigurować.
3. Próbę odtworzenia wykonuj **w nowym pustym katalogu danych** UID 1000:
   umieść kopię pod nazwą `catalog.sqlite3`, uruchom `status`/`serve` z osobnym
   `DATA_DIR`, sprawdź liczniki, daty, rewizję i przykładowe wyszukiwania.
4. Przy realnym restore zatrzymaj bota i importer, zachowaj całe poprzednie
   `/data` do diagnostyki, użyj nowego katalogu i popraw mount. Nie łącz starego
   `-wal`/`-shm` z odtworzoną bazą. Włącz tylko jeden proces po weryfikacji.

Kopia przechowuje katalog, nie token i nie historię rozmów (takiej historii
aplikacja nie zapisuje). Kopie na tym samym dysku nie chronią przed jego awarią.

## Rozwój i walidacja — Linux amd64

Nie pobieraj zależności aplikacji dla macOS/Windows. Na tym Macu buduj i testuj
przez `homevm`; oba poniższe buildy wykonują się na zdalnym silniku:

```sh
docker build --platform linux/amd64 --target validation .
docker build --platform linux/amd64 --tag docker.io/local/tgdoctor:0.1.0 .
dive docker.io/local/tgdoctor:0.1.0
```

Etap `validation` instaluje zależności developerskie wyłącznie w Linux amd64 i
uruchamia Ruff, format check, ty oraz pytest. Obraz runtime ich nie zawiera.
W Linuxowym checkout można używać `uv sync --locked`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run ty check`, `uv run pytest`.
Zmiana kontraktu, filtra, importu lub menu wymaga testu regresyjnego.

## CI i zależności

`.github/workflows/validate.yml`: każdy PR i push do main, release tag, ręcznie
oraz poniedziałek/czwartek 00:00 UTC. Pełne testy, build natywny amd64,
read-only smoke test i dive przed publikacją. Cron wymusza `pull` i `no-cache`,
co odświeża także mutable warstwy bazowego obrazu. Publikacja do GHCR tylko poza
PR, po walidacji; tag `v0.1.0` daje `:0.1.0`, main daje `:edge` i pełny SHA.
W produkcji wybieraj stabilny release, nie `edge` ani digest w Compose.

Dependabot: uv, Docker i GitHub Actions, grupowanie per ekosystem, wszystkie
stabilne wersje, dwa razy w tygodniu. Osobny `update-tools.yml` sprawdza build
backend/uv i aktualizuje lock transitive; przed otwarciem PR wykonuje testy,
build, smoke, dive i Compose validation. `scripts/update_tools.py` ma tylko stdlib,
aktualizuje `tool.uv.required-version` i etap uv w Dockerfile razem. Dive w CI
pochodzi z aktualnego stabilnego wydania GitHub pobranego przez `gh`.

Workflow wymaga uprawnień GHCR i możliwości tworzenia PR przez Actions. PR-y
utworzone przez `GITHUB_TOKEN` nie wywołują automatycznie kolejnego workflow;
dlatego walidacja aktualizacji narzędzi odbywa się przed otwarciem PR. Merge
ręczny. Przed użyciem skonfiguruj ochronę main i wymagane checki `validate` oraz
`image`. Nie aktywowano automerge ani nie zmieniano ustawień GitHub.

## Pomiary i ograniczenia walidacji (2026-10-18)

Na `homevm`, bez dodatkowych capabilities, wykonano rzeczywisty import:
22 943 rekordy / 88 476 opakowań leków i 656 rekordów / 3431 opakowań surowców.
Snapshot XML obu: 2026-10-18. Wyszukiwanie `alprazolam` i `alprazolamum`:
po 148 wyników w domyślnych filtrach; to nie rekomendacja ani stan aptek.
Wszystkie endpointy testu HTTP i readiness odpowiedziały poprawnie. Walidacja
Linux amd64 po wdrożeniu funkcji aptecznej: **67 testów PASS (8,31 s)**,
Ruff/format/ty clean; Compose config clean. Live sprawdzenie integracji
ktomalek.pl z kontenera walidacyjnego: `apap` × Warszawa → 3 produkty,
1 lokalizacja, 30 aptek z parsowanymi nazwami, odległościami, adresami
(encje zdekodowane), telefonami, statusami otwarcia i statusami leków.
Przykładowe czasy końcowego API przez tunel: GTIN 6,57 ms, substancja
120,04 ms, ATC 183,16 ms. Zawierają opóźnienie SSH i nie są benchmarkiem
obciążeniowym.

Końcowy runtime image: 141 597 162 bajtów nieskompresowane, dive
efficiency 97,23%, około 5,66 MB zduplikowanych plików, głównie w bazowym
Debianie. Testy Telegram używają realnego dispatchera PTB i symulowanych
odpowiedzi API, bez prawdziwego tokenu; integracja ktomalek jest testowana
na fixture’ach z realnych przechwyceń. Nie przetestowano fizycznego
restartu VM, rootless Docker, Podman ani Kubernetes. Nie wykonano wdrożenia
chmurowego, publikacji do GHCR ani uruchomienia workflow na GitHub,
ponieważ projekt nie ma skonfigurowanego repozytorium/remote. Poprzednio
potwierdzono też spójny backup SQLite przy żywym serwerze (23 599 produktów /
91 907 opakowań) oraz czysty actionlint/shellcheck — niniejsza zmiana tych
elementów nie dotyka.
