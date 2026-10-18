from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from urllib.parse import parse_qs

import httpx
import pytest
from telegram import CallbackQuery, Chat, Message, MessageEntity, Update, User
from telegram.request import HTTPXRequest

from tests.conftest import LOCATIONS_PAYLOAD, OFFERS_FRAGMENT, PRODUCTS_FRAGMENT
from tgdoctor.bot import AvailabilityFlow, Bot, BotContext, Session, keyboard, menu
from tgdoctor.catalog import Catalog
from tgdoctor.importer import Importer
from tgdoctor.ktomalek import KtomalekClient, KtomalekProduct
from tgdoctor.models import Search
from tgdoctor.settings import Settings


def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        token="123456:TEST_TOKEN_NOT_REAL",
        interval_hours=24,
        stale_hours=72,
        allowed_user_ids=frozenset({42}),
        http_host="127.0.0.1",
        http_port=3000,
        environment="test",
    )


def test_callback_size() -> None:
    session = Session()
    flow = AvailabilityFlow()
    flow.products = [
        KtomalekProduct(
            bloz="1",
            group_id="2",
            search_input="a",
            report_id="3",
            name="Lek",
            dose_line="500 mg",
            info="",
        )
    ]
    session.apteka = flow
    session.nonce = "12345678"
    instance = Bot(Catalog(Path("callback-size.sqlite3")), settings(Path("callback-size")))
    markups = (
        menu(session),
        keyboard([[("Produkt", "item:12345678:0")]]),
        keyboard([[("Lek", "aprod:12345678:12")]]),
        keyboard([[("Lokalizacja", "aloc:12345678:12")]]),
        instance.apteka_results_keyboard(session, 99999),
    )
    for markup in markups:
        for row in markup.inline_keyboard:
            for button in row:
                assert isinstance(button.callback_data, str)
                assert len(button.callback_data.encode()) <= 64


@pytest.mark.asyncio
async def test_real_dispatch_menu_search_detail(
    populated: Catalog,
    tmp_path: Path,
) -> None:
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/getMe"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "id": 123456,
                        "is_bot": True,
                        "first_name": "TestBot",
                        "username": "test_bot",
                    },
                },
            )
        if request.url.path.endswith("/answerCallbackQuery"):
            return httpx.Response(200, json={"ok": True, "result": True})
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "message_id": 1,
                    "date": 1,
                    "chat": {"id": 42, "type": "private"},
                    "text": "ok",
                },
            },
        )

    request = HTTPXRequest(httpx_kwargs={"transport": httpx.MockTransport(respond)})
    bot = Bot(populated, settings(tmp_path))
    application = bot.application(request)
    user = User(id=42, first_name="Test", is_bot=False)
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(42, "private"),
        from_user=user,
        text="/start",
        entities=[MessageEntity("bot_command", 0, 6)],
    )
    message.set_bot(application.bot)
    async with application:
        await application.process_update(Update(1, message=message))
        assert 42 in bot.sessions
        assert any("Menu" in r.content.decode() or "tgdoctor" in r.content.decode() for r in calls)
        session = bot.sessions[42]
        session.last_request = 0
        text = Message(
            message_id=2,
            date=datetime.now(UTC),
            chat=Chat(42, "private"),
            from_user=user,
            text="alprazolam",
        )
        text.set_bot(application.bot)
        await application.process_update(Update(2, message=text))
        assert len(session.results) == 1
        assert session.query.q == "alprazolam"

        async def click(data: str) -> None:
            session.last_request = 0
            callback = CallbackQuery("test-query", user, "instance", message=message, data=data)
            callback.set_bot(application.bot)
            await application.process_update(Update(3, callback_query=callback))

        await click(f"item:{session.nonce}:0")
        assert session.detail is not None
        bodies = [
            parse_qs(r.content.decode()) for r in calls if r.url.path.endswith("/sendMessage")
        ]
        assert any("GTIN" in body.get("text", [""])[0] for body in bodies)
        replies_before = sum(r.url.path.endswith("/sendMessage") for r in calls)
        await click("pharmacies")
        assert sum(r.url.path.endswith("/sendMessage") for r in calls) == replies_before
        assert all(r.url.host == "api.telegram.org" for r in calls)
        await click("item:obsolete:0")
        assert "wygas" in calls[-1].content.decode()


