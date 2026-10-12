"""account API 테스트. Google 토큰은 테스트용 RSA 키로 서명하고, 탈퇴 시 다른 서비스 삭제 호출은 가짜로 바꾼다.

    docker compose run --rm account pytest tests/account
"""
import time
from datetime import timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from app.account.errors import AccountError
from app.account.google import GoogleVerifier, get_google_verifier
from app.account.models import AccountRefreshToken, AccountUser
from app.account.router import get_data_cleaner
from app.account.security import now
from app.db import SessionLocal, init_db
from app.main import app

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CLIENT_ID = "test-client.apps.googleusercontent.com"
verifier = GoogleVerifier([CLIENT_ID], lambda token: KEY.public_key())


class FakeCleaner:
    def __init__(self):
        self.calls: list[str | None] = []
        self.fail = False

    def delete_all(self, authorization):
        self.calls.append(authorization)
        if self.fail:
            raise AccountError(503, "upstream_unavailable", "planner 데이터를 지우지 못했습니다.")


cleaner = FakeCleaner()


@pytest.fixture(scope="module")
def client():
    init_db()

    def cleanup():
        with SessionLocal.begin() as s:
            s.execute(delete(AccountUser).where(AccountUser.login_id.like("test%")))
            s.execute(delete(AccountUser).where(AccountUser.google_sub.like("test-%")))

    cleanup()
    app.dependency_overrides |= {get_google_verifier: lambda: verifier, get_data_cleaner: lambda: cleaner}
    with TestClient(app) as c:
        yield c
    for dep in (get_google_verifier, get_data_cleaner):
        app.dependency_overrides.pop(dep, None)
    cleanup()


@pytest.fixture(autouse=True)
def _reset():
    cleaner.calls, cleaner.fail = [], False


# ---------- 도우미 ----------

_n = [0]


def uid(prefix="test") -> str:
    _n[0] += 1
    return f"{prefix}{int(time.time() * 1000) % 10**8}{_n[0]}"


def ok(resp, status=200):
    assert resp.status_code == status, resp.text
    return resp.json()


def err(resp, status, code) -> dict:
    assert resp.status_code == status, resp.text
    d = resp.json()["detail"]
    assert d["code"] == code and d["message"], resp.text
    return d


