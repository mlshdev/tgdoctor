# tgdoctor

Polskojęzyczny bot Telegram do wyszukiwania produktów z polskiego Rejestru
Produktów Leczniczych (RPL), surowców farmaceutycznych oraz osobno importowanego
katalogu wyrobów medycznych. Python 3.14, SQLite i Docker **wyłącznie linux/amd64**.

## Co potrafi

- Interaktywne menu, szczegóły produktu, stronicowanie wyników i opakowań.
- Nazwa handlowa i poprzednia, substancja, nazwa powszechna, ATC/prefiks ATC,
  GTIN/EAN, postać, moc, podmiot/producent, droga podania i kategoria dostępności.
- Wyszukiwanie bez rozróżniania wielkości liter i polskich znaków: `zolc` → `Żółć`.
- Łacińskie nazwy z RPL; jawne aliasy m.in. `alprazolam` / `alprazolamum`.
  Nie zgaduje tożsamości substancji przez obcinanie końcówek nazw.
- Automatyczny import przy starcie i co 24 godziny; oddzielne daty i statusy
  leków oraz surowców. Błąd importu nie usuwa ostatniego poprawnego katalogu.
- Linki do ulotki i ChPL, jeśli występują w rejestrze.
- Import zweryfikowanych wyrobów z JSON operatora, z udokumentowanym źródłem,
  prawem użycia i potwierdzeniem rynku polskiego.
- **/apteka** — sprawdzenie, w których aptekach w wybranej miejscowości
  dostępny jest lek podany **nazwą handlową** (źródło: ktomalek.pl).

**Wpis w rejestrze nie oznacza faktycznej dostępności w aptece ani ważności
pozwolenia w danym dniu.** Bot nie ocenia wskazań, dawkowania, interakcji,
zamienności, skuteczności ani wycofania konkretnej serii. Nie zastępuje farmaceuty.
Domyślnie pokazuje preparaty ludzkie z przynajmniej jednym nieskasowanym
opakowaniem. Status „nieskasowane” jest statusem rekordu, nie stanem magazynowym.

## Sprawdzanie dostępności w aptekach — /apteka

`/apteka` sprawdza dostępność leku podanego **nazwą handlową** (np. Apap,
Ibuprom) w aptekach w miejscowości podanej przez użytkownika — z menu
(`Sprawdź w aptece`) lub komendą `/apteka <nazwa handlowa>`:

1. Bot pyta o nazwę handlową; przy wielu produktach o tej samej nazwie
   pokazuje klawiaturę wyboru (postać/opakowanie).
2. Bot pyta o miasto lub miejscowość; przy wielu dopasowaniach
   pokazuje klawiaturę wyboru (etykieta z powiatem i województwem).
3. Bot zwraca listę aptek z odległością, adresem, telefonem, godzinami
   i statusem leku (`ostatnie sztuki` itp.), po 5 aptek na stronę.

Źródłem danych jest serwis **ktomalek.pl** (publiczny serwis zdrowia,
bez opublikowanego API). Bot korzysta wyłącznie ze zwykłych żądań HTTP
(tych samych, które wykonuje przeglądarka na stronie) — bez scrapingu
wyrenderowanych stron i bez automatycznego odświeżania. Zapytania są
wykonywane wyłącznie na żądanie użytkownika; bot nie odpytuje serwisu
wg harmonogramu i nie przechowuje wyników po wygaśnięciu sesji
(30 minut). Odpowiedzi są walidowane (sesja `JSESSIONID`, kod odpowiedzi,
struktura fragmentów HTML) i przy przejściowej anomalii przepływ jest
jednorazowo ponowiony z nową sesją.

**Prywatność:** nazwa leku i miejscowość podane w /apteka trafiają
do ktomalek.pl (zewnętrzny serwis) razem z adresem IP serwera bota.
Nie podawaj adresu zamieszkania — wystarczy miejscowość. Bot nie zbiera
lokalizacji z Telegrama i nie zapisuje zapytań.