@pytest.mark.asyncio
async def test_real_dispatch_apteka_flow(
    populated: Catalog,
    tmp_path: Path,
) -> None:
    telegram: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        telegram.append(request)
        if request.url.path.endswith("/getMe"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "id": 123456,
                        "is_bot": True,
                        "first_name": "TestBot",
                        "username": "test_bot",
                    },
                },
            )
        if request.url.path.endswith("/answerCallbackQuery"):
            return httpx.Response(200, json={"ok": True, "result": True})
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "message_id": 1,
                    "date": 1,
                    "chat": {"id": 42, "type": "private"},
                    "text": "ok",
                },
            },
        )

    upstream: list[str] = []

    def ktomalek_respond(request: httpx.Request) -> httpx.Response:
        upstream.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"set-cookie": "JSESSIONID=abc123.t_leon20_1; path=/"},
            )
        if request.url.path == "/leon-www/produkty/szukajAjax":
            return httpx.Response(200, text=PRODUCTS_FRAGMENT)
        if request.url.path == "/leon-www/localization":
            return httpx.Response(200, text=LOCATIONS_PAYLOAD)
        if request.url.path == "/leon-www/serwispacjenta/cart/add":
            return httpx.Response(200, json={"type": "SUCCESS", "message": "Produkt dodany"})
        return httpx.Response(200, text=OFFERS_FRAGMENT)

    request = HTTPXRequest(httpx_kwargs={"transport": httpx.MockTransport(respond)})
    client = KtomalekClient(transport=httpx.MockTransport(ktomalek_respond))
    bot = Bot(populated, settings(tmp_path), client)
    application = bot.application(request)
    user = User(id=42, first_name="Test", is_bot=False)
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(42, "private"),
        from_user=user,
        text="/apteka",
        entities=[MessageEntity("bot_command", 0, 7)],
    )
    message.set_bot(application.bot)

    def texts() -> list[str]:
        return [
            (parse_qs(r.content.decode()).get("text", [""])[0] if r.content else "")
            for r in telegram
            if r.url.path.endswith(("/sendMessage", "/editMessageText"))
        ]

    async with application:
        await application.process_update(Update(1, message=message))
        session = bot.sessions[42]
        assert session.awaiting == "apteka-name"
        session.last_request = 0
        name = Message(
            message_id=2,
            date=datetime.now(UTC),
            chat=Chat(42, "private"),
            from_user=user,
            text="apap",
        )
        name.set_bot(application.bot)
        await application.process_update(Update(2, message=name))
        assert session.apteka is not None
        assert len(session.apteka.products) == 2
        session.last_request = 0

        async def click(data: str) -> None:
            session.last_request = 0
            callback = CallbackQuery("test-query", user, "instance", message=name, data=data)
            callback.set_bot(application.bot)
            await application.process_update(Update(3, callback_query=callback))

        await click(f"aprod:{session.nonce}:0")
        assert session.apteka is not None
        assert session.apteka.product is not None
        assert session.apteka.product.name == "Apap"
        assert session.awaiting == "apteka-city"
        session.last_request = 0
        city = Message(
            message_id=3,
            date=datetime.now(UTC),
            chat=Chat(42, "private"),
            from_user=user,
            text="Warszawa",
        )
        city.set_bot(application.bot)
        await application.process_update(Update(4, message=city))
        assert session.apteka is not None
        assert [p.id for p in session.apteka.pharmacies] == [877828, 877829]
        assert any("APTEKA WILANOWSKA" in text for text in texts())
        assert any("8,5 km" in text for text in texts())
        assert any("ostatnie sztuki" in text for text in texts())
        assert any("Warszawa" in text for text in texts())
        assert any("dostępny" in text for text in texts())
        session.last_request = 0
        await click(f"apage:{session.nonce}:0")
        assert upstream[-5:] == [
            "/",
            "/leon-www/produkty/szukajAjax",
            "/leon-www/serwispacjenta/cart/add",
            "/leon-www/localization",
            "/leon-www/oferty/KLEK/szukaj/ajax",
        ]


@pytest.mark.asyncio
async def test_private_chat_access_and_rate_limit(populated: Catalog, tmp_path: Path) -> None:
    bot = Bot(populated, settings(tmp_path))
    user = User(id=42, first_name="Test", is_bot=False)
    message = Message(
        message_id=1, date=datetime.now(UTC), chat=Chat(42, "private"), from_user=user
    )
    update = Update(1, message=message)
    assert await bot.session(update) is not None
    assert await bot.session(update) is None
    group = Message(message_id=2, date=datetime.now(UTC), chat=Chat(-42, "group"), from_user=user)
    assert await bot.session(Update(2, message=group)) is None


