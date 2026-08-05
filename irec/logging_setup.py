import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime

# Propagati dal middleware; finiscono in ogni riga di log.
correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)


def truncate_tenant(tenant_id: str | None) -> str | None:
    """Il tenant_id nei log è troncato: niente PII / identificativi completi."""
    if tenant_id is None:
        return None
    return tenant_id[:8]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if correlation_id := correlation_id_var.get():
            payload["correlation_id"] = correlation_id
        if tenant_id := tenant_id_var.get():
            payload["tenant"] = truncate_tenant(tenant_id)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