**Bot nie dodaje linków do ktomalek.pl w odpowiedziach** — zwraca wyłącznie
dane tekstowe (nazwa apteki, adres, telefon). Rezerwacja w aptece, jeśli
potrzebna, odbywa się samodzielnie przez użytkownika.

Dostępność w aptece nie jest wpisem w rejestrze RPL i może być nieaktualna;
potwierdź telefonicznie lub w aptece. To nie jest porada medyczna.

## Uruchomienie na serwerze Linux amd64

1. W Telegramie utwórz bota przez **@BotFather** (`/newbot`). Wyłącz możliwość
   dodawania do grup (`/setjoingroups`); aplikacja i tak obsługuje tylko prywatne czaty.
2. Skopiuj `.env.example` do `.env`, ustaw `TELEGRAM_BOT_TOKEN`
   **oraz `ALLOWED_USER_IDS`** — bot odpowiada **wyłącznie** wymienionym tam
   operatorom i odmawia startu bez tej listy. Swój numeryczny identyfikator
   Telegrama sprawdzisz przez @userinfobot albo w komunikacie odmowy bota
   (bot podaje go w odpowiedzi). Nie publikuj tokenu ani nie wklejaj go
   do czatu z asystentem.
3. Przygotuj uprawnienia katalogu danych i uruchom kontener:

```sh
cp .env.example .env
# Edytuj .env przed uruchomieniem.
sudo install -d -o 1000 -g 1000 -m 0700 bot/data
chmod 600 .env
docker compose -p tgdoctor config --quiet
docker compose -p tgdoctor up -d --build --wait
docker compose -p tgdoctor logs --tail 50 bot
```

Bot korzysta z long pollingu: **nie potrzebuje publicznego portu, domeny,
certyfikatu TLS ani load balancera**. Potrzebuje wychodzącego HTTPS do Telegrama,
`api.dane.gov.pl` i `rejestry.ezdrowie.gov.pl`. Port API jest publikowany tylko
na `127.0.0.1:3000`; nie wystawiaj go do Internetu. `--wait` sprawdza żywotność
procesu, nie ukończenie importu. Gotowość katalogu: `/health/ready`.

Pierwszy import pobiera około 76 MB XML. Do jego zakończenia bot może zwrócić
brak wyników; `/status` pokazuje stan każdego źródła. Zapewnij wolne miejsce na
bazę, WAL, pliki tymczasowe i kopie (na początek przynajmniej 2 GB).

### Z tego Maca przez `homevm`

W tym środowisku nie ma lokalnego silnika Docker. Bind mounty muszą istnieć
na VM, więc uruchom Compose z jej katalogu. Przykład dla własnego, nieużywanego
projektu testowego:

```sh
docker context show
docker version --format '{{.Server.Version}} {{.Server.Arch}}'
rsync -a --mkpath --exclude .git --exclude .venv --exclude .env \
  --exclude '*/data/*' --exclude '*/logs/*' ./ homevm:dev/tgdoctor/
```

Utwórz `.env` **na VM** (np. przez SSH), nie umieszczaj sekretu w historii komend.
Na VM sprawdź UID właściciela `bot/data` (`1000:1000`), następnie:

```sh
ssh homevm 'cd dev/tgdoctor && docker compose -p dev-tgdoctor up -d --build --wait'
ssh -O forward -L 3000:127.0.0.1:3000 homevm
curl --fail http://localhost:3000/health/ready
```

Nie używaj nazw projektów ani sieci produkcyjnych. Po testach:

```sh
ssh -O cancel -L 3000:127.0.0.1:3000 homevm
ssh homevm 'cd dev/tgdoctor && docker compose -p dev-tgdoctor down --volumes --remove-orphans'
```

## Używanie bota

