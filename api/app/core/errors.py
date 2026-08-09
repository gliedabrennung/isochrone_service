from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import ERROR_TYPE_BASE
from app.core.logging import get_logger, request_id_var

PROBLEM_MEDIA_TYPE = "application/problem+json"

ERROR_CATALOG: dict[str, tuple[int, str, str]] = {
    "VALIDATION_ERROR": (400, "Request validation failed", "validation-error"),
    "MALFORMED_JSON": (400, "Malformed JSON body", "malformed-json"),
    "POINT_OUT_OF_COVERAGE": (422, "Point is out of coverage", "point-out-of-coverage"),
    "POINT_NOT_ROUTABLE": (422, "Point is not routable", "point-not-routable"),
    "EMPTY_RESULT": (422, "Engine returned an empty result", "empty-result"),
    "RATE_LIMIT_EXCEEDED": (429, "Rate limit exceeded", "rate-limit-exceeded"),
    "INTERNAL_ERROR": (500, "Internal server error", "internal-error"),
    "ENGINE_UNAVAILABLE": (503, "Routing engine is unavailable", "engine-unavailable"),
    "ENGINE_TIMEOUT": (504, "Routing engine timed out", "engine-timeout"),
    "NOT_FOUND": (404, "Resource not found", "not-found"),
    "METHOD_NOT_ALLOWED": (405, "Method not allowed", "method-not-allowed"),
}

_STATUS_TO_CODE = {
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    429: "RATE_LIMIT_EXCEEDED",
}

logger = get_logger(__name__)


class ProblemError(Exception):
    def __init__(
        self,
        code: str,
        detail: str,
        *,
        status: int | None = None,
        title: str | None = None,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        default_status, default_title, slug = ERROR_CATALOG.get(
            code, (500, "Internal server error", "internal-error")
        )
        self.code = code
        self.detail = detail
        self.status = status or default_status
        self.title = title or default_title
        self.type_uri = f"{ERROR_TYPE_BASE}/{slug}"
        self.headers = headers or {}
        self.extra = extra or {}
        super().__init__(detail)


def problem_payload(
    code: str,
    detail: str,
    instance: str,
    *,
    status: int | None = None,
    title: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    default_status, default_title, slug = ERROR_CATALOG.get(
        code, (500, "Internal server error", "internal-error")
    )
    payload = {
        "type": f"{ERROR_TYPE_BASE}/{slug}",
        "title": title or default_title,
        "status": status or default_status,
        "detail": detail,
        "instance": instance,
        "request_id": request_id_var.get(),
        "code": code,
    }
    if extra:
        payload.update(extra)
    return payload


def problem_response(
    request: Request,
    code: str,
    detail: str,
    *,
    status: int | None = None,
    title: str | None = None,
    headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    payload = problem_payload(
        code, detail, request.url.path, status=status, title=title, extra=extra
    )
    response_headers = {"X-Request-Id": request_id_var.get()}
    response_headers.update(headers or {})
    return JSONResponse(
        status_code=payload["status"],
        content=payload,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=response_headers,
    )


def _format_validation_errors(errors: list[dict[str, Any]]) -> str:
    parts = []
    for error in errors:
        location = ".".join(str(item) for item in error.get("loc", ()) if item != "body")
        message = error.get("msg", "invalid value")
        parts.append(f"{location or 'body'}: {message}")
    return "; ".join(parts) or "invalid request"


async def problem_error_handler(request: Request, exc: ProblemError) -> JSONResponse:
    return problem_response(
        request,
        exc.code,
        exc.detail,
        status=exc.status,
        title=exc.title,
        headers=exc.headers,
        extra=exc.extra,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = jsonable_encoder(exc.errors())
    if any(error.get("type") == "json_invalid" for error in errors):
        return problem_response(
            request,
            "MALFORMED_JSON",
            "Request body is not valid JSON.",
        )
    return problem_response(
        request,
        "VALIDATION_ERROR",
        _format_validation_errors(errors),
        extra={"errors": errors},
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _STATUS_TO_CODE.get(exc.status_code)
    if code is None:
        code = "VALIDATION_ERROR" if exc.status_code < 500 else "INTERNAL_ERROR"
    detail = exc.detail if isinstance(exc.detail, str) else "Request could not be processed."
    return problem_response(
        request,
        code,
        detail,
        status=exc.status_code,
        headers=dict(exc.headers or {}),
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception", path=request.url.path, error=type(exc).__name__)
    return problem_response(
        request,
        "INTERNAL_ERROR",
        "Unexpected internal error. The incident is logged under the request_id.",
    )
