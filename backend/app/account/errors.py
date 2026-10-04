"""account 오류 형식: {"detail": {"code": "...", "message": "...", ...}} (다른 서비스와 같은 형식).
요청 검증 오류는 AccountRoute 가 422 invalid_request 로 바꾼다 (account 경로에만 적용)."""
from collections.abc import Callable
from typing import Any

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


class AccountError(Exception):
    def __init__(self, status: int, code: str, message: str, /, headers: dict | None = None, **extra: Any):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers
        self.extra = extra

    def response(self) -> JSONResponse:
        return JSONResponse({"detail": {"code": self.code, "message": self.message, **self.extra}},
                            status_code=self.status, headers=self.headers)


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


class AccountRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def route(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as e:
                # 비밀번호 같은 입력값이 응답에 되돌아가지 않도록 input 은 빼고 위치·메시지만 준다
                errors = [{"loc": _clean(list(x.get("loc", []))), "msg": _clean(x.get("msg", ""))} for x in e.errors()]
                first = errors[0] if errors else {"loc": [], "msg": ""}
                where = ".".join(str(x) for x in first["loc"])
                return JSONResponse({"detail": {"code": "invalid_request",
                                                "message": f"요청 형식이 잘못됐습니다: {where} — {first['msg']}",
                                                "errors": errors}}, status_code=422)
            except AccountError as e:
                return e.response()

        return route
