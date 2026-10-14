"""The API's error type and the handlers that render every failure in the contract's shape.

Every non-2xx response looks like `{"error": {"code": "not_found", "message": "..."}}`.
"""

import logging
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm.exc import StaleDataError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

ErrorCode = Literal[
    "bad_request", "unauthorized", "forbidden", "not_found", "conflict", "unavailable", "internal"
]

_CODE_FOR_STATUS: dict[int, ErrorCode] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    503: "unavailable",
}


class ApiError(Exception):
    """An error the API reports to the client with a specific status, code and message."""

    def __init__(self, status_code: int, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def bad_request(message: str) -> ApiError:
    """400: the request is malformed or breaks a rule."""
    return ApiError(400, "bad_request", message)


def unauthorized(message: str = "A valid token is required.") -> ApiError:
    """401: no token, or a token we don't recognise."""
    return ApiError(401, "unauthorized", message)


def forbidden(message: str) -> ApiError:
    """403: the token is valid but not allowed to do this."""
    return ApiError(403, "forbidden", message)


def not_found(message: str) -> ApiError:
    """404: the thing asked for doesn't exist."""
    return ApiError(404, "not_found", message)


def conflict(message: str) -> ApiError:
    """409: the request clashes with the current state."""
    return ApiError(409, "conflict", message)


def unavailable(message: str) -> ApiError:
    """503: a feature or upstream service isn't available right now."""
    return ApiError(503, "unavailable", message)


def error_body(code: ErrorCode, message: str) -> dict[str, dict[str, str]]:
    """The JSON body for an error response."""
    return {"error": {"code": code, "message": message}}


def _describe_validation_error(exc: RequestValidationError) -> str:
    """Turn pydantic's error list into one readable sentence, e.g. `draw_watts: must be > 0`."""
    errors = exc.errors()
    if not errors:
        return "The request is not valid."
    first = errors[0]
    if first.get("type") == "json_invalid":
        return "The request body is not valid JSON."
    location = [str(part) for part in first.get("loc", ()) if part not in ("body", "query", "path")]
    message = str(first.get("msg", "is not valid"))
    return f"{'.'.join(location)}: {message}" if location else message


def install_error_handlers(app: FastAPI) -> None:
    """Register handlers so every error, expected or not, uses the contract's error shape."""

    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(error_body(exc.code, exc.message), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            error_body("bad_request", _describe_validation_error(exc)), status_code=400
        )

    @app.exception_handler(StaleDataError)
    async def handle_concurrent_change(_: Request, exc: StaleDataError) -> JSONResponse:
        # Two writers changed the same row at once (optimistic locking on outages).
        return JSONResponse(
            error_body("conflict", "This changed at the same moment. Please try again."),
            status_code=409,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _CODE_FOR_STATUS.get(exc.status_code, "bad_request")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        if exc.status_code == 404 and message == "Not Found":
            message = "No such endpoint."
        return JSONResponse(
            error_body(code, message), status_code=exc.status_code, headers=exc.headers
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=exc)
        return JSONResponse(
            error_body("internal", "Something went wrong on our side. Please try again."),
            status_code=500,
        )