def test_reset_defaults() -> None:
    assert Search().kind == "ludzki"
    assert Search().active_only


def test_menu_apteka_button_without_external_links() -> None:
    markup = menu(Session())
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert all(button.url is None for button in buttons)
    assert any(button.callback_data == "apteka" for button in buttons)
    assert any("aptece" in button.text.casefold() for button in buttons)
    assert all(
        "ktomalek" not in (button.text + (button.url or "")).casefold() for button in buttons
    )


@pytest.mark.asyncio
async def test_background_import_lifecycle(
    populated: Catalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    imported = Event()

    def run(self: Importer) -> bool:
        imported.set()
        return True

    monkeypatch.setattr(Importer, "run", run)

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getMe"):
            result: object = {"id": 123456, "is_bot": True, "first_name": "Test"}
        else:
            result = True
        return httpx.Response(200, json={"ok": True, "result": result})

    bot = Bot(populated, settings(tmp_path))
    application = bot.application(
        HTTPXRequest(httpx_kwargs={"transport": httpx.MockTransport(respond)})
    )
    async with application:
        await bot.post_init(application)
        assert await asyncio.to_thread(imported.wait, 5)
        await bot.post_stop(application)
        assert bot.import_task is not None and bot.import_task.done()
        assert bot.cleanup_task is not None and bot.cleanup_task.done()


@pytest.mark.asyncio
async def test_allowlist_rejects_unknown_user(
    populated: Catalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    replies: list[str] = []
    bot = Bot(populated, replace(settings(tmp_path), allowed_user_ids=frozenset({1})))

    async def send(
        update: Update,
        text: str,
        markup: object = None,
    ) -> None:
        replies.append(text)

    monkeypatch.setattr(bot, "send", send)
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(42, "private"),
        from_user=User(42, "Test", is_bot=False),
    )
    assert await bot.session(Update(1, message=message)) is None
    assert not bot.sessions
    assert replies == [
        "Ten bot jest prywatny. Brak uprawnień. Twój identyfikator: 42 — "
        "może go dodać operator do ALLOWED_USER_IDS."
    ]


@pytest.mark.asyncio
async def test_allowlist_empty_denies_everyone(
    populated: Catalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    replies: list[str] = []
    bot = Bot(populated, replace(settings(tmp_path), allowed_user_ids=frozenset()))

    async def send(
        update: Update,
        text: str,
        markup: object = None,
    ) -> None:
        replies.append(text)

    monkeypatch.setattr(bot, "send", send)
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(42, "private"),
        from_user=User(42, "Test", is_bot=False),
    )
    assert await bot.session(Update(1, message=message)) is None
    assert not bot.sessions
    assert len(replies) == 1
    assert "Brak uprawnień" in replies[0]


@pytest.mark.asyncio
async def test_allowlist_admits_listed_user(
    populated: Catalog,
    tmp_path: Path,
) -> None:
    bot = Bot(populated, settings(tmp_path))
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(42, "private"),
        from_user=User(42, "Test", is_bot=False),
    )
    session = await bot.session(Update(1, message=message))
    assert session is not None
    assert 42 in bot.sessions


@pytest.mark.asyncio
async def test_errors_handler_skips_denied_user(
    populated: Catalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    def respond(request: httpx.Request) -> httpx.Response:
        result: object = (
            {"id": 123456, "is_bot": True, "first_name": "Test"}
            if request.url.path.endswith("/getMe")
            else True
        )
        return httpx.Response(200, json={"ok": True, "result": result})

    bot = Bot(populated, replace(settings(tmp_path), allowed_user_ids=frozenset({1})))
    application = bot.application(
        HTTPXRequest(httpx_kwargs={"transport": httpx.MockTransport(respond)})
    )
    replies: list[str] = []

    async def send(
        update: Update,
        text: str,
        markup: object = None,
    ) -> None:
        replies.append(text)

    monkeypatch.setattr(bot, "send", send)
    denied = Update(
        1,
        message=Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(42, "private"),
            from_user=User(42, "Test", is_bot=False),
        ),
    )
    await bot.errors(denied, BotContext.from_error(denied, RuntimeError("boom"), application))
    assert replies == []
    allowed = Update(
        2,
        message=Message(
            message_id=2,
            date=datetime.now(UTC),
            chat=Chat(1, "private"),
            from_user=User(1, "Owner", is_bot=False),
        ),
    )
    await bot.errors(allowed, BotContext.from_error(allowed, RuntimeError("boom"), application))
    assert replies == ["Wystąpił błąd. Spróbuj ponownie lub użyj /start."]
