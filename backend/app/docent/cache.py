"""검색·페이지 결과 캐시 (docent_search_cache). 같은 질의·URL 은 DOCENT_CACHE_TTL_SEC(기본 하루) 동안 다시 쓴다."""
import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.docent.config import settings
from app.docent.models import DocentSearchCache


def cache_key(kind: str, *parts: str) -> str:
    return hashlib.sha256("\x1f".join((kind, *parts)).encode()).hexdigest()


def _cutoff() -> datetime:
    return datetime.now(UTC) - timedelta(seconds=settings.cache_ttl_sec)


def get(db: Session, key: str) -> dict | None:
    row = db.get(DocentSearchCache, key)
    if row is None or row.created_at < _cutoff():
        return None
    return row.payload


def put(db: Session, key: str, kind: str, payload: dict) -> None:
    db.execute(delete(DocentSearchCache).where(DocentSearchCache.created_at < _cutoff()))  # 만료된 것 정리
    stmt = insert(DocentSearchCache).values(key=key, kind=kind, payload=payload, created_at=datetime.now(UTC))
    db.execute(stmt.on_conflict_do_update(index_elements=["key"],
                                          set_={"payload": stmt.excluded.payload, "created_at": stmt.excluded.created_at}))
    db.commit()
