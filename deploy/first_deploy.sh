#!/usr/bin/env bash
# 첫 배포 (한 번): VM 확인 -> 배포 폴더·.env(비밀 생성)·backend.env -> 이미지 빌드·전송 -> 길찾기 볼륨 전송
# -> compose up·헬스 체크 -> 계정 만들기 (deploy/users.txt). 이후 업데이트는 deploy/update.sh
#   deploy/first_deploy.sh [--vm user@host] [--port 8000]
# 준비 (노트북): deploy/deploy.conf, deploy/backend.env (backend.env.example 복사, 키 입력), (선택) deploy/users.txt
#                길찾기 볼륨 guryongmate-valhalla (개발 compose 로 valhalla 를 한 번 띄우면 생긴다)
# 다시 실행해도 된다: .env·볼륨·계정은 있으면 그대로 둔다. docs/deploy/README.md §2
source "$(dirname "$0")/lib.sh"
parse_common "$@"
need_vm
PY=$(command -v python3 || command -v python) || die "python 이 필요합니다"

log "VM 확인 ($VM)"
vm 'docker version -f "docker {{.Server.Version}}" && docker compose version --short && command -v curl >/dev/null && df -h / | tail -1' \
  || die "VM 에 docker·compose·curl 이 필요합니다"

# 배포 폴더와 .env (compose 값·비밀). 비밀은 노트북에서 만들어 VM 에만 둔다 (관리 토큰은 deploy/admin_token.sh 로 본다)
vm "mkdir -p ~/$VM_DIR && chmod 700 ~/$VM_DIR"
if vm "test -f ~/$VM_DIR/.env"; then
  ok ".env 가 이미 있어 그대로 둡니다"
else
  secret() { "$PY" -c 'import secrets; print(secrets.token_hex(32))'; }
  { echo "# 운영 compose 값 (deploy/first_deploy.sh 가 만듦). IMAGE_TAG·PREV_TAG 는 update.sh 가 바꾼다"
    echo "IMAGE_TAG="; echo "PREV_TAG="
    echo "API_PORT=$API_PORT"
    echo "JWT_SECRET=$(secret)"
    echo "GATEWAY_ADMIN_TOKEN=$(secret)"
    echo "DB_PASSWORD=$(secret)"
    echo "PLANNER_SAFETY_WATCH_SEC=10"
    echo "TRIP_TICK_SEC=30"
  } | tr -d '\r' | vm "umask 077 && cat > ~/$VM_DIR/.env"
  ok ".env 를 만들었습니다 (JWT_SECRET·GATEWAY_ADMIN_TOKEN·DB_PASSWORD 새로 생성)"
fi

# backend.env (서비스 키·설정). 노트북의 deploy/backend.env 가 있으면 올린다
if [[ -f "$DEPLOY_DIR/backend.env" ]]; then
  tr -d '\r' < "$DEPLOY_DIR/backend.env" | vm "umask 077 && cat > ~/$VM_DIR/backend.env"
  ok "backend.env 를 올렸습니다"
elif vm "test -f ~/$VM_DIR/backend.env"; then
  ok "VM 의 backend.env 를 그대로 씁니다"
else
  die "deploy/backend.env 가 없습니다. deploy/backend.env.example 을 복사해 키를 채우세요"
fi

# 이미지
TAG=$("$DEPLOY_DIR/build.sh" | tail -1)
"$DEPLOY_DIR/transfer.sh" --vm "$VM" "$TAG"

# 길찾기 볼륨: 노트북 볼륨에서 실행에 필요한 파일만 (타일 묶음·설정·행정구역, 약 25MB) 스트림으로. OSM 원본·고도 원본은 안 보낸다
if vm "docker volume inspect guryongmate-valhalla >/dev/null 2>&1"; then
  ok "VM 에 길찾기 볼륨이 이미 있습니다 (다시 보내려면: docker volume rm guryongmate-valhalla 후 재실행)"
else
  docker run --rm -v guryongmate-valhalla:/v:ro --entrypoint test "guryongmate/valhalla:$TAG" -f /v/valhalla_tiles.tar \
    || die "노트북에 길찾기 타일이 없습니다. 개발 compose 로 한 번 만드세요: docker compose up -d valhalla (약 3분)"
  log "길찾기 볼륨 전송"
  docker run --rm -v guryongmate-valhalla:/v:ro --entrypoint tar "guryongmate/valhalla:$TAG" \
      -czf - -C /v valhalla.json valhalla_tiles.tar admins.sqlite \
    | vm "docker volume create guryongmate-valhalla >/dev/null && docker run --rm -i -v guryongmate-valhalla:/v --entrypoint tar guryongmate/valhalla:$TAG -xzf - -C /v"
  ok "길찾기 볼륨 ($(vm "docker run --rm -v guryongmate-valhalla:/v --entrypoint du guryongmate/valhalla:$TAG -sh /v | cut -f1"))"
fi

# compose up
vm "cat > ~/$VM_DIR/docker-compose.prod.yml" < "$ROOT/docker-compose.prod.yml"
vmc "sed -i 's/^IMAGE_TAG=.*/IMAGE_TAG=$TAG/' .env"
log "compose up (첫 실행은 장소 임베딩 적재로 1~2분)"
vm_compose up -d --remove-orphans --wait --wait-timeout 600 || { vm_compose ps -a >&2; vm_compose logs --tail 40 >&2; die "compose up 실패"; }
vm_health || die "헬스 체크 실패 (deploy/status.sh)"

# 계정: deploy/users.txt 한 줄에 '아이디 비밀번호 [이름]'
if [[ -f "$DEPLOY_DIR/users.txt" ]]; then
  log "계정 만들기 (deploy/users.txt)"
  grep -vE '^\s*(#|$)' "$DEPLOY_DIR/users.txt" | tr -d '\r' | while read -r login pw name; do
    "$DEPLOY_DIR/create_user.sh" --vm "$VM" --port "$API_PORT" "$login" "$pw" "${name:-$login}" </dev/null || true
  done
fi
ok "첫 배포 완료: $TAG  —  앱 서버 주소 http://${VM#*@}:$API_PORT (GCP 방화벽에서 tcp:$API_PORT 를 열어야 한다, docs/deploy/README.md §6)"
