from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


def init_db() -> None:
    """확장 설치 후 이 프로세스가 맡은 모듈의 테이블 생성. 모델은 main.py 에서 SERVICE 에 맞게 import 된다.

    서비스 컨테이너 여러 개가 동시에 시작해도 충돌하지 않도록 DB advisory lock 으로 순서대로 실행한다.
    """
    with engine.begin() as conn:
        conn.exec_driver_sql("SELECT pg_advisory_xact_lock(7316)")
        conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        Base.metadata.create_all(conn)
