"""AI device helper: identify a device from its name or a photo of its label.

Anyone can call this endpoint, and the whole app (alarms included) runs on one event loop, so
nothing here may do unbounded work: bodies are cut off at a size limit as they arrive, and the
name is held to the same 500 characters whether it comes as JSON or as a form field.
"""

from typing import Any

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.datastructures import UploadFile
from starlette.types import Message

from app.ai import MAX_IMAGE_BYTES
from app.api.deps import ContextDep
from app.errors import bad_request
from app.schemas import IdentifyDeviceRequest, IdentifyDeviceResponse

router = APIRouter(tags=["ai"])

_FORM_OVERHEAD_BYTES = 64 * 1024
"""Room for the multipart boundaries and the optional text field around the photo."""
_MAX_FORM_BYTES = MAX_IMAGE_BYTES + _FORM_OVERHEAD_BYTES
_MAX_TEXT_FIELD_BYTES = 4 * 1024
"""Largest `text` form field the parser will hold; the name itself may be 500 characters."""
_MAX_JSON_BYTES = 16 * 1024
"""Far more than a 500-character name needs, even with every character escaped."""

_REQUEST_BODY: dict[str, Any] = {
    "required": True,
    "content": {
        "application/json": {"schema": IdentifyDeviceRequest.model_json_schema()},
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "format": "binary",
                        "description": "Photo of the label (JPEG, PNG, GIF or WebP, under 5 MB).",
                    },
                    "text": {
                        "type": "string",
                        "maxLength": 500,
                        "description": "Optional name or label text.",
                    },
                },
            }
        },
    },
}


@router.post("/ai/identify-device", openapi_extra={"requestBody": _REQUEST_BODY})
async def identify_device(request: Request, context: ContextDep) -> IdentifyDeviceResponse:
    """Identify a device from JSON `{ "text": ... }` or a multipart `image` upload."""
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        text, image, image_type = await _read_form(request)
    else:
        body = await _limited(request, _MAX_JSON_BYTES, "The request is too large.").body()
        text, image, image_type = _validated(body).text, None, None
    return await context.identifier.identify(text, image, image_type)


async def _read_form(request: Request) -> tuple[str | None, bytes | None, str | None]:
    """The text, photo bytes and photo type from a multipart upload."""
    too_large = "The photo is too large; please send one under 5 MB."
    async with _limited(request, _MAX_FORM_BYTES, too_large).form(
        max_files=1, max_fields=4, max_part_size=_MAX_TEXT_FIELD_BYTES
    ) as form:
        upload, text = form.get("image"), form.get("text")
        if upload is not None and not isinstance(upload, UploadFile):
            raise bad_request("'image' must be a file upload.")
        image = await upload.read(MAX_IMAGE_BYTES + 1) if upload is not None else None
        image_type = upload.content_type if upload is not None else None
    if not isinstance(text, str) or not text.strip():
        return None, image, image_type  # a photo on its own is fine
    return _validated({"text": text}).text, image, image_type


def _validated(body: bytes | dict[str, Any]) -> IdentifyDeviceRequest:
    """The request checked like any JSON body (400 with the usual message if it isn't valid)."""
    try:
        if isinstance(body, bytes):
            return IdentifyDeviceRequest.model_validate_json(body)
        return IdentifyDeviceRequest.model_validate(body)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


def _limited(request: Request, limit: int, message: str) -> Request:
    """The same request, except that its body can't be read past `limit` bytes (400 instead).

    The Content-Length header alone isn't enough: a chunked upload has none. So bytes are
    counted as they arrive, and nothing past the limit is ever read, buffered or spooled.
    """
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise bad_request(message)
    received = 0

    async def receive() -> Message:
        nonlocal received
        chunk = await request.receive()
        if chunk["type"] == "http.request":
            received += len(chunk.get("body", b""))
            if received > limit:
                raise bad_request(message)
        return chunk

    return Request(request.scope, receive)
