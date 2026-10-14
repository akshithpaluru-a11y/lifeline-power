"""AI device helper: identify a device from its name or a photo of its label."""

from typing import Any

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from app.ai import MAX_IMAGE_BYTES
from app.api.deps import ContextDep
from app.errors import bad_request
from app.schemas import IdentifyDeviceRequest, IdentifyDeviceResponse

router = APIRouter(tags=["ai"])

_FORM_OVERHEAD_BYTES = 64 * 1024
"""Room for the multipart boundaries and the optional text field around the photo."""

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
                    "text": {"type": "string", "description": "Optional name or label text."},
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
        try:
            body = IdentifyDeviceRequest.model_validate_json(await request.body())
        except ValidationError as exc:
            raise RequestValidationError(exc.errors()) from exc
        text, image, image_type = body.text, None, None
    return await context.identifier.identify(text, image, image_type)


async def _read_form(request: Request) -> tuple[str | None, bytes | None, str | None]:
    """The text, photo bytes and photo type from a multipart upload."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_IMAGE_BYTES + _FORM_OVERHEAD_BYTES:
        raise bad_request("The photo is too large; please send one under 5 MB.")
    async with request.form(max_files=1, max_fields=4) as form:
        upload, text = form.get("image"), form.get("text")
        if upload is not None and not isinstance(upload, UploadFile):
            raise bad_request("'image' must be a file upload.")
        image = await upload.read(MAX_IMAGE_BYTES + 1) if upload is not None else None
        image_type = upload.content_type if upload is not None else None
    return (text if isinstance(text, str) else None), image, image_type
