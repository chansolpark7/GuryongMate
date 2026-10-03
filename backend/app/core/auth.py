"""인증 공용 코드. account 서비스가 JWT 를 발급하고, 나머지 서비스는 서명만 확인해 user_id 를 꺼낸다.

    from app.core.auth import CurrentUser, OptionalUser

    @router.get("/plans")
    def list_plans(user_id: CurrentUser): ...      # 로그인 필수 (AUTH_REQUIRED=false 면 'anonymous')
    def list_public(user_id: OptionalUser): ...    # 로그인 선택 (없으면 None)

토큰 형식: Authorization: Bearer <JWT(HS256)>, claim `sub` = user_id
"""
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

ANONYMOUS = "anonymous"
_bearer = HTTPBearer(auto_error=False)


def issue_token(user_id: str, ttl: timedelta = timedelta(days=7)) -> str:
    now = datetime.now(UTC)
    return jwt.encode({"sub": user_id, "iat": now, "exp": now + ttl}, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> str:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])["sub"]
    except (jwt.PyJWTError, KeyError) as e:
        raise HTTPException(401, "유효하지 않은 토큰입니다.", headers={"WWW-Authenticate": "Bearer"}) from e


def optional_user(cred: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> str | None:
    return decode_token(cred.credentials) if cred else None


def current_user(user_id: Annotated[str | None, Depends(optional_user)]) -> str:
    if user_id:
        return user_id
    if settings.auth_required:
        raise HTTPException(401, "로그인이 필요합니다.", headers={"WWW-Authenticate": "Bearer"})
    return ANONYMOUS


CurrentUser = Annotated[str, Depends(current_user)]
OptionalUser = Annotated[str | None, Depends(optional_user)]
