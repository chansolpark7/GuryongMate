"""docent 테스트 공용 fixture. 실제 DB 를 쓰고, planner 호출(계획 소유 확인)은 같은 앱을 ASGI 로 부른다.

    docker compose run --rm docent pytest tests/docent
"""
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db import SessionLocal, init_db
from app.docent.models import (DocentGuideNote, DocentMemory, DocentMemoryVersion, DocentSearchCache, DocentStaleMark,
                               DocentUsage)
from app.docent.plans import get_http
from app.main import app
from app.planner.models import PlannerPlan

USER = "test-docent-user"
OTHER = "test-docent-other"
H = {"X-User-Id": USER}
H2 = {"X-User-Id": OTHER}


def _cleanup():
    with SessionLocal.begin() as s:
        for model in (DocentMemory, DocentMemoryVersion, DocentGuideNote, DocentStaleMark, DocentUsage):
            s.execute(delete(model).where(model.user_id.like("test-docent-%")))
        s.execute(delete(DocentSearchCache))  # 캐시는 사용자와 무관하고 버려도 되는 데이터
        s.execute(delete(PlannerPlan).where(PlannerPlan.user_id.like("test-docent-%")))


@pytest.fixture(scope="module")
def client():
    init_db()
    _cleanup()
    asgi = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://app")
    app.dependency_overrides[get_http] = lambda: asgi
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_http, None)
    _cleanup()


# ---------- 도우미 ----------

def ok(resp, status=200):
    assert resp.status_code == status, resp.text
    return resp.json() if resp.content else None


def err(resp, status: int, code: str) -> dict:
    assert resp.status_code == status, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, dict) and detail["code"] == code, resp.text
    assert detail["message"]
    return detail


def new_plan(client, headers=H) -> int:
    body = {"title": "구룡포 당일치기", "start_date": "2026-10-10", "day_start": "10:00", "day_end": "18:00"}
    return ok(client.post("/planner/plans", json=body, headers=headers), 201)["id"]


def remember(client, category="interest", text="역사·근대 건축을 좋아함", scope="user", plan_id=None, headers=H,
             status=201) -> dict:
    body = {"scope": scope, "category": category, "text": text, "source": "msg-1"}
    if plan_id is not None:
        body["plan_id"] = plan_id
    return ok(client.post("/docent/memory", json=body, headers=headers), status)


READY_NOTE = {
    "status": "ready",
    "short": "구룡포 일본인가옥거리는 1900년대 초 일본 어민들이 살던 거리입니다.",
    "long": "구룡포는 1900년대 초부터 일본 어민들이 모여 살며 큰 어항으로 자랐습니다. 지금 남은 가옥들은 그때의 모습입니다.",
    "for_you": ["사진을 좋아하시니 계단 위 공원에서 내려다보는 각도를 추천해요"],
    "highlights": ["구룡포 공원 계단"],
    "companion_tips": ["계단 대신 오른쪽 완만한 길"],
    "cautions": ["비 오면 돌길 미끄러움"],
    "qa": [{"q": "왜 일본인이 여기 살았나요?", "a": "어업 이민 정책으로 어민들이 들어왔습니다."}],
    "sources": [{"title": "구룡포 근대문화역사거리", "url": "https://www.pohang.go.kr/", "tier": "official"}],
    "memory_version": 1,
    "model": "gpt-5.4-mini",
}