def bearer(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def signup(client, login_id=None, password="pass1234", **kw) -> dict:
    return ok(client.post("/auth/signup", json={"login_id": login_id or uid(), "password": password} | kw), 201)


def google_token(sub, email="a@example.com", verified=True, aud=CLIENT_ID, iss="https://accounts.google.com",
                 exp=timedelta(hours=1), name="구글 사용자", key=KEY) -> str:
    t = int(time.time())
    claims = {"sub": sub, "email": email, "email_verified": verified, "aud": aud, "iss": iss, "name": name,
              "iat": t, "exp": t + int(exp.total_seconds())}
    return jwt.encode(claims, key, algorithm="RS256")


# ---------- 가입 ----------

def test_signup_returns_tokens_and_user(client):
    lid = uid()
    r = signup(client, lid.upper(), display_name="서윤")
    assert r["token_type"] == "bearer" and r["expires_in"] == 3600 and r["refresh_expires_in"] == 30 * 86400
    assert r["created"] is True
    u = r["user"]
    assert (u["provider"], u["login_id"], u["email"], u["display_name"]) == ("password", lid, None, "서윤")
    assert ok(client.get("/users/me", headers=bearer(r)))["id"] == u["id"]
    claims = jwt.decode(r["access_token"], options={"verify_signature": False})
    assert claims["sub"] == u["id"] and claims["typ"] == "access" and claims["exp"] - claims["iat"] == 3600
    assert signup(client)["user"]["display_name"].startswith("test")  # 이름 생략 시 아이디


@pytest.mark.parametrize("body", [
    {"login_id": "ab", "password": "pass1234"},               # 너무 짧음
    {"login_id": "한글아이디", "password": "pass1234"},
    {"login_id": "_bad", "password": "pass1234"},             # 기호로 시작
    {"login_id": "testok1", "password": "short1"},           # 8자 미만
    {"login_id": "testok1", "password": "pass1234", "role": "admin"},  # 모르는 필드
    {"login_id": "testok1"},
])
def test_signup_validation(client, body):
    d = err(client.post("/auth/signup", json=body), 422, "invalid_request")
    assert "pass1234" not in str(d) and "short1" not in str(d)  # 비밀번호가 오류 응답에 되돌아가지 않는다


@pytest.mark.parametrize("password", ["onlyletters", "12345678", "TESTWEAK9"])
def test_weak_password(client, password):
    err(client.post("/auth/signup", json={"login_id": "testweak9", "password": password}), 422, "weak_password")


def test_duplicate_login_id_case_insensitive(client):
    lid = uid()
    signup(client, lid)
    err(client.post("/auth/signup", json={"login_id": lid.upper(), "password": "pass1234"}), 409, "login_id_taken")


# ---------- 로그인 ----------

def test_login_and_failures(client):
    lid = uid()
    signup(client, lid, "pass1234")
    r = ok(client.post("/auth/login", json={"login_id": lid.upper(), "password": "pass1234"}))
    assert r["created"] is False and r["user"]["login_id"] == lid
    e1 = err(client.post("/auth/login", json={"login_id": lid, "password": "wrong1234"}), 401, "invalid_credentials")
    e2 = err(client.post("/auth/login", json={"login_id": uid("nobody"), "password": "wrong1234"}), 401,
             "invalid_credentials")
    assert e1["message"] == e2["message"]  # 없는 아이디와 틀린 비밀번호를 구분하지 않는다


def test_lockout_after_five_failures(client):
    lid = uid()
    signup(client, lid, "pass1234")
    for _ in range(5):
        err(client.post("/auth/login", json={"login_id": lid, "password": "nope1234"}), 401, "invalid_credentials")
    r = client.post("/auth/login", json={"login_id": lid, "password": "pass1234"})  # 맞는 비밀번호도 잠김
    d = err(r, 429, "too_many_attempts")
    assert 0 < d["retry_after"] <= 15 * 60 + 1 and r.headers["Retry-After"] == str(d["retry_after"])
    with SessionLocal.begin() as s:  # 잠금 시간이 지났다고 가정
        s.execute(update(AccountUser).where(AccountUser.login_id == lid).values(locked_until=now() - timedelta(seconds=1)))
    ok(client.post("/auth/login", json={"login_id": lid, "password": "pass1234"}))


def test_success_resets_failure_count(client):
    lid = uid()
    signup(client, lid, "pass1234")
    for _ in range(4):
        client.post("/auth/login", json={"login_id": lid, "password": "nope1234"})
    ok(client.post("/auth/login", json={"login_id": lid, "password": "pass1234"}))
    for _ in range(4):
        err(client.post("/auth/login", json={"login_id": lid, "password": "nope1234"}), 401, "invalid_credentials")


# ---------- 토큰 갱신·로그아웃 ----------

def test_refresh_rotation_and_reuse_detection(client):
    t1 = signup(client)
    t2 = ok(client.post("/auth/refresh", json={"refresh_token": t1["refresh_token"]}))
    assert t2["refresh_token"] != t1["refresh_token"] and t2["user"]["id"] == t1["user"]["id"]
    ok(client.get("/users/me", headers=bearer(t2)))
    # 이미 쓴 refresh 토큰을 다시 쓰면 탈취로 보고 모든 로그인을 끊는다
    err(client.post("/auth/refresh", json={"refresh_token": t1["refresh_token"]}), 401, "refresh_token_reused")
    err(client.post("/auth/refresh", json={"refresh_token": t2["refresh_token"]}), 401, "invalid_refresh_token")


def test_refresh_invalid_and_expired(client):
    err(client.post("/auth/refresh", json={"refresh_token": "nope"}), 401, "invalid_refresh_token")
    t = signup(client)
    with SessionLocal.begin() as s:
        s.execute(update(AccountRefreshToken).where(AccountRefreshToken.user_id == t["user"]["id"])
                  .values(expires_at=now() - timedelta(seconds=1)))
    err(client.post("/auth/refresh", json={"refresh_token": t["refresh_token"]}), 401, "invalid_refresh_token")


def test_logout(client):
    t = signup(client)
    other = ok(client.post("/auth/login", json={"login_id": t["user"]["login_id"], "password": "pass1234"}))
    assert client.post("/auth/logout", json={"refresh_token": t["refresh_token"]}).status_code == 204
    assert client.post("/auth/logout", json={"refresh_token": t["refresh_token"]}).status_code == 204  # 멱등
    err(client.post("/auth/refresh", json={"refresh_token": t["refresh_token"]}), 401, "invalid_refresh_token")
    ok(client.post("/auth/refresh", json={"refresh_token": other["refresh_token"]}))  # 다른 기기는 그대로


# ---------- Google ----------

def test_google_login_creates_then_reuses_account(client):
    sub = uid("test-g")
    r1 = ok(client.post("/auth/google", json={"id_token": google_token(sub, email="me@gmail.com")}))
    assert r1["created"] is True
    u = r1["user"]
    assert (u["provider"], u["login_id"], u["email"], u["display_name"]) == ("google", None, "me@gmail.com", "구글 사용자")
    r2 = ok(client.post("/auth/google", json={"id_token": google_token(sub, email="new@gmail.com", name="새 이름")}))
    assert r2["created"] is False and r2["user"]["id"] == u["id"]
    assert (r2["user"]["email"], r2["user"]["display_name"]) == ("new@gmail.com", "새 이름")
    # 사용자가 이름을 바꾸면 Google 이름으로 덮지 않는다
    ok(client.patch("/users/me", json={"display_name": "내가 정한 이름"}, headers=bearer(r2)))
    r3 = ok(client.post("/auth/google", json={"id_token": google_token(sub, name="또 다른 이름")}))
    assert r3["user"]["display_name"] == "내가 정한 이름"


def test_google_and_password_accounts_are_separate(client):
    lid = uid()
    pw = signup(client, lid)
    g = ok(client.post("/auth/google", json={"id_token": google_token(uid("test-g"), email=f"{lid}@gmail.com")}))
    assert pw["user"]["id"] != g["user"]["id"]


@pytest.mark.parametrize("kw", [
    {"aud": "someone-else"},
    {"iss": "https://evil.example.com"},
    {"exp": timedelta(seconds=-10)},
    {"verified": False},
    {"key": rsa.generate_private_key(public_exponent=65537, key_size=2048)},   # 다른 키로 서명
])
def test_google_invalid_tokens(client, kw):
    err(client.post("/auth/google", json={"id_token": google_token(uid("test-g"), **kw)}), 401, "invalid_google_token")


def test_google_garbage_and_unconfigured(client):
    err(client.post("/auth/google", json={"id_token": "not-a-jwt"}), 401, "invalid_google_token")
    app.dependency_overrides[get_google_verifier] = lambda: GoogleVerifier([], lambda t: KEY.public_key())
    try:
        err(client.post("/auth/google", json={"id_token": google_token(uid("test-g"))}), 401, "invalid_google_token")
    finally:
        app.dependency_overrides[get_google_verifier] = lambda: verifier


# ---------- 내 정보 ----------

def test_me_requires_token_even_in_dev_mode(client):
    err(client.get("/users/me"), 401, "unauthorized")
    err(client.get("/users/me", headers={"X-User-Id": "someone"}), 401, "unauthorized")
    err(client.get("/users/me", headers={"Authorization": "Bearer garbage"}), 401, "unauthorized")
    t = signup(client)
    u = ok(client.patch("/users/me", json={"display_name": "새이름"}, headers=bearer(t)))
    assert u["display_name"] == "새이름"
    err(client.patch("/users/me", json={"display_name": ""}, headers=bearer(t)), 422, "invalid_request")
    err(client.patch("/users/me", json={"display_name": "a", "login_id": "x"}, headers=bearer(t)), 422, "invalid_request")


def test_change_password(client):
    lid = uid()
    t = signup(client, lid, "pass1234")
    other = ok(client.post("/auth/login", json={"login_id": lid, "password": "pass1234"}))
    url = "/users/me/password"
    err(client.put(url, json={"current_password": "wrong1234", "new_password": "newpass99"}, headers=bearer(t)),
        401, "invalid_credentials")
    err(client.put(url, json={"current_password": "pass1234", "new_password": "nodigitsss"}, headers=bearer(t)),
        422, "weak_password")
    new = ok(client.put(url, json={"current_password": "pass1234", "new_password": "newpass99"}, headers=bearer(t)))
    # 다른 기기 로그아웃 (무효 처리, 탈취로 보지 않아 새 토큰은 살아 있다)
    err(client.post("/auth/refresh", json={"refresh_token": other["refresh_token"]}), 401, "invalid_refresh_token")
    ok(client.post("/auth/refresh", json={"refresh_token": new["refresh_token"]}))
    err(client.post("/auth/login", json={"login_id": lid, "password": "pass1234"}), 401, "invalid_credentials")
    ok(client.post("/auth/login", json={"login_id": lid, "password": "newpass99"}))


def test_google_account_has_no_password(client):
    g = ok(client.post("/auth/google", json={"id_token": google_token(uid("test-g"))}))
    err(client.put("/users/me/password", json={"current_password": "x", "new_password": "newpass99"},
                   headers=bearer(g)), 409, "no_password")


def test_delete_account(client):
    lid = uid()
    t = signup(client, lid)
    cleaner.fail = True
    err(client.delete("/users/me", headers=bearer(t)), 503, "upstream_unavailable")
    ok(client.get("/users/me", headers=bearer(t)))  # 다른 서비스 삭제가 실패하면 계정은 남는다
    cleaner.fail = False
    assert client.delete("/users/me", headers=bearer(t)).status_code == 204
    assert cleaner.calls[-1] == f"Bearer {t['access_token']}"  # 같은 토큰으로 다른 서비스 데이터를 지운다
    err(client.get("/users/me", headers=bearer(t)), 401, "unauthorized")
    err(client.post("/auth/login", json={"login_id": lid, "password": "pass1234"}), 401, "invalid_credentials")
    err(client.post("/auth/refresh", json={"refresh_token": t["refresh_token"]}), 401, "invalid_refresh_token")
    with SessionLocal() as s:
        assert s.scalars(select(AccountRefreshToken).where(AccountRefreshToken.user_id == t["user"]["id"])).all() == []


def test_data_cleaner_deletes_every_service(client):
    """탈퇴는 planner·trip·docent·agent 의 이 사용자 데이터를 모두 지운다 (관제 콘솔 reset.js WIPE 와 같은 순서).
    실제 DataCleaner 를 같은 앱으로 보내 각 서비스의 DELETE /me 가 있는지·실제로 지우는지 본다."""
    from app.account.router import DataCleaner

    t = signup(client)
    h = bearer(t)
    plan = ok(client.post("/planner/plans", json={"title": "t", "start_date": "2026-10-10"}, headers=h), 201)
    ok(client.post("/docent/memory", json={"scope": "user", "category": "interest", "text": "역사", "source": "user_edit"},
                   headers=h), 201)
    from app.agent.models import AgentSession  # 세션 생성 API 는 planner 를 HTTP 로 부르므로 여기서는 DB 에 직접
    with SessionLocal.begin() as s:
        s.add(AgentSession(id=uid("test-sess"), user_id=t["user"]["id"], plan_id=plan["id"]))
    assert len(ok(client.get("/agent/sessions", headers=h))) == 1
    seen: list[str] = []

    class Spy(httpx.BaseTransport):
        def handle_request(self, request):
            seen.append(f"{request.method} {request.url.path}")
            return client._transport.handle_request(request)

    DataCleaner("http://testserver", transport=Spy()).delete_all(h["Authorization"])
    assert seen == ["DELETE /planner/me", "DELETE /trip/me", "DELETE /docent/me", "DELETE /agent/me"]
    assert ok(client.get("/docent/memory", headers=h))["items"] == []
    assert ok(client.get("/planner/plans", headers=h)) == []
    assert ok(client.get("/agent/sessions", headers=h)) == []


def test_non_utf8_body_uses_error_format(client):
    r = client.post("/auth/signup", content="{\"login_id\": \"테스트\"}".encode("cp949"),
                    headers={"Content-Type": "application/json"})
    err(r, 422, "invalid_request")
