#!/usr/bin/env bash
# 서버 상태: 컨테이너·헬스 체크·디스크·메모리 (발표 T-60 점검에도)
#   deploy/status.sh                전체 상태
#   deploy/status.sh logs <서비스>  그 서비스 로그 최근 100줄 (예: agent, trip, gateway)
#   deploy/status.sh disk           디스크 상세 (이미지·볼륨 실제 크기)
source "$(dirname "$0")/lib.sh"
parse_common "$@"; set -- "${ARGS[@]}"
need_vm
case ${1:-} in
  logs) vm_compose logs --tail "${3:-100}" "${2:?서비스 이름}" ;;
  disk)
    vm 'df -h /; echo; docker system df -v | sed -n "1,40p"; echo; sudo du -sh /var/lib/docker /var/lib/containerd 2>/dev/null' ;;
  *)
    log "태그: $(vm_env_get IMAGE_TAG) (이전 $(vm_env_get PREV_TAG))"
    vm_compose ps -a --format "'table {{.Service}}\t{{.Status}}'"
    vm_health 1 || warn "헬스 체크 실패가 있습니다"
    vm 'echo; df -h / | tail -1; free -m | sed -n 2p; docker system df; echo; docker stats --no-stream --format "{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"' ;;
esac
