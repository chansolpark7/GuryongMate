# 길찾기 엔진 (Valhalla). 내부 전용, routing 서비스가 호출한다.
# 첫 실행 때 OSM 원본(대한민국)을 받아 포항 영역만 잘라 그래프 타일을 만든다. 자세한 내용: docs/routing/README.md
FROM ghcr.io/valhalla/valhalla-scripted:3.9.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends osmium-tool \
    && rm -rf /var/lib/apt/lists/*

COPY prepare.sh /valhalla/scripts/prepare.sh
RUN chmod +x /valhalla/scripts/prepare.sh

ENTRYPOINT ["/valhalla/scripts/prepare.sh"]
CMD ["build_tiles"]
