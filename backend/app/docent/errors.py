"""docent 오류 형식: {"detail": {"code": "...", "message": "...", ...추가 필드}} (planner 와 같은 형식).

- 서비스 코드는 DocentError(status, code, message, **extra) 를 던진다.
- 요청 검증 오류는 DocentRoute 가 422 invalid_request 로 바꾼다 (docent 경로에만 적용).
"""
from collections.abc import Callable
from typing import Any

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException


class DocentError(Exception):
    def __init__(self, status: int, code: str, message: str, /, **extra: Any):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def body(self) -> dict:
        return {"detail": {"code": self.code, "message": self.message, **self.extra}}


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def validation_body(exc: RequestValidationError) -> dict:
    errors = [{"loc": _clean(list(e.get("loc", []))), "msg": _clean(e.get("msg", ""))} for e in exc.errors()]
    first = errors[0] if errors else {"loc": [], "msg": ""}
    where = ".".join(str(x) for x in first["loc"])
    return {"detail": {"code": "invalid_request", "message": f"요청 형식이 잘못됐습니다: {where} — {first['msg']}",
                       "errors": errors}}


class DocentRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def route(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as e:
                return JSONResponse(validation_body(e), status_code=422)
            except DocentError as e:
                return JSONResponse(e.body(), status_code=e.status)
            except HTTPException as e:
                if e.status_code != 400:
                    raise
                return JSONResponse({"detail": {"code": "invalid_request", "message": f"요청 본문을 읽을 수 없습니다: {e.detail}",
                                                "errors": []}}, status_code=422)

        return route
