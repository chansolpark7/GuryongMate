"""planner 테스트 공용 fixture. 실제 DB 를 쓰고, places 조회는 가짜 구현으로 바꾼다.

    docker compose run --rm planner pytest tests/planner
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db import SessionLocal, init_db
from app.main import app
from app.planner.models import PlannerPlan
from app.planner.places import PlacesUnavailable, get_place_lookup
from app.planner.schemas import PlaceInfo

USER = "test-planner-user"
H = {"X-User-Id": USER}

# 구룡포 중심가에서 북쪽으로 약 300m 간격 + 멀리 떨어진 호미곶
PLACES = {p.id: p for p in [
    PlaceInfo(id="p-house", title="가옥거리", lat=35.9880, lng=129.5570),
    PlaceInfo(id="p-food", title="물회집", lat=35.9907, lng=129.5570),
    PlaceInfo(id="p-museum", title="과메기문화관", lat=35.9934, lng=129.5570),
    PlaceInfo(id="p-cafe", title="카페", lat=35.9894, lng=129.5570),
    PlaceInfo(id="p-far", title="호미곶", lat=36.0765, lng=129.5670),
]}


class FakeLookup:
    down = False

    def get(self, place_id):
        if self.down:
            raise PlacesUnavailable("down")
        return PLACES.get(place_id)


fake = FakeLookup()


@pytest.fixture(scope="module")
def client():
    init_db()

    def cleanup():
        with SessionLocal.begin() as s:
            s.execute(delete(PlannerPlan).where(PlannerPlan.user_id.like("test-planner-%")))

    cleanup()
    app.dependency_overrides[get_place_lookup] = lambda: fake
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_place_lookup, None)
    cleanup()


@pytest.fixture(autouse=True)
def _places_up():
    fake.down = False
    yield
    fake.down = False


# ---------- 도우미 ----------

def ok(resp, status=200):
    assert resp.status_code == status, resp.text
    return resp.json()


def err(resp, status: int, code: str) -> dict:
    """오류 형식 {"detail": {"code", "message", ...}} 과 코드를 함께 확인하고 detail 을 돌려준다."""
    assert resp.status_code == status, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, dict) and detail["code"] == code, resp.text
    assert detail["message"]
    return detail


def new_plan(client, **kw) -> dict:
    body = {"title": "구룡포 당일치기", "start_date": "2026-10-10", "day_start": "12:30", "day_end": "18:00"} | kw
    return ok(client.post("/planner/plans", json=body, headers=H), 201)


def add(client, pid: int, place_id: str, **kw) -> dict:
    return ok(client.post(f"/planner/plans/{pid}/items", json={"place_id": place_id} | kw, headers=H), 201)


def get_plan(client, pid: int) -> dict:
    return ok(client.get(f"/planner/plans/{pid}", headers=H))


def order(plan: dict, day: int = 1) -> list[str]:
    return [i["place_id"] for i in plan["days"][day - 1]["items"]]


def item_id(plan: dict, place_id: str, day: int = 1) -> int:
    return next(i["id"] for i in plan["days"][day - 1]["items"] if i["place_id"] == place_id)
