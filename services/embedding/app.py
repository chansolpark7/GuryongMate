"""임베딩 모델 서버. Hugging Face text-embeddings-inference(TEI)와 같은 API(/embed, /health, /info)를 제공한다.

TEI(ONNX)는 모델 로드 시 메모리를 3GB 넘게 써서 Docker Desktop 기본 한도(약 4GB)에서 OOM 이 난다.
sentence-transformers 로 같은 모델을 띄우면 상주 약 0.7GB 라 이 서버를 쓴다.
메모리가 넉넉해지면 compose 의 embedding 이미지를 TEI 로 바꿔도 클라이언트 코드는 그대로 동작한다.
"""
import os

from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

MODEL_ID = os.environ.get("MODEL_ID", "intfloat/multilingual-e5-base")

# 시작할 때 로드한다. 로드가 끝나야 /health 가 응답하므로 compose healthcheck 로 준비 여부를 알 수 있다.
model = SentenceTransformer(MODEL_ID, device="cpu")
app = FastAPI(title="GuryongMate embedding")


class EmbedRequest(BaseModel):
    inputs: str | list[str]
    normalize: bool = True
    truncate: bool = True  # 모델 최대 길이(512 토큰)를 넘으면 자른다 (sentence-transformers 기본 동작)


@app.post("/embed")
def embed(req: EmbedRequest) -> list[list[float]]:
    texts = [req.inputs] if isinstance(req.inputs, str) else req.inputs
    return model.encode(texts, normalize_embeddings=req.normalize, batch_size=16).tolist()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/info")
def info() -> dict[str, object]:
    return {"model_id": MODEL_ID, "dim": model.get_sentence_embedding_dimension(),
            "max_input_length": model.max_seq_length}
