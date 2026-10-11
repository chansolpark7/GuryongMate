"""인증 공용 코드. account 서비스가 JWT 를 발급하고, 나머지 서비스는 서명만 확인해 user_id 를 꺼낸다.
명세: docs/account/README.md §5

    from app.core.auth import CurrentUser, OptionalUser, ForwardAuth

    @router.get("/plans")
    def list_plans(user_id: CurrentUser): ...      # 로그인 필수
    def list_public(user_id: OptionalUser): ...    # 로그인 선택 (없으면 None)
    async def call_other(auth: ForwardAuth): ...   # 다른 서비스를 사용자 대신 부를 때 붙일 헤더

토큰 형식: Authorization: Bearer <JWT(HS256)>, claim `sub` = user_id, `typ` = "access"

AUTH_REQUIRED=false (개발·테스트) 이고 토큰이 없으면 X-User-Id 헤더 값(없으면 'anonymous')을 사용자로 본다.
토큰이 있는데 잘못됐으면 개발 모드에서도 401 이다.
"""
import hmac
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

ANONYMOUS = "anonymous"
_bearer = HTTPBearer(auto_error=False)


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(401, {"code": "unauthorized", "message": message}, headers={"WWW-Authenticate": "Bearer"})


def issue_token(user_id: str, ttl: timedelta = timedelta(days=7)) -> str:
    now = datetime.now(UTC)
    payload = {"sub": user_id, "iat": now, "exp": now + ttl, "typ": "access"}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> str:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        if claims.get("typ", "access") != "access":
            raise jwt.InvalidTokenError("access 토큰이 아닙니다")
        return claims["sub"]
    except (jwt.PyJWTError, KeyError) as e:
        raise _unauthorized("유효하지 않거나 만료된 토큰입니다.") from e


def optional_user(cred: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> str | None:
    return decode_token(cred.credentials) if cred else None


def current_user(
    user_id: Annotated[str | None, Depends(optional_user)],
    x_user_id: Annotated[str | None, Header(alias="X-User-Id", max_length=64,
                                            description="개발 모드(AUTH_REQUIRED=false)에서 토큰 대신 쓰는 사용자 ID")] = None,
) -> str:
    if user_id:
        return user_id
    if settings.auth_required:
        raise _unauthorized("로그인이 필요합니다.")
    return x_user_id or ANONYMOUS


def forward_auth(
    cred: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    user_id: Annotated[str, Depends(current_user)],
) -> dict[str, str]:
    """다른 서비스를 이 사용자 대신 호출할 때 붙일 헤더 (받은 토큰을 그대로, 개발 모드면 X-User-Id)."""
    if cred:
        return {"Authorization": f"Bearer {cred.credentials}"}
    return {"X-User-Id": user_id}


def admin_request(
    x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token", description="관리 토큰 (공개 서버의 데모 조작 API)")] = None,
) -> bool:
    """관리 토큰(ADMIN_TOKEN)이 설정돼 있고 요청 헤더 X-Admin-Token 이 같으면 True. docs/deploy/README.md §3"""
    return bool(settings.admin_token and x_admin_token and hmac.compare_digest(settings.admin_token, x_admin_token))


CurrentUser = Annotated[str, Depends(current_user)]
OptionalUser = Annotated[str | None, Depends(optional_user)]
ForwardAuth = Annotated[dict[str, str], Depends(forward_auth)]
AdminRequest = Annotated[bool, Depends(admin_request)]
