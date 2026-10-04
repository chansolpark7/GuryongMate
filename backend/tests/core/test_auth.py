from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import auth
from app.core.auth import CurrentUser, OptionalUser, issue_token

app = FastAPI()


@app.get("/me")
def me(user_id: CurrentUser) -> dict:
    return {"user_id": user_id}


@app.get("/maybe")
def maybe(user_id: OptionalUser) -> dict:
    return {"user_id": user_id}


client = TestClient(app)


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_token_roundtrip():
    assert client.get("/me", headers=bearer(issue_token("u1"))).json() == {"user_id": "u1"}
    assert client.get("/maybe").json() == {"user_id": None}


def test_invalid_and_expired_token():
    assert client.get("/me", headers=bearer("garbage")).status_code == 401
    assert client.get("/me", headers=bearer(issue_token("u1", ttl=timedelta(seconds=-1)))).status_code == 401


def test_auth_required_switch(monkeypatch: pytest.MonkeyPatch):
    assert client.get("/me").json() == {"user_id": auth.ANONYMOUS}
    monkeypatch.setattr(auth.settings, "auth_required", True)
    assert client.get("/me").status_code == 401


@app.get("/fwd")
def fwd(headers: auth.ForwardAuth) -> dict:
    return headers


def test_error_format_and_dev_user_header(monkeypatch: pytest.MonkeyPatch):
    r = client.get("/me", headers=bearer("garbage"))
    assert r.json()["detail"]["code"] == "unauthorized"
    # 개발 모드: 토큰이 없으면 X-User-Id, 있으면 토큰 우선
    assert client.get("/me", headers={"X-User-Id": "dev"}).json() == {"user_id": "dev"}
    assert client.get("/me", headers={"X-User-Id": "dev"} | bearer(issue_token("u1"))).json() == {"user_id": "u1"}
    # 로그인 필수면 X-User-Id 는 무시하고 401
    monkeypatch.setattr(auth.settings, "auth_required", True)
    r = client.get("/me", headers={"X-User-Id": "dev"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "unauthorized"


def test_forward_auth():
    token = issue_token("u1")
    assert client.get("/fwd", headers=bearer(token)).json() == {"Authorization": f"Bearer {token}"}
    assert client.get("/fwd", headers={"X-User-Id": "dev"}).json() == {"X-User-Id": "dev"}


def test_non_access_token_rejected():
    import jwt as pyjwt
    refresh_like = pyjwt.encode({"sub": "u1", "typ": "refresh"}, auth.settings.jwt_secret, algorithm="HS256")
    assert client.get("/me", headers=bearer(refresh_like)).status_code == 401
