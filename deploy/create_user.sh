#!/usr/bin/env bash
# 팀원·멘토 계정 만들기 (ID/비밀번호). 공개 서버는 가입(POST /auth/signup)을 관리 토큰으로만 열기 때문에 이 스크립트로 만든다.
#   deploy/create_user.sh [--vm user@host] <아이디> <비밀번호> [이름]
# VM 안에서 게이트웨이(localhost)로 부르고, 관리 토큰은 VM 의 .env 에서 읽는다 (노트북에 토큰이 없어도 됨).
# 비밀번호 규칙: docs/account/README.md §3.1. 이미 있는 아이디면 409 login_id_taken.
source "$(dirname "$0")/lib.sh"
parse_common "$@"; set -- "${ARGS[@]}"
need_vm
[[ $# -ge 2 ]] || die "사용법: deploy/create_user.sh <아이디> <비밀번호> [이름]"
PY=$(command -v python3 || command -v python) || die "python 이 필요합니다"
body=$(LOGIN=$1 PW=$2 NAME=${3:-$1} "$PY" -c 'import json, os; print(json.dumps({"login_id": os.environ["LOGIN"], "password": os.environ["PW"], "display_name": os.environ["NAME"]}))' | tr -d '\r')
out=$(printf '%s' "$body" | vm "tok=\$(grep '^GATEWAY_ADMIN_TOKEN=' ~/$VM_DIR/.env | cut -d= -f2-)
  curl -s -w '\n%{http_code}' -X POST -H 'content-type: application/json' -H \"X-Admin-Token: \$tok\" --data-binary @- http://127.0.0.1:$API_PORT/auth/signup")
code=${out##*$'\n'}
case $code in
  201) ok "계정 $1 을 만들었습니다" ;;
  409) warn "계정 $1 은 이미 있습니다" ;;
  *) die "계정 $1 을 만들지 못했습니다 ($code): ${out%$'\n'*}" ;;
esac
