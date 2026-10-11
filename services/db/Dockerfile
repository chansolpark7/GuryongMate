# 운영 DB 이미지: postgres:16-alpine + pgvector (서버 배포용, docker-compose.prod.yml).
# 개발 compose 의 pgvector/pgvector:pg16(Debian, 약 630MB) 대신 약 280MB. 같은 PostgreSQL 16 + pgvector 0.8.
# OPTFLAGS="" : 빌드한 노트북 CPU 전용 명령(-march=native)을 쓰지 않는다 (VM CPU 가 달라도 돌게)
FROM postgres:16-alpine AS build
ARG PGVECTOR_VERSION=0.8.0
RUN apk add --no-cache build-base curl \
    && curl -fsSL https://github.com/pgvector/pgvector/archive/refs/tags/v${PGVECTOR_VERSION}.tar.gz | tar xz -C /tmp \
    && cd /tmp/pgvector-${PGVECTOR_VERSION} \
    && make OPTFLAGS="" with_llvm=no \
    && make install with_llvm=no DESTDIR=/out

FROM postgres:16-alpine
COPY --from=build /out/usr/local/ /usr/local/
