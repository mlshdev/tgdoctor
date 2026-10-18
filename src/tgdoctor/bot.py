import asyncio
import logging
import secrets
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass, field
from time import monotonic

from pydantic import ValidationError
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
    Update,
)
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ExtBot,
    MessageHandler,
    filters,
)
from telegram.request import BaseRequest

from tgdoctor.catalog import Catalog
from tgdoctor.importer import Importer
from tgdoctor.ktomalek import (
    KtomalekClient,
    KtomalekError,
    KtomalekLocation,
    KtomalekPharmacy,
    KtomalekProduct,
)
from tgdoctor.models import Dataset, ProductSummary, Search
from tgdoctor.presentation import (
    APTEKA_PAGE,
    LABELS,
    NOTICE,
    apteka_choice_label,
    apteka_error_text,
    apteka_product_text,
    apteka_results_text,
    filters_text,
    freshness,
    product_text,
    short,
    summary_line,
)
from tgdoctor.settings import Settings
from tgdoctor.sources import SourceError

LOGGER = logging.getLogger(__name__)


class BotContext(
    CallbackContext[ExtBot[None], dict[str, object], dict[str, object], dict[str, object]]
):
    pass


type BotApplication = Application[
    ExtBot[None],
    BotContext,
    dict[str, object],
    dict[str, object],
    dict[str, object],
    None,
]

MODES: dict[str, tuple[str, str]] = {
    "q": ("Dowolna fraza", "Wpisz nazwę, substancję, ATC lub GTIN."),
    "name": ("Nazwa handlowa", "Wpisz nazwę handlową produktu."),
    "substance": ("Substancja czynna", "Wpisz substancję, np. alprazolam lub alprazolamum."),
    "atc": ("Kod ATC", "Wpisz kod lub prefiks ATC, np. N05BA12 albo N05."),
    "gtin": ("Kod GTIN / EAN", "Wpisz 8, 12, 13 lub 14 cyfr GTIN/EAN."),
    "form": ("Postać", "Wpisz postać, np. tabletki lub roztwór."),
    "strength": ("Moc", "Wpisz moc zgodnie z zapisem w rejestrze, np. 0,5 mg."),
    "holder": (
        "Podmiot / producent",
        "Wpisz nazwę podmiotu odpowiedzialnego lub producenta wyrobu.",
    ),
    "route": ("Droga podania", "Wpisz drogę podania, np. doustna."),
    "availability": (
        "Kategoria dostępności",
        "Wpisz dokładną kategorię: OTC, Rp, Rpw, Rpz lub Lz. To nie stan apteki.",
    ),
    "kind": ("Rodzaj preparatu", "Wpisz ludzki lub weterynaryjny; znak - usuwa filtr rodzaju."),
}


@dataclass
class AvailabilityFlow:
    query: str = ""
    product: KtomalekProduct | None = None
    products: list[KtomalekProduct] = field(default_factory=list)
    locations: list[KtomalekLocation] = field(default_factory=list)
    location: KtomalekLocation | None = None
    pharmacies: list[KtomalekPharmacy] = field(default_factory=list)


@dataclass
class Session:
    query: Search = field(default_factory=Search)
    awaiting: str = ""
    nonce: str = field(default_factory=lambda: secrets.token_hex(4))
    results: list[ProductSummary] = field(default_factory=list)
    detail: tuple[Dataset, str] | None = None
    apteka: AvailabilityFlow | None = None
    last_apteka: float = 0.0
    touched: float = field(default_factory=monotonic)
    last_request: float = 0


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=value) for label, value in row] for row in rows]
    )


def menu(session: Session) -> InlineKeyboardMarkup:
    rows = [
        [(MODES[key][0], f"mode:{key}") for key in keys]
        for keys in (
            ("name", "substance"),
            ("atc", "gtin"),
            ("form", "strength"),
            ("holder", "route"),
            ("availability", "kind"),
        )
    ]
    rows.extend(
        [
            [("Wybierz zbiór", "datasets"), ("Pokaż wyniki", "search")],
            [("Sprawdź w aptece", "apteka")],
            [("Także skasowane: " + ("NIE" if session.query.active_only else "TAK"), "deleted")],
            [("Wyczyść filtry", "reset"), ("Źródła i aktualność", "status")],
            [("Prywatność", "privacy")],
        ]
    )
    return keyboard(rows)


