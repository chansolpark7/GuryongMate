#!/usr/bin/env bash
# 이미지를 VM 으로 스트림 전송한다: docker save | gzip | ssh 'gunzip | docker load' (VM 에 tar 파일을 남기지 않음)
# 같은 소스(라벨 guryong.src)로 만든 이미지가 VM 에 이미 있으면 보내지 않고 태그만 붙인다.
#   deploy/transfer.sh <tag> [이미지 이름...]     (기본: 5개 모두)
source "$(dirname "$0")/lib.sh"
parse_common "$@"; set -- "${ARGS[@]}"
need_vm
TAG=${1:?태그}; shift
names=("$@"); [[ ${#names[@]} -gt 0 ]] || names=("${IMAGES[@]}")
for name in "${names[@]}"; do
  img="guryongmate/$name:$TAG"
  src=$(docker image inspect -f '{{index .Config.Labels "guryong.src"}}' "$img") || die "$img 이 없습니다 (deploy/build.sh 먼저)"
  # VM 에 같은 소스 라벨의 이미지가 있으면 태그만 (예: 백엔드만 바뀐 업데이트면 임베딩·DB·길찾기 이미지는 안 보낸다)
  have=$(vm "docker image ls 'guryongmate/$name' --format '{{.Repository}}:{{.Tag}}' | while read -r i; do
      [ \"\$(docker image inspect -f '{{index .Config.Labels \"guryong.src\"}}' \$i)\" = '$src' ] && echo \$i && break; done" || true)
  if [[ -n "$have" ]]; then
    [[ "$have" == "$img" ]] || vm "docker tag $have $img"
    ok "$img: VM 에 같은 이미지가 있어 태그만 ($have)"
    continue
  fi
  log "$img 전송 중…"
  start=$(date +%s)
  docker save "$img" | gzip -1 | vm 'gunzip | docker load -q' >/dev/null || die "$img 전송 실패"
  ok "$img 전송 ($(( $(date +%s) - start ))초)"
done