- `/start` — menu i filtry; zwykła wiadomość wyszukuje dowolną frazę.
- `/szukaj alprazolam` — nowe wyszukiwanie, zerujące wcześniejsze filtry.
- `/apteka [nazwa handlowa]` — dostępność leku w aptekach wybranej miejscowości.
- `/status` — źródła, daty, liczby rekordów i ewentualny błąd aktualizacji.
- `/prywatnosc` — zasady przetwarzania informacji.
- `/usun` — usuwa sesję i filtry z pamięci aplikacji, ale nie historię Telegrama.

Filtry menu łączą się przez AND. Wpisanie `-` usuwa wybrany filtr.
Przycisk **Wyczyść filtry** przywraca ustawienia domyślne. Filtr rodzaju może
zostać zmieniony na weterynaryjny albo usunięty. Moc jest filtrem tekstowym:
`0,5 mg` i `0.5 mg` nie są automatycznie przeliczane ani utożsamiane.

## Wyroby medyczne — rzeczywisty zakres

RPL nie jest kompletnym rejestrem wyrobów medycznych. Nie dostarczam fikcyjnego
„ogólnopolskiego API wyrobów”. Wyszukiwanie tej kategorii działa po imporcie
katalogu pozyskanego legalnie przez operatora, np. od uprawnionego dostawcy.
EUDAMED nie dowodzi sam w sobie dostępności konkretnego wyrobu w polskiej aptece.

Umieść plik w `bot/config/devices.json`, zgodny z `DeviceFeed` w
`src/tgdoctor/models.py`. Katalog `bot/config` jest celowo pusty w repozytorium:
plik dostarczasz na hoście, kontener widzi go przez read-only bind mount
(`bot/config` → `/config`) — bot odczytuje plik wyłącznie podczas importu
i nigdy tam nie zapisuje. Wymagane pola:

- katalog: `asOf` (YYYY-MM-DD), `sourceUrl` (HTTPS), `license`, `reviewedBy`, `devices`;
- każdy wyrób: `id` (tekst), `name`, `manufacturer`, `country: "PL"`,
  `polandEvidence` (opis dowodu obecności na polskim rynku), `sourceUrl` (HTTPS);
- opcjonalnie `form` i `gtins` (tablica tekstowych identyfikatorów).

```sh
docker compose -p tgdoctor exec -T bot tgdoctor import-devices --file /config/devices.json
```

Import zastępuje **wyłącznie cały katalog wyrobów**, nigdy dane RPL. Operator
odpowiada za weryfikację klasyfikacji i dowodów; samo pole `country: "PL"` nie
jest automatyczną weryfikacją. Stare dane są oznaczane jako nieaktualne.

## Konfiguracja

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | wymagana | Sekret od BotFather |
| `ALLOWED_USER_IDS` | wymagana | Lista numerycznych ID Telegram oddzielona przecinkami — **operatorzy** bota (fail-closed: pusta lista odmawia wszystkim i blokuje start). Operatorzy mogą dodawać i usuwać zwykłych użytkowników bezpośrednio z menu bota (patrz niżej) |
| `IMPORT_INTERVAL_HOURS` | `24` | Interwał importu, od 1 do 168 h |
| `STALE_AFTER_HOURS` | `72` | Próg nieaktualności kontroli i daty danych, 24–720 h |
| `ENVIRONMENT` | `production` w kontenerze | Pole środowiska w logach JSON |

Własne uruchomienia CLI mogą dodatkowo ustawiać `DATA_DIR`, `HTTP_HOST`,
`HTTP_PORT`. `.env` jest czytany przez Compose, nie automatycznie przez Python.

## Zarządzanie użytkownikami z menu bota

`ALLOWED_USER_IDS` to statyczna lista **operatorów** — jej zmiana wymaga edycji
`.env` i restartu. Operatorzy mogą natomiast **dynamicznie** dopuszczać i
usuwać zwykłych użytkowników bezpośrednio w rozmowie z botem:

