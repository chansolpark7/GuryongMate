"""임베딩 클라이언트. 모델(intfloat/multilingual-e5-base, 768차원)은 embedding 컨테이너
(Hugging Face text-embeddings-inference)에 항상 떠 있고, 여기서는 HTTP 로 벡터만 받아온다.
"""
import re

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

MODEL_NAME = "intfloat/multilingual-e5-base"
DIM = 768

CHUNK_CHARS = 500  # e5 입력 한도(512 토큰) 안에 들어가도록 한국어 기준 여유 있게
CHUNK_OVERLAP = 100
BATCH = 32  # TEI 기본 max-client-batch-size


class EmbeddingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="EMBEDDING_", extra="ignore")

    url: str = "http://embedding:80"
    timeout_sec: float = 60


settings = EmbeddingSettings()


class EmbeddingUnavailable(RuntimeError):
    pass


def _embed(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    try:
        with httpx.Client(base_url=settings.url, timeout=settings.timeout_sec) as client:
            for i in range(0, len(texts), BATCH):
                r = client.post("/embed", json={"inputs": texts[i:i + BATCH], "normalize": True, "truncate": True})
                r.raise_for_status()
                vectors += r.json()
    except httpx.HTTPError as e:
        raise EmbeddingUnavailable(f"임베딩 서버({settings.url}) 호출 실패: {e}") from e
    return vectors


def encode_passages(texts: list[str]) -> list[list[float]]:
    # e5 계열은 문서에 "passage: ", 질의에 "query: " 접두어를 붙여야 성능이 나온다
    return _embed([f"passage: {t}" for t in texts])


def encode_query(text: str) -> list[float]:
    return _embed([f"query: {text}"])[0]


def split_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """문장 경계를 최대한 살려 size 글자 이하 조각으로 나눈다. 인접 조각은 overlap 만큼 겹친다."""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    sentences = re.split(r"(?<=[.!?。])\s+|\n+", text)
    chunks: list[str] = []
    cur = ""
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(s) > size:  # 아주 긴 문장은 글자 단위로 겹쳐 자름
            if cur:
                chunks.append(cur)
                cur = ""
            step = size - overlap
            chunks += [s[i:i + size] for i in range(0, len(s) - overlap, step)]
            continue
        if cur and len(cur) + len(s) + 1 > size:
            chunks.append(cur.strip())
            cur = cur[-overlap:] if overlap else ""
        cur = f"{cur} {s}".strip()
    if cur:
        chunks.append(cur)
    return chunks
