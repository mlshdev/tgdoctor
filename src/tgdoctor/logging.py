import json
import logging
from datetime import UTC, datetime
from typing import override


class JsonFormatter(logging.Formatter):
    def __init__(self, environment: str, token: str) -> None:
        super().__init__()
        self.environment = environment
        self.token = token

    @override
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if self.token:
            message = message.replace(self.token, "[redacted]")
        payload: dict[str, str] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "severity": record.levelname,
            "message": message,
            "service": "tgdoctor",
            "environment": self.environment,
        }
        for key in ("dataset", "code"):
            value: object = getattr(record, key, None)
            if isinstance(value, str):
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


def configure(environment: str, token: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(environment, token))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    for name in ("httpx", "httpcore", "telegram", "apscheduler"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
