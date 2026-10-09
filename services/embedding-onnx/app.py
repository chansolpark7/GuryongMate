"""임베딩 모델 서버 (경량판, 서버 배포용). services/embedding/ 과 같은 API(/embed, /health, /info)·같은 모델이다.

intfloat/multilingual-e5-base 를 ONNX(기본: 단어 임베딩 표만 int8, export.py)로 바꿔 onnxruntime + tokenizers 로 돌린다.
torch 가 없고 모델 파일이 이미지 안에 있어 첫 실행 다운로드·모델 볼륨이 없다 (이미지 1.9GB + 모델 1.1GB -> 약 0.8GB).
sentence-transformers 와 같은 계산: 512 토큰에서 자르기 -> last_hidden_state 평균 풀링(패딩 제외) -> L2 정규화.
품질 비교(torch 서버와 places 검색 상위 결과 겹침): docs/deploy/README.md §1.2
"""
import json
import os
from pathlib import Path

import numpy as np
import onnxruntime as ort
from fastapi import FastAPI
from pydantic import BaseModel
from tokenizers import Tokenizer

MODEL_DIR = Path(os.environ.get("MODEL_DIR", "/model"))
BATCH = 16

meta = json.loads((MODEL_DIR / "meta.json").read_text())
tokenizer = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
tokenizer.enable_padding(pad_id=meta["pad_token_id"], pad_token=meta["pad_token"])

opts = ort.SessionOptions()
if threads := int(os.environ.get("EMBEDDING_THREADS", "0")):  # 0: onnxruntime 기본 (코어 수)
    opts.intra_op_num_threads = threads
# 시작할 때 로드한다. 로드가 끝나야 /health 가 응답하므로 compose healthcheck 로 준비 여부를 알 수 있다.
session = ort.InferenceSession(str(MODEL_DIR / "model.onnx"), opts, providers=["CPUExecutionProvider"])
app = FastAPI(title="GuryongMate embedding (onnx)")


class EmbedRequest(BaseModel):
    inputs: str | list[str]
    normalize: bool = True
    truncate: bool = True  # 모델 최대 길이(512 토큰)를 넘으면 자른다 (sentence-transformers 와 같이 항상 자른다)


def _encode(texts: list[str], normalize: bool) -> np.ndarray:
    tokenizer.enable_truncation(max_length=meta["max_seq_length"])
    enc = tokenizer.encode_batch(texts)
    ids = np.array([e.ids for e in enc], dtype=np.int64)
    mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
    hidden = session.run(["last_hidden_state"], {"input_ids": ids, "attention_mask": mask})[0]
    m = mask[..., None].astype(np.float32)
    vec = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
    if normalize:
        vec /= np.clip(np.linalg.norm(vec, axis=1, keepdims=True), 1e-12, None)
    return vec


@app.post("/embed")
def embed(req: EmbedRequest) -> list[list[float]]:
    texts = [req.inputs] if isinstance(req.inputs, str) else req.inputs
    out = [_encode(texts[i:i + BATCH], req.normalize) for i in range(0, len(texts), BATCH)]
    return np.concatenate(out).tolist() if out else []


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/info")
def info() -> dict[str, object]:
    return {"model_id": meta["model_id"], "dim": meta["dim"], "max_input_length": meta["max_seq_length"],
            "runtime": f"onnxruntime {ort.__version__}", "quantize": meta["quantize"]}
