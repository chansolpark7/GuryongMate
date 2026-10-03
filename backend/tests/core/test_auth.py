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
