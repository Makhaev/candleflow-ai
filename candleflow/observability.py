"""Request IDs and structured logs.

Every request gets an ID that is returned in X-Request-ID and attached to each log line,
so a user-visible error can be matched to the server log. The access log omits query
strings because batch filters may contain supplier names (personal data).
"""

import atexit
import logging
import queue
import re
import time
from contextvars import ContextVar
from logging.handlers import QueueHandler, QueueListener
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id: ContextVar[str] = ContextVar("request_id", default="-")
SAFE_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
access = logging.getLogger("candleflow.access")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    """Request handlers only enqueue records; a background thread writes them to stderr.

    Writing to stderr synchronously on every request cost about 20% of throughput under load.
    """
    output = logging.StreamHandler()
    output.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s")
    )
    records: queue.SimpleQueue = queue.SimpleQueue()
    enqueue = QueueHandler(records)
    # The filter runs in the request's context, so the request ID is captured before queuing.
    enqueue.addFilter(RequestIdFilter())
    listener = QueueListener(records, output)
    listener.start()
    atexit.register(listener.stop)
    root = logging.getLogger()
    root.handlers[:] = [enqueue]
    root.setLevel(level)


class RequestContextMiddleware:
    """Pure ASGI middleware: unlike BaseHTTPMiddleware it does not buffer SSE streams."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        current = incoming if SAFE_ID.fullmatch(incoming) else uuid4().hex
        # Not reset afterwards: the 500 handler runs outside this middleware and must still see
        # the ID. Every request sets its own value first, so nothing leaks between requests.
        request_id.set(current)
        started = time.perf_counter()
        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [*message.get("headers", []), (b"x-request-id", current.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            access.info(
                "%s %s %s %.1fms",
                scope["method"],
                scope["path"],
                status,
                (time.perf_counter() - started) * 1000,
            )