class Bot:
    def __init__(
        self,
        catalog: Catalog,
        settings: Settings,
        ktomalek: KtomalekClient | None = None,
    ) -> None:
        self.catalog = catalog
        self.settings = settings
        self.ktomalek = ktomalek if ktomalek is not None else KtomalekClient()
        self.sessions: OrderedDict[int, Session] = OrderedDict()
        self.import_task: asyncio.Task[None] | None = None
        self.stop_import = asyncio.Event()
        self.cleanup_task: asyncio.Task[None] | None = None

    async def send(
        self,
        update: Update,
        text: str,
        markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        if update.effective_message:
            await update.effective_message.reply_text(
                text,
                reply_markup=markup,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )

    async def session(self, update: Update) -> Session | None:
        user = update.effective_user
        if not user or not update.effective_chat or update.effective_chat.type != "private":
            return None
        if user.id not in self.settings.allowed_user_ids:
            await self.send(
                update,
                f"Ten bot jest prywatny. Brak uprawnień. Twój identyfikator: {user.id} — "
                "może go dodać operator do ALLOWED_USER_IDS.",
            )
            return None
        current = monotonic()
        while self.sessions:
            key, oldest = next(iter(self.sessions.items()))
            if current - oldest.touched < 1800 and len(self.sessions) < 5000:
                break
            del self.sessions[key]
        session = self.sessions.setdefault(user.id, Session())
        self.sessions.move_to_end(user.id)
        session.touched = current
        if current - session.last_request < 0.7:
            return None
        session.last_request = current
        return session

    async def start(self, update: Update, context: BotContext) -> None:
        session = await self.session(update)
        if session:
            await self.send(
                update,
                "tgdoctor — polski katalog leków, surowców i zaimportowanych wyrobów.\n"
                "Wpisz frazę lub wybierz filtr. Filtry łączą się; usuwa je „Wyczyść filtry”.\n"
                "/apteka — sprawdź, gdzie kupić lek podany nazwą handlową "
                "w wybranej miejscowości.\n"
                "Domyślnie: preparaty ludzkie i nieskasowane opakowania.\n\n"
                + NOTICE
                + "\n\n/usun usuwa sesję. /prywatnosc — zasady prywatności.",
                menu(session),
            )

    async def command_search(self, update: Update, context: BotContext) -> None:
        session = await self.session(update)
        if session:
            text = " ".join(context.args or [])
            if len(text) > 160:
                await self.send(update, "Zapytanie może mieć najwyżej 160 znaków.")
                return
            session.query = Search(q=text)
            await self.results(update, session)

    async def forget(self, update: Update, context: BotContext) -> None:
        if await self.session(update) and update.effective_user:
            self.sessions.pop(update.effective_user.id, None)
            context.application.drop_user_data(update.effective_user.id)
            context.application.drop_chat_data(update.effective_user.id)
            await self.send(
                update, "Usunięto filtry i sesję z pamięci bota. Telegram zachowuje historię czatu."
            )

    async def placeholder(self, update: Update, text: str) -> Message | None:
        message = update.effective_message
        if message is None:
            return None
        try:
            return await message.reply_text(
                text, link_preview_options=LinkPreviewOptions(is_disabled=True)
            )
        except TelegramError:
            return None

    async def finish(
        self,
        update: Update,
        placeholder: Message | None,
        text: str,
        markup: InlineKeyboardMarkup,
    ) -> None:
        if placeholder is not None:
            try:
                await placeholder.edit_text(
                    text,
                    reply_markup=markup,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
                return
            except TelegramError:
                LOGGER.warning("telegram_edit_failed")
        await self.send(update, text, markup)

    @staticmethod
    def apteka_allowed(session: Session, cooldown: float) -> bool:
        current = monotonic()
        if current - session.last_apteka < cooldown:
            return False
        session.last_apteka = current
        return True

    async def command_apteka(self, update: Update, context: BotContext) -> None:
        session = await self.session(update)
        if session is None:
            return
        session.awaiting = ""
        text = " ".join(context.args or []).strip()
        if text:
            await self.apteka_name(update, session, text)
        else:
            await self.apteka_prompt_name(update, session)

    async def apteka_prompt_name(self, update: Update, session: Session) -> None:
        session.apteka = AvailabilityFlow()
        session.awaiting = "apteka-name"
        await self.send(
            update,
            "Podaj nazwę handlową leku, np. Apap.",
            keyboard([[("Anuluj", "apteka-cancel")]]),
        )

    @staticmethod
    def apteka_city_keyboard(session: Session) -> InlineKeyboardMarkup:
        return keyboard([[("Zmień lek", "apteka"), ("Menu", "home")]])

    async def apteka_prompt_city(self, update: Update, session: Session) -> None:
        session.awaiting = "apteka-city"
        await self.send(
            update,
            "Podaj miasto lub miejscowość, np. Warszawa.",
            self.apteka_city_keyboard(session),
        )

    async def apteka_name(self, update: Update, session: Session, value: str) -> None:
        if not value or len(value) > 160:
            await self.send(update, "Wpisz nazwę od 1 do 160 znaków.")
            return
        if not self.apteka_allowed(session, 5.0):
            await self.send(update, "Sprawdzanie w aptekach jest możliwe raz na kilka sekund.")
            return
        flow = AvailabilityFlow(query=value)
        session.apteka = flow
        session.awaiting = ""
        placeholder = await self.placeholder(update, "Sprawdzam nazwę leku…")
        try:
            products = await self.ktomalek.products(value)
        except KtomalekError as exc:
            LOGGER.warning("apteka_products_failed", extra={"code": exc.code})
            await self.finish(
                update,
                placeholder,
                apteka_error_text(exc.code),
                keyboard([[("Anuluj", "apteka-cancel")]]),
            )
            return
        flow.products = products
        if not products:
            session.awaiting = "apteka-name"
            await self.finish(
                update,
                placeholder,
                "Nie znaleziono leku o takiej nazwie handlowej. Podaj inną nazwę handlową.",
                keyboard([[("Anuluj", "apteka-cancel")]]),
            )
            return
        session.nonce = secrets.token_hex(4)
        if len(products) == 1:
            flow.product = products[0]
            session.awaiting = "apteka-city"
            await self.finish(
                update,
                placeholder,
                apteka_product_text(products[0])
                + "\n\nPodaj miasto lub miejscowość, np. Warszawa.",
                self.apteka_city_keyboard(session),
            )
            return
        rows = [
            [
                (
                    short(f"{p.name} · {p.dose_line}" if p.dose_line else p.name, 60),
                    f"aprod:{session.nonce}:{index}",
                )
            ]
            for index, p in enumerate(products[:10])
        ]
        rows.append([("Anuluj", "apteka-cancel")])
        await self.finish(
            update,
            placeholder,
            f"Wybrana nazwa pasuje do {len(products)} leków. Wybierz lek:",
            keyboard(rows),
        )

    async def apteka_city(self, update: Update, session: Session, value: str) -> None:
        flow = session.apteka
        if flow is None or flow.product is None:
            await self.apteka_prompt_name(update, session)
            return
        if not value or len(value) > 160:
            await self.send(update, "Wpisz nazwę miejscowości od 1 do 160 znaków.")
            return
        placeholder = await self.placeholder(update, "Sprawdzam miejscowość…")
        try:
            locations = await self.ktomalek.locations(value)
        except KtomalekError as exc:
            LOGGER.warning("apteka_locations_failed", extra={"code": exc.code})
            session.awaiting = "apteka-city"
            await self.finish(
                update, placeholder, apteka_error_text(exc.code), self.apteka_city_keyboard(session)
            )
            return
        session.nonce = secrets.token_hex(4)
        if not locations:
            session.awaiting = "apteka-city"
            await self.finish(
                update,
                placeholder,
                "Nie znaleziono takiej miejscowości. Podaj inną miejscowość.",
                keyboard([[("Zmień lek", "apteka"), ("Menu", "home")]]),
            )
            return
        flow.locations = locations
        if len(locations) == 1:
            flow.location = locations[0]
            await self.apteka_offers(update, session, placeholder)
            return
        rows = [
            [
                (
                    short(apteka_choice_label(location), 60),
                    f"aloc:{session.nonce}:{index}",
                )
            ]
            for index, location in enumerate(locations[:10])
        ]
        rows.append([("Zmień lek", "apteka"), ("Anuluj", "apteka-cancel")])
        await self.finish(update, placeholder, "Wybierz miejscowość:", keyboard(rows))

    async def apteka_offers(
        self,
        update: Update,
        session: Session,
        placeholder: Message | None = None,
    ) -> None:
        flow = session.apteka
        if flow is None or flow.product is None or flow.location is None:
            await self.apteka_prompt_name(update, session)
            return
        session.awaiting = ""
        if placeholder is None:
            placeholder = await self.placeholder(
                update, "Sprawdzam dostępność w aptekach… Może potrwać kilka sekund."
            )
        try:
            pharmacies = await self.ktomalek.offers(flow.product, flow.location)
        except KtomalekError as exc:
            LOGGER.warning("apteka_offers_failed", extra={"code": exc.code})
            session.awaiting = "apteka-city"
            await self.finish(
                update,
                placeholder,
                apteka_error_text(exc.code),
                self.apteka_city_keyboard(session),
            )
            return
        flow.pharmacies = pharmacies
        session.nonce = secrets.token_hex(4)
        await self.finish(
            update,
            placeholder,
            apteka_results_text(flow.product, flow.location, pharmacies, 0),
            self.apteka_results_keyboard(session, 0),
        )

    def apteka_results_keyboard(self, session: Session, offset: int) -> InlineKeyboardMarkup:
        flow = session.apteka
        pharmacies = flow.pharmacies if flow else []
        rows: list[list[tuple[str, str]]] = []
        navigation: list[tuple[str, str]] = []
        if offset:
            navigation.append(
                ("← Poprzednie", f"apage:{session.nonce}:{max(0, offset - APTEKA_PAGE)}")
            )
        if offset + APTEKA_PAGE < len(pharmacies):
            navigation.append(("Następne →", f"apage:{session.nonce}:{offset + APTEKA_PAGE}"))
        if navigation:
            rows.append(navigation)
        rows.append([("Zmień miasto", "apteka-city"), ("Nowy lek", "apteka")])
        rows.append([("Menu", "home")])
        return keyboard(rows)

    async def privacy(self, update: Update, context: BotContext) -> None:
        if await self.session(update):
            await self.send(update, self.privacy_text())

    @staticmethod
    def privacy_text() -> str:
        return (
            "Nie wysyłaj recept, PESEL, objawów ani dokumentacji medycznej.\n"
            "Funkcja /apteka wysyła podaną nazwę handlową leku i miejscowość "
            "do zewnętrznego serwisu ktomalek.pl; nie wysyłaj więcej niż jest potrzebne "
            "do sprawdzenia dostępności i nie podawaj adresu zamieszkania.\n"
            "Telegram przetwarza wiadomości zgodnie ze swoją polityką; rozmowa z botem nie jest "
            "szyfrowana end-to-end. Operator bota otrzymuje zapytania i identyfikator Telegram.\n"
            "Sesje i filtry są tylko w RAM, wygasają po 30 minutach bezczynności; "
            "limit 5000 sesji. Sprzątanie pamięci co minutę. "
            "Brak zapisu historii zapytań i identyfikatorów w bazie lub logach aplikacji. "
            "/usun — usuń sesję."
        )

    async def text(self, update: Update, context: BotContext) -> None:
        session = await self.session(update)
        if (
            session is None
            or update.effective_message is None
            or update.effective_message.text is None
        ):
            return
        value = update.effective_message.text.strip()
        if not value or len(value) > 160:
            await self.send(update, "Wpisz od 1 do 160 znaków.")
            return
        mode = session.awaiting or "q"
        if mode == "apteka-name":
            await self.apteka_name(update, session, value)
            return
        if mode == "apteka-city":
            await self.apteka_city(update, session, value)
            return
        try:
            data = session.query.model_dump()
            data[mode] = "" if value == "-" else value
            data["offset"] = 0
            session.query = Search.model_validate(data)
            session.awaiting = ""
            await self.results(update, session)
        except ValidationError:
            await self.send(update, "Nieprawidłowy filtr. " + MODES[mode][1])

    async def results(self, update: Update, session: Session, offset: int = 0) -> None:
        session.query = session.query.model_copy(update={"offset": offset})
        found = await asyncio.to_thread(self.catalog.search, session.query)
        session.nonce = secrets.token_hex(4)
        session.results = found.items
        session.detail = None
        rows = [
            [(short(summary_line(product), 60), f"item:{session.nonce}:{index}")]
            for index, product in enumerate(found.items)
        ]
        navigation: list[tuple[str, str]] = []
        if offset:
            navigation.append(
                ("← Poprzednie", f"page:{session.nonce}:{max(0, offset - found.limit)}")
            )
        if offset + found.limit < found.total and offset + found.limit <= 10000:
            navigation.append(("Następne →", f"page:{session.nonce}:{offset + found.limit}"))
        if navigation:
            rows.append(navigation)
        rows.append([("Filtry / menu", "home"), ("Wyczyść", "reset")])
        datasets = {product.dataset for product in found.items}
        if not datasets:
            datasets = (
                {session.query.dataset} if session.query.dataset is not None else set(Dataset)
            )
        message = (
            f"Wyniki: {found.total}. Strona {offset // found.limit + 1}.\n"
            + filters_text(session.query)
            + "\n\n"
            + (
                "\n".join(f"{index + 1}. {summary_line(p)}" for index, p in enumerate(found.items))
                if found.items
                else "Brak wyników. Spróbuj usunąć część filtrów. Moc jest wyszukiwana tekstowo."
            )
            + "\n\n"
            + freshness(found.snapshot, datasets)
            + "\n"
            + NOTICE
        )
        await self.send(update, short(message, 3900), keyboard(rows))

    async def detail(self, update: Update, session: Session, offset: int = 0) -> None:
        if not session.detail:
            return
        dataset, identity = session.detail
        product = await asyncio.to_thread(self.catalog.detail, dataset, identity)
        if product is None:
            await self.send(update, "Produkt zniknął po aktualizacji. Wyszukaj ponownie.")
            return
        snapshot = await asyncio.to_thread(self.catalog.snapshot)
        rows: list[list[InlineKeyboardButton]] = []
        for name, url in product.documents.items():
            rows.append([InlineKeyboardButton("Ulotka" if name == "ulotka" else "ChPL", url=url)])
        if product.dataset == Dataset.DEVICES and product.source_url:
            rows.append([InlineKeyboardButton("Źródło danych wyrobu", url=product.source_url)])
        navigation: list[InlineKeyboardButton] = []
        if offset:
            navigation.append(
                InlineKeyboardButton(
                    "← Opakowania", callback_data=f"packs:{session.nonce}:{max(0, offset - 5)}"
                )
            )
        if offset + 5 < len(product.packages):
            navigation.append(
                InlineKeyboardButton(
                    "Opakowania →", callback_data=f"packs:{session.nonce}:{offset + 5}"
                )
            )
        if navigation:
            rows.append(navigation)
        rows.append(
            [
                InlineKeyboardButton("Wyniki", callback_data="search"),
                InlineKeyboardButton("Menu", callback_data="home"),
            ]
        )
        await self.send(update, product_text(product, snapshot, offset), InlineKeyboardMarkup(rows))

    async def callbacks(self, update: Update, context: BotContext) -> None:
        callback = update.callback_query
        if not callback:
            return
        await callback.answer()
        session = await self.session(update)
        if not session or not isinstance(callback.data, str):
            return
        action, _, value = callback.data.partition(":")
        if action in ("page", "item", "packs"):
            nonce, _, index = value.partition(":")
            if nonce != session.nonce or not index.isdigit():
                await self.send(update, "Ten przycisk wygasł. Użyj bieżącego menu.", menu(session))
                return
            position = int(index)
            if action == "page" and position <= 10000:
                await self.results(update, session, position)
            elif action == "item" and position < len(session.results):
                product = session.results[position]
                session.detail = (product.dataset, product.id)
                await self.detail(update, session)
            elif action == "packs" and position <= 10000:
                await self.detail(update, session, position)
        elif action == "apteka":
            await self.apteka_prompt_name(update, session)
        elif action == "apteka-cancel":
            session.apteka = None
            session.awaiting = ""
            await self.send(update, "Przerwano sprawdzanie w aptekach.", menu(session))
        elif action == "apteka-city":
            flow = session.apteka
            if flow is None or flow.product is None:
                await self.apteka_prompt_name(update, session)
                return
            await self.apteka_prompt_city(update, session)
        elif action == "aprod":
            nonce, _, index = value.partition(":")
            flow = session.apteka
            if (
                nonce != session.nonce
                or not index.isdigit()
                or flow is None
                or int(index) >= len(flow.products)
            ):
                await self.send(update, "Ten przycisk wygasł. Użyj bieżącego menu.", menu(session))
                return
            flow.product = flow.products[int(index)]
            await self.apteka_prompt_city(update, session)
        elif action == "aloc":
            nonce, _, index = value.partition(":")
            flow = session.apteka
            if (
                nonce != session.nonce
                or not index.isdigit()
                or flow is None
                or int(index) >= len(flow.locations)
            ):
                await self.send(update, "Ten przycisk wygasł. Użyj bieżącego menu.", menu(session))
                return
            flow.location = flow.locations[int(index)]
            await self.apteka_offers(update, session)
        elif action == "apage":
            nonce, _, index = value.partition(":")
            flow = session.apteka
            if (
                nonce != session.nonce
                or not index.isdigit()
                or flow is None
                or not flow.pharmacies
                or flow.product is None
                or flow.location is None
                or int(index) >= 10000
            ):
                await self.send(update, "Ten przycisk wygasł. Użyj bieżącego menu.", menu(session))
                return
            await self.finish(
                update,
                None,
                apteka_results_text(flow.product, flow.location, flow.pharmacies, int(index)),
                self.apteka_results_keyboard(session, int(index)),
            )
        elif action == "mode" and value in MODES:
            session.awaiting = value
            await self.send(update, MODES[value][1] + "\nZnak - usuwa ten filtr.")
        elif action == "datasets":
            await self.send(
                update,
                "Wybierz zbiór (importy i daty są niezależne):",
                keyboard(
                    [[(LABELS[d], f"dataset:{d.value}")] for d in Dataset]
                    + [[("Wszystkie", "dataset:all")]]
                ),
            )
        elif action == "dataset" and value in ("all", *(d.value for d in Dataset)):
            session.query = session.query.model_copy(
                update={
                    "dataset": None if value == "all" else Dataset(value),
                    "offset": 0,
                }
            )
            await self.results(update, session)
        elif action in ("home", "reset", "deleted"):
            session.awaiting = ""
            if action == "reset":
                session.query = Search()
                session.nonce = secrets.token_hex(4)
                session.results = []
                session.detail = None
            elif action == "deleted":
                session.query = session.query.model_copy(
                    update={"active_only": not session.query.active_only}
                )
            await self.send(
                update, "Aktywne filtry:\n" + filters_text(session.query), menu(session)
            )
        elif action == "search":
            await self.results(update, session, session.query.offset)
        elif action == "status":
            await self.status(update, context)
        elif action == "privacy":
            await self.send(update, self.privacy_text())

    async def status(self, update: Update, context: BotContext) -> None:
        # Callback handlers have already authenticated and rate-limited the user.
        if not update.callback_query and not await self.session(update):
            return
        snapshot = await asyncio.to_thread(self.catalog.snapshot)
        lines = [freshness(snapshot, set(Dataset))]
        for dataset, status in snapshot.datasets.items():
            if status.snapshot:
                source = status.snapshot
                lines.append(
                    f"\n{LABELS[dataset]}: {source.product_count} rekordów, "
                    f"sprawdzono {source.checked_at.isoformat()}\n{source.source_url}"
                )
            if status.last_attempt is not None and status.last_attempt.error_code is not None:
                lines.append("Ostatni import nieudany: " + status.last_attempt.error_code)
        lines.append(
            "\nBrak pełnego katalogu wyrobów. Rejestry nie są wykazem stanów aptek, "
            "wycofań serii ani rekomendacji terapeutycznych."
        )
        await self.send(update, short("\n".join(lines), 3900))

    async def errors(self, update: object, context: BotContext) -> None:
        LOGGER.error("telegram_handler_failed", extra={"code": type(context.error).__name__})
        if not isinstance(update, Update):
            return
        user = update.effective_user
        if user is not None and user.id not in self.settings.allowed_user_ids:
            return
        try:
            await self.send(update, "Wystąpił błąd. Spróbuj ponownie lub użyj /start.")
        except TelegramError:
            LOGGER.warning("telegram_error_reply_failed")

    async def refresh(self) -> None:
        while not self.stop_import.is_set():
            try:
                await asyncio.to_thread(Importer(self.catalog).run)
            except SourceError as exc:
                LOGGER.warning("import_skipped", extra={"code": exc.code})
            except Exception as exc:
                LOGGER.error("import_cycle_failed", extra={"code": type(exc).__name__})
            with suppress(TimeoutError):
                await asyncio.wait_for(self.stop_import.wait(), self.settings.interval_hours * 3600)

    async def cleanup(self, application: BotApplication) -> None:
        while not self.stop_import.is_set():
            expired = [
                key for key, value in self.sessions.items() if monotonic() - value.touched >= 1800
            ]
            for key in expired:
                del self.sessions[key]
            for key in list(application.user_data):
                if key not in self.sessions:
                    application.drop_user_data(key)
            for key in list(application.chat_data):
                if key not in self.sessions:
                    application.drop_chat_data(key)
            with suppress(TimeoutError):
                await asyncio.wait_for(self.stop_import.wait(), 60)

    async def post_init(self, application: BotApplication) -> None:
        await application.bot.set_my_commands(
            [
                ("start", "Menu i filtry"),
                ("szukaj", "Nowe wyszukiwanie"),
                ("apteka", "Sprawdź lek w aptekach"),
                ("status", "Źródła i aktualność"),
                ("prywatnosc", "Zasady prywatności"),
                ("usun", "Usuń sesję i filtry"),
            ]
        )
        self.import_task = asyncio.create_task(self.refresh(), name="rpl-importer")
        self.cleanup_task = asyncio.create_task(self.cleanup(application), name="session-cleanup")

    async def post_stop(self, application: BotApplication) -> None:
        self.stop_import.set()
        if self.import_task:
            await self.import_task
        if self.cleanup_task:
            await self.cleanup_task
        self.sessions.clear()

    def application(self, request: BaseRequest | None = None) -> BotApplication:
        context_types = ContextTypes(
            context=BotContext,
            user_data=dict[str, object],
            chat_data=dict[str, object],
            bot_data=dict[str, object],
        )
        builder = (
            ApplicationBuilder()
            .token(self.settings.token)
            .concurrent_updates(False)
            .context_types(context_types)
            .job_queue(None)
            .post_init(self.post_init)
            .post_stop(self.post_stop)
        )
        if request is not None:
            builder = builder.request(request)
        application = builder.build()
        application.add_handler(CommandHandler(["start", "help", "menu"], self.start))
        application.add_handler(CommandHandler("szukaj", self.command_search))
        application.add_handler(CommandHandler("status", self.status))
        application.add_handler(CommandHandler("prywatnosc", self.privacy))
        application.add_handler(CommandHandler("usun", self.forget))
        application.add_handler(CommandHandler("apteka", self.command_apteka))
        application.add_handler(CallbackQueryHandler(self.callbacks))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.text))
        application.add_error_handler(self.errors)
        return application
