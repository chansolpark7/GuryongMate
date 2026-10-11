#!/usr/bin/env bash
# 운영 이미지 5개를 노트북에서 빌드한다 (서버에서는 빌드하지 않는다). 태그 = main 의 커밋 (git short sha)
# 웹 앱도 여기서 빌드해 gateway 이미지에 넣는다 (deploy/build_web.sh — 노트북에 Flutter SDK 필요)
#   deploy/build.sh                 main·깨끗한 작업 트리 확인 후 빌드
#   ALLOW_BRANCH=1 deploy/build.sh  다른 브랜치로 시험 빌드 (로컬 확인용, 배포하지 않는다)
# 결과: guryongmate/{backend,embedding-onnx,gateway,db,valhalla}:<tag>  (라벨 guryong.src = 소스 트리 해시)
source "$(dirname "$0")/lib.sh"
parse_common "$@"
check_main
TAG=$(current_tag)
"$DEPLOY_DIR/build_web.sh"   # 웹 앱 → app/build/web (gateway 이미지에 들어간다)
for name in "${IMAGES[@]}"; do
  log "빌드 guryongmate/$name:$TAG"
  extra=()   # gateway: 웹 앱 빌드 결과를 추가 컨텍스트로 (infra/gateway/Dockerfile 의 COPY --from=webapp)
  [[ "$name" == gateway ]] && extra=(--build-context "webapp=$ROOT/app/build/web")
  docker build -q -f "$ROOT/$(image_dockerfile "$name")" --label "guryong.src=$(src_hash "$name")" "${extra[@]}" \
    -t "guryongmate/$name:$TAG" "$ROOT/$(image_context "$name")" >/dev/null
done
# 노트북에 오래된 태그가 쌓이지 않게: 이미지마다 최근 KEEP_LOCAL_IMAGES 개만 남긴다 (VM=local 시험에서는 VM 쪽 이미지라 남김)
if [[ "$VM" != local ]]; then
  for name in "${IMAGES[@]}"; do
    docker image ls "guryongmate/$name" --format '{{.CreatedAt}}\t{{.Tag}}' | sort -r | tail -n +$((KEEP_LOCAL_IMAGES + 1)) | cut -f2 |
      while read -r old; do [[ "$old" != "$TAG" ]] && docker image rm "guryongmate/$name:$old" >/dev/null 2>&1 || true; done
  done
fi
docker image ls --format '{{.Repository}}:{{.Tag}}\t{{.Size}}' | grep "^guryongmate/.*:$TAG" >&2
ok "빌드 완료: $TAG"
echo "$TAG"
