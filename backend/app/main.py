"""앱 진입점. 모듈을 자동 등록하므로 기능 개발 중에는 이 파일을 수정할 필요가 없다.

규칙: app/<module>/models.py  -> 테이블 자동 생성
      app/<module>/router.py  -> `router` (APIRouter) 자동 등록
"""
import importlib
import pkgutil
from contextlib import asynccontextmanager
from types import ModuleType

from fastapi import FastAPI

import app as app_pkg
from app.db import init_db


def _discover(submodule: str) -> list[ModuleType]:
    found = []
    for info in pkgutil.iter_modules(app_pkg.__path__):
        if not info.ispkg:
            continue
        name = f"app.{info.name}.{submodule}"
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


app = FastAPI(title="GuryongMate API", lifespan=lifespan)

for module in _discover("router"):
    app.include_router(module.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
