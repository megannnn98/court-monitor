"""The JSON error body every API error uses."""

from fastapi.responses import JSONResponse
from pydantic import BaseModel

from observability import REQUEST_ID_HEADER, request_id_var


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    request_id = request_id_var.get()
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error=ErrorBody(code=code, message=message, request_id=request_id)
        ).model_dump(),
        headers={REQUEST_ID_HEADER: request_id},
    )