1. W menu bota (widocznym tylko operatorom) wybierz **Użytkownicy**.
2. **Dodaj użytkownika** i podaj jego numeryczny identyfikator Telegram —
   np. identyfikator z komunikatu odmowy, który bot wysyła nieznajomym,
   albo z @userinfobot.
3. Dodany użytkownik uzyskuje dostęp **natychmiast**, bez restartu — członkostwo
   jest sprawdzane w SQLite przy każdej wiadomości.
4. Przycisk **Usuń <id>** wycofuje dostęp równie natychmiast.

Zwykli użytkownicy mogą korzystać z bota, ale nie widzą przycisku
Użytkownicy i nie mogą zmieniać listy. Dynamiczna lista jest przechowywana
w pliku `allowlist.sqlite3` w katalogu danych (`/data` w kontenerze) —
identyfikator, autor dodania i data; żadnych innych danych.

## API lokalne i kopie

```sh
curl 'http://localhost:3000/api/v1/medications?substance=alprazolam&atc=N05&limit=20'
curl 'http://localhost:3000/api/v1/medications?dataset=raw-materials&q=cannabis'
curl 'http://localhost:3000/api/v1/medications/100000014?dataset=medicinal-products'
docker compose -p tgdoctor exec -T bot tgdoctor backup --to /data/backups/catalog-2026-10-18.sqlite3
```

Kopia używa SQLite Backup API, jest spójna także przy aktywnym WAL i sprawdzana
przez `integrity_check`. Nazwa docelowa musi być nowa. Przenieś kopię na osobny,
szyfrowany nośnik i regularnie ćwicz odtwarzanie. Procedura w `TECHNICAL.md`.

## Prywatność i bezpieczeństwo

Bot jest **prywatny**: bez wypełnionej listy `ALLOWED_USER_IDS` nie wystartuje,
a użytkownicy spoza listy dostają wyłącznie komunikat odmowy (z własnym
identyfikatorem, aby operator mógł ich dodać — przez menu bota lub `.env`).
Obsługiwane są tylko prywatne czaty. Nie wysyłaj botowi danych pacjenta, recept,
PESEL ani dokumentacji medycznej. Telegram widzi wiadomości i metadane; boty
nie zapewniają end-to-end encryption. Aplikacja nie zapisuje zapytań ani
historii rozmów w bazie/logach — jedynym wyjątkiem jest lista dopuszczonych
użytkowników (`allowlist.sqlite3`: identyfikator Telegram, autor dodania i data).
Sesje są tylko w RAM, wygasają po 30 minutach bezczynności (sprzątanie co
minutę), limit 5000 sesji. Przed publicznym uruchomieniem operator powinien
opublikować własne dane kontaktowe, zasady prywatności i ustalić swoje
obowiązki dotyczące danych.

## Źródła i stan weryfikacji

Zweryfikowano **2026-10-18**:

- Leki: <https://api.dane.gov.pl/1.4/resources/65522>,
  <https://rejestry.ezdrowie.gov.pl/api/rpl/medicinal-products/public-pl-report/6.0.0/overall.xml>.
- Surowce: <https://api.dane.gov.pl/1.4/resources/52341>,
  <https://rejestry.ezdrowie.gov.pl/api/rpl/medicinal-products/pharmaceutical-raw-materials/get-xml>.
- Zakres EUDAMED: <https://health.ec.europa.eu/medical-devices-eudamed/overview_en>.

Rzeczywisty import na `homevm`: **22 943 leki i 656 surowców**, stan XML
2026-10-18. To liczby rekordów rejestru, nie liczby leków dostępnych w aptekach.
Testy Telegram korzystają z symulowanego API; nie wykonano połączenia z kontem
prawdziwego bota, ponieważ nie przekazano tokenu.

Rozwój, testy Linux, szczegóły architektury, backup/restore i CI: `TECHNICAL.md`.
