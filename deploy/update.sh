#!/usr/bin/env bash
# 업데이트 배포 (dev -> main 머지 뒤): ① main 확인 ② 이미지 빌드 ③ VM 으로 스트림 전송 ④ compose up
# ⑤ 헬스 체크 (/health, 서비스 /docs, /agent/info) ⑥ 실패하면 이전 이미지로 되돌린다. 성공하면 현재·이전 태그만 남기고 정리.
#   deploy/update.sh [--vm user@host] [--port 8000]
# 첫 배포는 deploy/first_deploy.sh (볼륨·.env·계정). docs/deploy/README.md §4
source "$(dirname "$0")/lib.sh"
parse_common "$@"
need_vm
vm "test -f ~/$VM_DIR/.env" || die "VM 에 ~/$VM_DIR/.env 가 없습니다. 첫 배포는 deploy/first_deploy.sh"

TAG=$("$DEPLOY_DIR/build.sh" | tail -1)                         # ① ②
OLD=$(vm_env_get IMAGE_TAG)
log "배포: ${OLD:-없음} -> $TAG"
"$DEPLOY_DIR/transfer.sh" --vm "$VM" "$TAG"                      # ③

# ④ compose 파일 교체 (이전 것은 .prev 로 보관) + 태그 바꾸기
vmc "cp docker-compose.prod.yml docker-compose.prod.yml.prev 2>/dev/null || true"
vm "cat > ~/$VM_DIR/docker-compose.prod.yml" < "$ROOT/docker-compose.prod.yml"
vmc "sed -i -e 's/^IMAGE_TAG=.*/IMAGE_TAG=$TAG/' -e 's/^PREV_TAG=.*/PREV_TAG=$OLD/' .env"
log "compose up"
if vm_compose up -d --remove-orphans --wait --wait-timeout 300 && vm_health; then    # ⑤
  ok "배포 성공: $TAG"
else                                                              # ⑥
  warn "헬스 체크 실패. 이전 이미지($OLD)로 되돌립니다. 실패한 서비스 로그:"
  vm_compose ps -a >&2 || true
  vm_compose logs --tail 30 >&2 || true
  [[ -n "$OLD" ]] || die "되돌릴 이전 태그가 없습니다"
  vmc "test -f docker-compose.prod.yml.prev && mv docker-compose.prod.yml.prev docker-compose.prod.yml; \
       sed -i -e 's/^IMAGE_TAG=.*/IMAGE_TAG=$OLD/' -e 's/^PREV_TAG=.*/PREV_TAG=/' .env"
  vm_compose up -d --remove-orphans --wait --wait-timeout 300 && vm_health && die "되돌림 완료 ($OLD 로 동작 중). 원인을 고친 뒤 다시 배포하세요"
  die "되돌림도 실패했습니다. deploy/status.sh 로 확인하세요"
fi

# 정리: guryongmate/* 중 현재·이전 태그가 아닌 이미지, 태그 없는 이미지
log "쓰지 않는 이미지 정리"
vm "docker image ls 'guryongmate/*' --format '{{.Repository}}:{{.Tag}}' | grep -v -e ':$TAG\$' ${OLD:+-e ':$OLD\$'} | xargs -r docker image rm >/dev/null; docker image prune -f >/dev/null; df -h / | tail -1"
