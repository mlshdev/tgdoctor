import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import override
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import ValidationError

from tgdoctor.catalog import Catalog
from tgdoctor.models import Dataset, Detail, Model, Search

LOGGER = logging.getLogger(__name__)


class ApiServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, host: str, port: int, catalog: Catalog) -> None:
        self.catalog = catalog
        super().__init__((host, port), Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "tgdoctor"
    sys_version = ""

    @override
    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10)

    @override
    def log_message(self, format: str, *args: object) -> None:
        pass

    def response(self, status: HTTPStatus, payload: Model | str) -> None:
        data = (
            payload.model_dump_json(by_alias=True).encode()
            if isinstance(payload, Model)
            else payload.encode()
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        try:
            self.route()
        except ValueError, ValidationError:
            self.response(HTTPStatus.BAD_REQUEST, '{"error":"invalid-query"}')
        except BrokenPipeError, ConnectionResetError:
            return
        except Exception as exc:
            LOGGER.error("api_request_failed", extra={"code": type(exc).__name__})
            self.response(HTTPStatus.INTERNAL_SERVER_ERROR, '{"error":"internal-error"}')

    def route(self) -> None:
        if len(self.path) > 4096:
            self.response(HTTPStatus.REQUEST_URI_TOO_LONG, '{"error":"uri-too-long"}')
            return
        parsed = urlsplit(self.path)
        if not isinstance(self.server, ApiServer):
            raise TypeError("Expected ApiServer")
        catalog = self.server.catalog
        if parsed.path == "/health/live":
            self.response(HTTPStatus.OK, '{"status":"alive"}')
            return
        if parsed.path in ("/health/ready", "/api/v1/status"):
            snapshot = catalog.snapshot()
            ready = all(not snapshot.datasets[d].stale for d in (Dataset.MEDICINAL, Dataset.RAW))
            status = (
                HTTPStatus.OK
                if ready or parsed.path == "/api/v1/status"
                else HTTPStatus.SERVICE_UNAVAILABLE
            )
            self.response(status, snapshot)
            return
        values = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=20)
        if any(len(value) != 1 for value in values.values()):
            raise ValueError("duplicate-query-parameter")
        query = {key: value[0] for key, value in values.items()}
        if parsed.path == "/api/v1/medications":
            self.response(HTTPStatus.OK, catalog.search(Search.model_validate(query)))
        elif parsed.path.startswith("/api/v1/medications/"):
            if set(query) != {"dataset"}:
                raise ValueError("dataset-required")
            identity = unquote(parsed.path.removeprefix("/api/v1/medications/"))
            if not identity or len(identity) > 80 or "/" in identity:
                raise ValueError("invalid-id")
            product = catalog.detail(Dataset(query["dataset"]), identity)
            if product:
                self.response(HTTPStatus.OK, Detail(item=product, snapshot=catalog.snapshot()))
            else:
                self.response(HTTPStatus.NOT_FOUND, '{"error":"not-found"}')
        else:
            self.response(HTTPStatus.NOT_FOUND, '{"error":"not-found"}')


def start_server(host: str, port: int, catalog: Catalog) -> tuple[ApiServer, threading.Thread]:
    server = ApiServer(host, port, catalog)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="catalog-http")
    thread.start()
    return server, thread
