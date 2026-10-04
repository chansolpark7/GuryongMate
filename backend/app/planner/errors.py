"""planner 오류 형식: {"detail": {"code": "...", "message": "...", ...추가 필드}}

- 서비스 코드는 PlannerError(status, code, message, **extra) 를 던진다.
- 요청 검증 오류(헤더·경로·쿼리·본문)는 PlannerRoute 가 같은 형식의 422 invalid_request 로 바꾼다.
  (앱 전체의 예외 처리기는 공용 main.py 소관이라, 라우트 클래스로 planner 경로에만 적용한다)
"""
from collections.abc import Callable
from typing import Any

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


class PlannerError(Exception):
    def __init__(self, status: int, code: str, message: str, /, **extra: Any):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def body(self) -> dict:
        return {"detail": {"code": self.code, "message": self.message, **self.extra}}


def not_found(code: str, what: str) -> PlannerError:
    return PlannerError(404, code, f"{what} 을(를) 찾을 수 없습니다.")


def _clean(value: Any) -> Any:
    """검증 오류의 ctx 등에 JSON 으로 못 바꾸는 값(예외 객체)이 섞여 있어 문자열로 바꾼다."""
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


class PlannerRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def route(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as e:
                return JSONResponse(validation_body(e), status_code=422)
            except PlannerError as e:
                return JSONResponse(e.body(), status_code=e.status)

        return route
