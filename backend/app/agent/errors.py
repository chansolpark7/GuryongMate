"""agent 오류 형식: {"detail": {"code": "...", "message": "...", ...추가 필드}} (planner 와 같은 형식).

- 서비스 코드는 AgentError 를 던진다. planner 가 거부한 오류(ApiError)는 코드·추가 필드를 그대로 전달한다.
- 요청 검증 오류는 AgentRoute 가 422 invalid_request 로 바꾼다 (agent 경로에만 적용).
"""
from collections.abc import Callable
from typing import Any

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.agent.api import ApiError


class AgentError(Exception):
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


class AgentRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def route(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as e:
                return JSONResponse(validation_body(e), status_code=422)
            except AgentError as e:
                return JSONResponse(e.body(), status_code=e.status)
            except ApiError as e:
                return JSONResponse(e.body(), status_code=e.status)

        return route
