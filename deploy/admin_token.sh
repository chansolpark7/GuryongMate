#!/usr/bin/env bash
# 관리 토큰을 보여준다 (발표 진행 스크립트·CLI 용). 화면에만 출력하고 저장하지 않는다.
#   export GURYONG_ADMIN_TOKEN=$(deploy/admin_token.sh)
#   python scripts/demo/run_demo.py --url http://<VM>:8000          (환경변수를 읽는다)
source "$(dirname "$0")/lib.sh"
parse_common "$@"
need_vm
tok=$(vm_env_get GATEWAY_ADMIN_TOKEN)
[[ -n "$tok" ]] || die "VM 의 .env 에 GATEWAY_ADMIN_TOKEN 이 없습니다"
echo "$tok"
