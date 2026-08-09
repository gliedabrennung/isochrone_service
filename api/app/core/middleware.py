import math
import time
from collections import OrderedDict

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import PROBLEM_MEDIA_TYPE, problem_payload
from app.core.ids import sanitize_request_id
from app.core.logging import get_logger, request_id_var
from app.core.metrics import RATE_LIMITED

logger = get_logger("app.request")

RATE_LIMIT_EXEMPT_PREFIXES = (
    "/api/v1/health",
    "/api/v1/ready",
    "/api/v1/openapi.json",
    "/api/v1/docs",
    "/api/v1/redoc",
    "/metrics",
)


def _client_ip(scope: Scope) -> str:
    headers = Headers(scope=scope)
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    client = scope.get("client")
    return client[0] if client else "unknown"


def _problem_response(
    scope: Scope, code: str, detail: str, headers: dict[str, str]
) -> JSONResponse:
    payload = problem_payload(code, detail, scope.get("path", "/"))
    return JSONResponse(
        status_code=payload["status"],
        content=payload,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers,
    )


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        structlog.contextvars.clear_contextvars()
        request_id = sanitize_request_id(Headers(scope=scope).get("x-request-id"))
        token = request_id_var.set(request_id)
        structlog.contextvars.bind_contextvars(request_id=request_id)

        started = time.perf_counter()
        state = {"status": 500, "started": False}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                state["started"] = True
                MutableHeaders(scope=message)["X-Request-Id"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            logger.exception("unhandled_exception", error=type(exc).__name__)
            state["status"] = 500
            if not state["started"]:
                response = _problem_response(
                    scope,
                    "INTERNAL_ERROR",
                    "Unexpected internal error. The incident is logged under the request_id.",
                    {"X-Request-Id": request_id},
                )
                await response(scope, receive, send)
        finally:
            bound = dict(structlog.contextvars.get_contextvars())
            bound.pop("request_id", None)
            logger.info(
                "http_request",
                method=scope.get("method"),
                path=scope.get("path"),
                status=state["status"],
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                client_ip=_client_ip(scope),
                **bound,
            )
            request_id_var.reset(token)
            structlog.contextvars.clear_contextvars()


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_body_size: int) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return

        detail = f"Request body exceeds the {self.max_body_size} byte limit."
        content_length = Headers(scope=scope).get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > self.max_body_size:
            response = _problem_response(scope, "VALIDATION_ERROR", detail, {})
            await response(scope, receive, send)
            return

        if content_length:
            await self.app(scope, receive, send)
            return

        buffered: list[Message] = []
        received = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] != "http.request":
                break
            received += len(message.get("body", b""))
            if received > self.max_body_size:
                logger.warning("request_body_too_large", limit=self.max_body_size)
                response = _problem_response(scope, "VALIDATION_ERROR", detail, {})
                await response(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        queue = iter(buffered)

        async def replayed_receive() -> Message:
            return next(queue, None) or await receive()

        await self.app(scope, replayed_receive, send)


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        rate_per_second: float,
        burst: int,
        max_tracked_clients: int = 20000,
    ) -> None:
        self.app = app
        self.rate_per_second = max(rate_per_second, 0.0)
        self.burst = max(burst, 1)
        self.max_tracked_clients = max_tracked_clients
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def _allow(self, client: str, now: float) -> tuple[bool, float]:
        if self.rate_per_second <= 0:
            return True, 0.0

        tokens, last_seen = self._buckets.get(client, (float(self.burst), now))
        tokens = min(float(self.burst), tokens + (now - last_seen) * self.rate_per_second)

        if tokens >= 1.0:
            self._buckets[client] = (tokens - 1.0, now)
            self._buckets.move_to_end(client)
            if len(self._buckets) > self.max_tracked_clients:
                self._buckets.popitem(last=False)
            return True, 0.0

        self._buckets[client] = (tokens, now)
        self._buckets.move_to_end(client)
        retry_after = math.ceil((1.0 - tokens) / self.rate_per_second)
        return False, max(retry_after, 1)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path.startswith(RATE_LIMIT_EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        allowed, retry_after = self._allow(_client_ip(scope), time.monotonic())
        if allowed:
            await self.app(scope, receive, send)
            return

        RATE_LIMITED.inc()
        logger.warning("rate_limit_exceeded", client_ip=_client_ip(scope), retry_after=retry_after)
        response = _problem_response(
            scope,
            "RATE_LIMIT_EXCEEDED",
            f"Rate limit of {self.rate_per_second:g} request(s) per second per IP exceeded.",
            {"Retry-After": str(int(retry_after)), "X-Request-Id": request_id_var.get()},
        )
        await response(scope, receive, send)
