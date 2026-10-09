#!/usr/bin/env bash
# 이전 배포(.env 의 PREV_TAG)로 되돌린다. 이미지는 VM 에 남아 있다 (update.sh 가 현재·이전 태그를 남김).
#   deploy/rollback.sh [--vm user@host]
# 현재·이전 태그(와 compose 파일)를 맞바꾼다 — 다시 실행하면 앞으로. 그보다 이전 커밋은 정리돼 없다 -> main 을 되돌려(git revert) update.sh
source "$(dirname "$0")/lib.sh"
parse_common "$@"
need_vm
CUR=$(vm_env_get IMAGE_TAG); PREV=$(vm_env_get PREV_TAG)
[[ -n "$PREV" ]] || die "되돌릴 이전 태그(PREV_TAG)가 없습니다"
vm "docker image inspect guryongmate/backend:$PREV >/dev/null" || die "VM 에 $PREV 이미지가 없습니다"
log "되돌리기: $CUR -> $PREV"
vmc "if test -f docker-compose.prod.yml.prev; then mv docker-compose.prod.yml c.tmp && mv docker-compose.prod.yml.prev docker-compose.prod.yml && mv c.tmp docker-compose.prod.yml.prev; fi
     sed -i -e 's/^IMAGE_TAG=.*/IMAGE_TAG=$PREV/' -e 's/^PREV_TAG=.*/PREV_TAG=$CUR/' .env"
if vm_compose up -d --remove-orphans --wait --wait-timeout 300 && vm_health; then
  ok "되돌림 완료: $PREV (다시 앞으로: deploy/rollback.sh)"
else
  die "되돌림 후 헬스 체크 실패. deploy/status.sh 로 확인하세요"
fi
