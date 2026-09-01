from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.common.exceptions.base import AppException
from app.common.middleware.trace import get_trace_id


async def app_exception_handler(_: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=jsonable_encoder({"success": False, "data": None, "error": {"code": exc.code, "message": exc.message, "details": exc.details}, "trace_id": get_trace_id()}))


async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content=jsonable_encoder({"success": False, "data": None, "error": {"code": "VALIDATION_ERROR", "message": "request validation failed", "details": {"errors": exc.errors()}}, "trace_id": get_trace_id()}))
