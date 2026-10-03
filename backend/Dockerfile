FROM python:3.12-slim

WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# 모듈별 requirements/*.txt 를 모두 설치 (모듈끼리 같은 파일을 수정하지 않도록 분리)
COPY requirements/ requirements/
RUN for f in requirements/*.txt; do pip install --no-cache-dir -r "$f"; done

COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
