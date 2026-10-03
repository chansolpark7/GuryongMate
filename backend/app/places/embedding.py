"""로컬 임베딩 (intfloat/multilingual-e5-base, 768차원, CPU).

모델은 처음 사용할 때 한 번만 로드한다 (약 1.1GB 다운로드, data/places/models/ 에 캐시).
키워드 검색만 쓰는 경우에는 로드되지 않는다.
"""
import os
import re
from functools import lru_cache
from pathlib import Path

MODEL_NAME = "intfloat/multilingual-e5-base"
DIM = 768
MODEL_DIR = Path(__file__).resolve().parents[3] / "data" / "places" / "models"

CHUNK_CHARS = 500  # e5 입력 한도(512 토큰) 안에 들어가도록 한국어 기준 여유 있게
CHUNK_OVERLAP = 100


@lru_cache(maxsize=1)
def _model():
    os.environ.setdefault("HF_HOME", str(MODEL_DIR))
    from sentence_transformers import SentenceTransformer  # 무거운 import 는 필요할 때만

    return SentenceTransformer(MODEL_NAME, device="cpu")


def encode_passages(texts: list[str]) -> list[list[float]]:
    # e5 계열은 문서에 "passage: ", 질의에 "query: " 접두어를 붙여야 성능이 나온다
    vecs = _model().encode([f"passage: {t}" for t in texts], normalize_embeddings=True, batch_size=16)
    return [v.tolist() for v in vecs]


def encode_query(text: str) -> list[float]:
    return _model().encode(f"query: {text}", normalize_embeddings=True).tolist()


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
