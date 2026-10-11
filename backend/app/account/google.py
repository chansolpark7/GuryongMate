"""Google ID 토큰 검증. 앱이 Google Sign-In 으로 받은 ID 토큰을 Google 공개키로 확인한다 (리다이렉트 흐름 없음)."""
from collections.abc import Callable
from functools import lru_cache

import jwt

from app.account.config import settings
from app.account.errors import AccountError

ISSUERS = ["accounts.google.com", "https://accounts.google.com"]


class GoogleVerifier:
    def __init__(self, client_ids: list[str], key_resolver: Callable[[str], object]):
        """key_resolver: 토큰을 받아 서명 검증용 공개키를 돌려준다 (실서비스는 Google JWKS, 테스트는 가짜 키)."""
        self.client_ids = client_ids
        self._resolve = key_resolver

    def verify(self, token: str) -> dict:
        if not self.client_ids:
            raise AccountError(401, "invalid_google_token", "Google 로그인이 설정되지 않았습니다 (ACCOUNT_GOOGLE_CLIENT_IDS).")
        try:
            key = self._resolve(token)
        except jwt.PyJWKClientConnectionError as e:
            raise AccountError(503, "google_unavailable", "Google 공개키를 가져올 수 없습니다.") from e
        except (jwt.PyJWKClientError, jwt.DecodeError) as e:
            raise AccountError(401, "invalid_google_token", "Google 토큰 형식이 잘못됐습니다.") from e
        try:
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.client_ids, issuer=ISSUERS,
                                options={"require": ["sub", "exp", "iss", "aud"]})
        except jwt.PyJWTError as e:
            raise AccountError(401, "invalid_google_token", f"Google 토큰 검증 실패: {type(e).__name__}") from e
        if claims.get("email_verified") not in (True, "true"):
            raise AccountError(401, "invalid_google_token", "Google 계정 이메일이 인증되지 않았습니다.")
        return claims


@lru_cache
def _jwks() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(settings.google_certs_url, cache_keys=True, lifespan=3600, timeout=10)


def get_google_verifier() -> GoogleVerifier:
    """FastAPI 의존성. 테스트에서는 가짜 키로 서명한 토큰을 쓰도록 바꾼다."""
    return GoogleVerifier(settings.client_ids, lambda token: _jwks().get_signing_key_from_jwt(token).key)
