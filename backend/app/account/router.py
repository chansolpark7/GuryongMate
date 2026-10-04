"""account API: 로그인 흐름(/auth/*)과 내 정보(/users/me). 명세: docs/account/README.md"""
import uuid
from datetime import timedelta
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.account import security
from app.account.config import settings
from app.account.errors import AccountError, AccountRoute
from app.account.google import GoogleVerifier, get_google_verifier
from app.account.models import AccountUser
from app.account.schemas import (
    GoogleLoginRequest,
    LoginRequest,
    PasswordChange,
    ProfileUpdate,
    RefreshRequest,
    SignupRequest,
    TokenResponse,
    UserView,
)
from app.core.auth import optional_user
from app.db import get_db

router = APIRouter(tags=["account"], route_class=AccountRoute)

DbSession = Annotated[Session, Depends(get_db)]
Google = Annotated[GoogleVerifier, Depends(get_google_verifier)]


def _invalid_credentials() -> AccountError:
    return AccountError(401, "invalid_credentials", "아이디 또는 비밀번호가 맞지 않습니다.")


# ---------- 로그인 흐름 ----------

@router.post("/auth/signup", response_model=TokenResponse, status_code=201)
def signup(body: SignupRequest, db: DbSession) -> TokenResponse:
    security.check_password_rules(body.login_id, body.password)
    if db.scalars(select(AccountUser).where(AccountUser.login_id == body.login_id)).first():
        raise AccountError(409, "login_id_taken", "이미 사용 중인 아이디입니다.")
    user = AccountUser(id=uuid.uuid4().hex, provider="password", login_id=body.login_id,
                       password_hash=security.hash_password(body.password),
                       display_name=body.display_name or body.login_id, name_customized=body.display_name is not None,
                       failed_logins=0, last_login_at=security.now())
    db.add(user)
    db.flush()
    tokens = security.issue_tokens(db, user, created=True)
    db.commit()
    return tokens


@router.post("/auth/login", response_model=TokenResponse)
def login(body: LoginRequest, db: DbSession) -> TokenResponse:
    user = db.scalars(select(AccountUser).where(AccountUser.login_id == body.login_id,
                                                AccountUser.provider == "password").with_for_update()).first()
    if user and user.locked_until and user.locked_until > security.now():
        retry = int((user.locked_until - security.now()).total_seconds()) + 1
        raise AccountError(429, "too_many_attempts", f"로그인 시도가 많아 {retry // 60 + 1}분 뒤에 다시 시도하세요.",
                           headers={"Retry-After": str(retry)}, retry_after=retry)
    if not security.verify_password(user.password_hash if user else None, body.password):
        if user:
            user.failed_logins += 1
            if user.failed_logins >= settings.max_failures:
                user.locked_until = security.now() + timedelta(minutes=settings.lock_min)
                user.failed_logins = 0
            db.commit()
        raise _invalid_credentials()
    user.failed_logins, user.locked_until, user.last_login_at = 0, None, security.now()
    tokens = security.issue_tokens(db, user)
    db.commit()
    return tokens


@router.post("/auth/google", response_model=TokenResponse)
def google_login(body: GoogleLoginRequest, db: DbSession, google: Google) -> TokenResponse:
    claims = google.verify(body.id_token)
    user = db.scalars(select(AccountUser).where(AccountUser.google_sub == claims["sub"])).first()
    created = user is None
    name = (claims.get("name") or claims.get("email", "").split("@")[0] or "사용자")[:30]
    if created:
        user = AccountUser(id=uuid.uuid4().hex, provider="google", google_sub=claims["sub"], display_name=name,
                           name_customized=False, failed_logins=0)
        db.add(user)
    user.email = claims.get("email")
    if not user.name_customized:
        user.display_name = name
    user.last_login_at = security.now()
    db.flush()
    tokens = security.issue_tokens(db, user, created=created)
    db.commit()
    return tokens


