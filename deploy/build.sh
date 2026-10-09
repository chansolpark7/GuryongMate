#!/usr/bin/env bash
# 운영 이미지 5개를 노트북에서 빌드한다 (서버에서는 빌드하지 않는다). 태그 = main 의 커밋 (git short sha)
#   deploy/build.sh                 main·깨끗한 작업 트리 확인 후 빌드
#   ALLOW_BRANCH=1 deploy/build.sh  다른 브랜치로 시험 빌드 (로컬 확인용, 배포하지 않는다)
# 결과: guryongmate/{backend,embedding-onnx,gateway,db,valhalla}:<tag>  (라벨 guryong.src = 소스 트리 해시)
source "$(dirname "$0")/lib.sh"
parse_common "$@"
check_main
TAG=$(current_tag)
for name in "${IMAGES[@]}"; do
  log "빌드 guryongmate/$name:$TAG"
  docker build -q -f "$ROOT/$(image_dockerfile "$name")" --label "guryong.src=$(src_hash "$name")" \
    -t "guryongmate/$name:$TAG" "$ROOT/$(image_context "$name")" >/dev/null
done
# 노트북에 오래된 태그가 쌓이지 않게: 이미지마다 최근 KEEP_LOCAL_IMAGES 개만 남긴다
for name in "${IMAGES[@]}"; do
  docker image ls "guryongmate/$name" --format '{{.CreatedAt}}\t{{.Tag}}' | sort -r | tail -n +$((KEEP_LOCAL_IMAGES + 1)) | cut -f2 |
    while read -r old; do [[ "$old" != "$TAG" ]] && docker image rm "guryongmate/$name:$old" >/dev/null 2>&1 || true; done
done
docker image ls --format '{{.Repository}}:{{.Tag}}\t{{.Size}}' | grep "^guryongmate/.*:$TAG" >&2
ok "빌드 완료: $TAG"
echo "$TAG"
