"""비밀번호 해시와 토큰 발급·회전."""
import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.account.config import settings
from app.account.errors import AccountError
from app.account.models import AccountRefreshToken, AccountUser
from app.account.schemas import TokenResponse, UserView
from app.core.auth import issue_token

_hasher = PasswordHasher()
# 존재하지 않는 아이디로 로그인할 때도 같은 시간이 걸리게 하려는 더미 해시
_DUMMY_HASH = _hasher.hash("dummy-password-0")


def now() -> datetime:
    return datetime.now(UTC)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def check_password_rules(login_id: str | None, password: str) -> None:
    if not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        raise AccountError(422, "weak_password", "비밀번호는 영문과 숫자를 하나 이상씩 포함해야 합니다.")
    if login_id and password.lower() == login_id.lower():
        raise AccountError(422, "weak_password", "비밀번호는 아이디와 달라야 합니다.")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_tokens(db: Session, user: AccountUser, created: bool = False) -> TokenResponse:
    """access JWT 와 refresh 토큰을 새로 발급한다 (refresh 는 해시만 저장). 호출 쪽에서 commit."""
    access_ttl = timedelta(minutes=settings.access_ttl_min)
    refresh_ttl = timedelta(days=settings.refresh_ttl_days)
    refresh = secrets.token_urlsafe(48)
    db.add(AccountRefreshToken(user_id=user.id, token_hash=_hash_token(refresh), expires_at=now() + refresh_ttl))
    return TokenResponse(access_token=issue_token(user.id, ttl=access_ttl), expires_in=int(access_ttl.total_seconds()),
                         refresh_token=refresh, refresh_expires_in=int(refresh_ttl.total_seconds()),
                         user=UserView.model_validate(user), created=created)


def find_refresh(db: Session, token: str) -> AccountRefreshToken | None:
    """동시에 같은 토큰으로 두 번 갱신하지 못하도록 행을 잠근다."""
    return db.scalars(select(AccountRefreshToken).where(AccountRefreshToken.token_hash == _hash_token(token))
                      .with_for_update()).first()


def revoke_all(db: Session, user_id: str) -> None:
    """이 계정의 모든 로그인을 끊는다. 행을 지워서, 끊긴 토큰은 이후 '재사용(탈취)'이 아니라 그냥 무효로 처리된다.
    (revoked_at 표시는 회전으로 폐기된 토큰에만 써서 재사용 탐지에 쓴다)"""
    db.execute(delete(AccountRefreshToken).where(AccountRefreshToken.user_id == user_id))