@router.post("/auth/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, db: DbSession) -> TokenResponse:
    row = security.find_refresh(db, body.refresh_token)
    if row is None or row.expires_at <= security.now():
        raise AccountError(401, "invalid_refresh_token", "다시 로그인해 주세요.")
    if row.revoked_at is not None:
        # 이미 쓴 토큰이 다시 왔다 = 탈취 의심 -> 이 계정의 모든 로그인을 끊는다
        security.revoke_all(db, row.user_id)
        db.commit()
        raise AccountError(401, "refresh_token_reused", "보안을 위해 모든 기기에서 로그아웃됐습니다. 다시 로그인해 주세요.")
    user = db.get(AccountUser, row.user_id)
    row.revoked_at = security.now()
    tokens = security.issue_tokens(db, user)
    db.commit()
    return tokens


@router.post("/auth/logout", status_code=204)
def logout(body: RefreshRequest, db: DbSession) -> Response:
    row = security.find_refresh(db, body.refresh_token)
    if row is not None and row.revoked_at is None:
        db.delete(row)  # 지운다: 로그아웃한 토큰을 다시 써도 '탈취'로 보지 않고 그냥 무효
        db.commit()
    return Response(status_code=204)


# ---------- 내 정보 (항상 access 토큰 필요) ----------

def _me(db: Session, user_id: str | None) -> AccountUser:
    user = db.get(AccountUser, user_id) if user_id else None
    if user is None:
        raise AccountError(401, "unauthorized", "로그인이 필요합니다.", headers={"WWW-Authenticate": "Bearer"})
    return user


TokenUser = Annotated[str | None, Depends(optional_user)]  # 개발 모드의 X-User-Id 는 받지 않는다


@router.get("/users/me", response_model=UserView)
def get_me(db: DbSession, user_id: TokenUser) -> UserView:
    return UserView.model_validate(_me(db, user_id))


@router.patch("/users/me", response_model=UserView)
def update_me(body: ProfileUpdate, db: DbSession, user_id: TokenUser) -> UserView:
    user = _me(db, user_id)
    user.display_name, user.name_customized = body.display_name, True
    db.commit()
    return UserView.model_validate(user)


@router.put("/users/me/password", response_model=TokenResponse)
def change_password(body: PasswordChange, db: DbSession, user_id: TokenUser) -> TokenResponse:
    user = _me(db, user_id)
    if user.provider != "password":
        raise AccountError(409, "no_password", "Google 계정은 비밀번호가 없습니다.")
    if not security.verify_password(user.password_hash, body.current_password):
        raise _invalid_credentials()
    security.check_password_rules(user.login_id, body.new_password)
    user.password_hash = security.hash_password(body.new_password)
    security.revoke_all(db, user.id)  # 다른 기기 로그아웃
    tokens = security.issue_tokens(db, user)
    db.commit()
    return tokens


class DataCleaner:
    """탈퇴 시 다른 서비스(planner, agent)의 이 사용자 데이터를 지운다. 같은 access 토큰으로 DELETE /<서비스>/me."""

    SERVICES = ("planner", "agent")

    def __init__(self, base_url: str):
        self._base = base_url

    def delete_all(self, authorization: str) -> None:
        try:
            with httpx.Client(base_url=self._base, timeout=15) as http:
                for name in self.SERVICES:
                    r = http.delete(f"/{name}/me", headers={"Authorization": authorization})
                    if r.status_code != 204:
                        raise AccountError(503, "upstream_unavailable",
                                           f"{name} 데이터를 지우지 못했습니다 (HTTP {r.status_code}). 다시 시도해 주세요.")
        except httpx.HTTPError as e:
            raise AccountError(503, "upstream_unavailable", "다른 서비스에 연결할 수 없습니다. 다시 시도해 주세요.") from e


def get_data_cleaner() -> DataCleaner:
    return DataCleaner(settings.api_base_url)


@router.delete("/users/me", status_code=204)
def delete_me(db: DbSession, user_id: TokenUser, cleaner: Annotated[DataCleaner, Depends(get_data_cleaner)],
              authorization: Annotated[str | None, Header()] = None) -> Response:
    """탈퇴: 다른 서비스 데이터를 먼저 지우고, 모두 성공하면 계정·토큰을 지운다."""
    user = _me(db, user_id)
    cleaner.delete_all(authorization)
    db.delete(user)  # refresh 토큰은 ON DELETE CASCADE
    db.commit()
    return Response(status_code=204)
