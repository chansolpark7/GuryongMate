# 임베딩 서버 경량판 (서버 배포용). 같은 모델(intfloat/multilingual-e5-base)을 ONNX 로 바꿔 torch 없이 돌린다.
#   docker build -t guryongmate-embedding-onnx services/embedding-onnx                       # emb-int8 (기본, 약 530MB 모델)
#   docker build --build-arg QUANTIZE=int8 -t ... services/embedding-onnx                    # 전부 int8 (품질 손실, export.py)
# 1단계(export)에서 torch 로 내보내고, 2단계는 onnxruntime + tokenizers 와 모델 파일만 담는다. 자세한 내용: app.py, docs/deploy/README.md

FROM python:3.12-slim AS export
WORKDIR /build
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/tmp/hf
COPY requirements-export.txt .
RUN pip install --no-cache-dir -r requirements-export.txt
COPY export.py .
ARG QUANTIZE=emb-int8
RUN python export.py --out /model --quantize ${QUANTIZE} && rm -rf /tmp/hf

FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MODEL_DIR=/model
COPY requirements.txt .
RUN pip install --no-cache-dir --no-deps -r requirements.txt && python -c "import onnxruntime, tokenizers, fastapi, uvicorn"
COPY --from=export /model /model
COPY app.py .
HEALTHCHECK --interval=5s --timeout=3s --retries=60 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:80/health')"
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "80"]
