"""Uniform HTTP response envelope.

Every JSON response from the API has the same shape:

Success::

    {"success": true, "message": "Upload confirmed", "data": {...}}

Error::

    {"success": false, "error": {"code": 404, "message": "...", "details": null}}

Handlers return :func:`ok` together with ``response_model=SuccessEnvelope[...]``;
exceptions are converted by the handlers registered in ``app.main``.
``204 No Content`` responses stay bodyless — HTTP semantics, and the only
documented exception to the envelope.
"""

from http import HTTPStatus
from typing import Any, Generic, TypeVar

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel

T = TypeVar("T")


class ErrorBody(BaseModel):
    code: int
    message: str
    details: Any | None = None


class EnvelopeError(BaseModel):
    """Error body as returned by the API — usable as an OpenAPI response model."""

    success: bool = False
    error: ErrorBody


class SuccessEnvelope(BaseModel, Generic[T]):
    success: bool = True
    message: str = "OK"
    data: T | None = None


def ok(data: Any = None, message: str = "OK") -> dict[str, Any]:
    """Build a success envelope body for a route handler to return."""
    return {"success": True, "message": message, "data": data}


def _phrase(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Error"


def error_json(
    status_code: int, message: str, details: Any | None = None
) -> JSONResponse:
    """Build the uniform error response."""
    payload = {
        "success": False,
        "error": {
            "code": status_code,
            "message": message,
            "details": jsonable_encoder(details) if details is not None else None,
        },
    }
    return JSONResponse(status_code=status_code, content=payload)


def http_exception_response(exc: Any) -> JSONResponse:
    """Convert Starlette/FastAPI ``HTTPException`` to the uniform envelope."""
    detail = exc.detail
    if isinstance(detail, str):
        message, details = detail, None
    else:
        # Structured details (e.g. framework-generated): keep the phrase as the
        # human-readable message and pass the structure through untouched.
        message, details = _phrase(exc.status_code), detail
    response = error_json(exc.status_code, message, details)
    headers = getattr(exc, "headers", None)
    if headers:
        response.headers.update(headers)
    return response


def error_responses(*specs: tuple[int, str]) -> dict[int, dict[str, Any]]:
    """Build OpenAPI ``responses=`` entries all backed by ``EnvelopeError``.

    Usage::

        @router.get("", responses=COMMON_ERRORS | error_responses(
            (401, "Not authenticated"),
            (404, "Document not found"),
        ))
    """
    return {
        code: {"model": EnvelopeError, "description": description}
        for code, description in specs
    }


# Possible on every /api/v1 route: the global rate limit, request validation,
# and unexpected crashes.
COMMON_ERRORS = error_responses(
    (422, "Request validation failed"),
    (429, "Rate limit exceeded"),
    (500, "Internal server error"),
)
