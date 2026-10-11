#!/usr/bin/env bash
# 발표 후 정리.
#   deploy/teardown.sh            컨테이너만 내린다 (데이터·이미지는 남김 — 다시 up 가능)
#   deploy/teardown.sh --all      컨테이너·DB 볼륨·길찾기 볼륨·이미지·배포 폴더(.env·키)까지 모두 지운다 (되돌릴 수 없음)
# VM 반납·삭제와 방화벽 규칙 삭제는 GCP 콘솔에서 (docs/deploy/README.md §8)
source "$(dirname "$0")/lib.sh"
parse_common "$@"; set -- "${ARGS[@]}"
need_vm
if [[ "${1:-}" == --all ]]; then
  read -r -p "VM($VM)의 GuryongMate 데이터(계정·계획·대화)·이미지·비밀을 모두 지웁니다. 계속하려면 'delete' 입력: " a
  [[ "$a" == delete ]] || die "취소했습니다"
  vm_compose down -v --remove-orphans || true
  vm "docker volume rm guryongmate-valhalla 2>/dev/null; docker image ls 'guryongmate/*' -q | sort -u | xargs -r docker image rm -f >/dev/null; docker image prune -f >/dev/null; rm -rf ~/$VM_DIR; df -h / | tail -1"
  ok "모두 지웠습니다"
else
  vm_compose down --remove-orphans
  ok "컨테이너를 내렸습니다 (다시 띄우기: ssh 후 cd ~/$VM_DIR && docker compose -f docker-compose.prod.yml up -d)"
fi
