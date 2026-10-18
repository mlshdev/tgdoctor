import argparse
import logging
import os
import platform
import signal
import sys
import threading
from pathlib import Path

import httpx
from telegram.error import TelegramError

from tgdoctor.bot import Bot
from tgdoctor.catalog import Catalog
from tgdoctor.httpserver import start_server
from tgdoctor.importer import Importer, exclusive, import_devices
from tgdoctor.logging import configure
from tgdoctor.settings import Settings
from tgdoctor.sources import SourceError

LOGGER = logging.getLogger(__name__)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="tgdoctor")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="Bot Telegram + importer + lokalne API")
    commands.add_parser("serve", help="Tylko lokalne API; bez bota i automatycznego importu")
    commands.add_parser("import", help="Import obu oficjalnych rejestrów")
    devices = commands.add_parser(
        "import-devices", help="Import zweryfikowanego katalogu wyrobów JSON"
    )
    devices.add_argument("--file", type=Path, required=True)
    backup = commands.add_parser("backup", help="Spójna kopia SQLite (także w trybie WAL)")
    backup.add_argument("--to", type=Path, required=True)
    commands.add_parser("healthcheck", help="Sprawdzenie lokalnego procesu HTTP")
    commands.add_parser("status", help="Daty, źródła, liczniki i błędy importu")
    return root


def main() -> None:
    args = parser().parse_args()
    try:
        settings = Settings.load()
        configure(settings.environment, settings.token)
        if os.environ.get("TGDOCTOR_CONTAINER") == "1" and (
            sys.platform != "linux" or platform.machine() != "x86_64"
        ):
            raise ValueError("Kontener obsługuje wyłącznie linux/amd64")
        if args.command == "healthcheck":
            host = "127.0.0.1" if settings.http_host == "0.0.0.0" else settings.http_host
            with httpx.Client(timeout=5, trust_env=False) as client:
                client.get(f"http://{host}:{settings.http_port}/health/live").raise_for_status()
            return
        catalog = Catalog(settings.data_dir / "catalog.sqlite3", settings.stale_hours)
        catalog.initialize()
        if args.command == "import":
            if not Importer(catalog).run():
                raise SourceError("one-or-more-imports-failed")
        elif args.command == "import-devices":
            import_devices(catalog, args.file)
        elif args.command == "backup":
            catalog.backup(args.to)
        elif args.command == "status":
            print(catalog.snapshot().model_dump_json(by_alias=True, indent=2))
        elif args.command == "serve":
            serve(catalog, settings)
        elif args.command == "run":
            if not settings.token or settings.token.startswith("replace-"):
                raise ValueError("Ustaw TELEGRAM_BOT_TOKEN otrzymany od @BotFather")
            with exclusive(settings.data_dir / "bot.lock"):
                server, thread = start_server(settings.http_host, settings.http_port, catalog)
                try:
                    Bot(catalog, settings).application().run_polling(
                        allowed_updates=["message", "callback_query"],
                        drop_pending_updates=False,
                    )
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()
    except (SourceError, ValueError, OSError, httpx.HTTPError, TelegramError) as exc:
        LOGGER.error(
            "command_failed",
            extra={
                "code": exc.code if isinstance(exc, SourceError) else type(exc).__name__,
            },
        )
        if isinstance(exc, ValueError):
            print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None


def serve(catalog: Catalog, settings: Settings) -> None:
    stopping = threading.Event()

    def stop(signum: int, frame: object) -> None:
        stopping.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    server, thread = start_server(settings.http_host, settings.http_port, catalog)
    try:
        stopping.wait()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
