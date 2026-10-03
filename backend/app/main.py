"""앱 진입점. 모듈을 자동 등록하므로 기능 개발 중에는 이 파일을 수정할 필요가 없다.

규칙: app/<module>/models.py  -> 테이블 자동 생성
      app/<module>/router.py  -> `router` (APIRouter) 자동 등록

SERVICE 환경변수로 이 프로세스가 맡을 모듈을 고른다 (compose 에서 서비스마다 지정).
  SERVICE=places        -> places 의 테이블·API 만
  SERVICE=all (기본)     -> 전체 (테스트, 컨테이너 하나로 띄울 때)
"""
import importlib
import pkgutil
from contextlib import asynccontextmanager
from types import ModuleType

from fastapi import FastAPI

import app as app_pkg
from app.core.config import settings
from app.db import init_db

INFRA = {"core"}  # 모듈이 아닌 공용 패키지


def _modules() -> list[str]:
    names = [i.name for i in pkgutil.iter_modules(app_pkg.__path__) if i.ispkg and i.name not in INFRA]
    selected = settings.services
    if selected is not None:
        unknown = selected - set(names)
        if unknown:
            raise RuntimeError(f"SERVICE 에 알 수 없는 모듈: {sorted(unknown)} (가능: {names})")
        names = [n for n in names if n in selected]
    return names


def _discover(submodule: str) -> list[ModuleType]:
    found = []
    for mod in _modules():
        name = f"app.{mod}.{submodule}"
        try:
            found.append(importlib.import_module(name))
        except ModuleNotFoundError as e:
            if e.name != name:
                raise
    return found


_discover("models")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


# 서비스 하나만 맡을 때는 게이트웨이 뒤에서도 열리도록 문서 경로에 모듈 이름을 붙인다 (/places/docs)
_single = next(iter(settings.services)) if settings.services and len(settings.services) == 1 else None
app = FastAPI(
    title=f"GuryongMate {_single or 'API'}",
    lifespan=lifespan,
    docs_url=f"/{_single}/docs" if _single else "/docs",
    openapi_url=f"/{_single}/openapi.json" if _single else "/openapi.json",
)

for module in _discover("router"):
    app.include_router(module.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.service}
