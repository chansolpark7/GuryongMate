# deploy/ 스크립트 공용 (source 해서 쓴다). 노트북(Git Bash·macOS·Linux)에서 돈다. docs/deploy/README.md
# 설정: deploy/deploy.conf (커밋 안 함, deploy/deploy.conf.example 복사) 또는 인자 --vm user@host
set -euo pipefail
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'   # Git Bash 가 docker -v /path 를 Windows 경로로 바꾸지 않게

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DEPLOY_DIR/.." && { pwd -W 2>/dev/null || pwd; })"   # Git Bash: C:/... (git·docker 가 Windows 프로그램)
[[ -f "$DEPLOY_DIR/deploy.conf" ]] && source "$DEPLOY_DIR/deploy.conf"
VM="${VM:-}"                    # user@host
VM_DIR="${VM_DIR:-guryongmate}" # VM 홈 아래 폴더
API_PORT="${API_PORT:-8000}"
SSH_OPTS="${SSH_OPTS:--o ServerAliveInterval=30 -o ConnectTimeout=15}"
KEEP_LOCAL_IMAGES="${KEEP_LOCAL_IMAGES:-2}"

# 이미지: 이름 -> 빌드 (Dockerfile, 컨텍스트, 소스 경로들). 소스 경로의 git 트리 해시를 라벨(guryong.src)로 붙여
# VM 에 같은 소스로 만든 이미지가 있으면 다시 보내지 않고 태그만 붙인다.
IMAGES=(backend embedding-onnx gateway db valhalla)
image_dockerfile() { case $1 in
  backend) echo backend/Dockerfile.prod ;; embedding-onnx) echo services/embedding-onnx/Dockerfile ;;
  gateway) echo infra/gateway/Dockerfile ;; db) echo services/db/Dockerfile ;; valhalla) echo services/valhalla/Dockerfile.prod ;; esac; }
image_context() { case $1 in
  backend) echo . ;; embedding-onnx) echo services/embedding-onnx ;; gateway) echo infra/gateway ;;
  db) echo services/db ;; valhalla) echo services/valhalla ;; esac; }
image_sources() { case $1 in
  backend) echo backend data/places/snapshot data/places/manual scripts ;; *) image_context "$1" ;; esac; }

log()  { printf '\033[36m==>\033[0m %s\n' "$*" >&2; }
ok()   { printf '\033[32m OK\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[33m !!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31mERR\033[0m %s\n' "$*" >&2; exit 1; }

# 공통 인자: --vm user@host, --port N
parse_common() {
  ARGS=()
  while [[ $# -gt 0 ]]; do
    case $1 in
      --vm) VM=$2; shift 2 ;;
      --port) API_PORT=$2; shift 2 ;;
      *) ARGS+=("$1"); shift ;;
    esac
  done
}

need_vm() { [[ -n "$VM" ]] || die "VM 주소가 없습니다. --vm user@host 또는 deploy/deploy.conf 의 VM=..."; }
# VM 에서 명령. VM=local 이면 노트북에서 그대로 돈다 (스크립트 시험용: VM_DIR 를 따로 두고, 그 .env 에
# COMPOSE_PROJECT_NAME·VALHALLA_VOLUME 을 넣어 개발 스택과 겹치지 않게. docs/deploy/README.md §9)
vm() { if [[ "$VM" == local ]]; then bash -c "$*"; else ssh $SSH_OPTS "$VM" "$@"; fi; }
vmc() { vm "cd ~/$VM_DIR && $*"; }                      # VM 의 배포 폴더에서
vm_compose() { vmc "docker compose -f docker-compose.prod.yml $*"; }
vm_env_get() { vmc "grep -E '^$1=' .env | tail -1 | cut -d= -f2-" 2>/dev/null || true; }

src_hash() {  # 이미지 소스 경로들의 git 트리 해시 (커밋된 내용 기준)
  local paths h=""
  read -r -a paths <<<"$(image_sources "$1")"
  for p in "${paths[@]}"; do h+="$(git -C "$ROOT" rev-parse "HEAD:$p")"; done
  printf '%s' "$h" | git hash-object --stdin | cut -c1-16
}

# main 브랜치·깨끗한 작업 트리 확인 (서버에는 main 만 배포한다, PROJECT.md §8)
check_main() {
  local branch; branch=$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)
  if [[ "${ALLOW_BRANCH:-0}" != 1 ]]; then
    [[ "$branch" == main ]] || die "main 브랜치가 아닙니다 ($branch). git switch main && git pull 후 다시 (시험용: ALLOW_BRANCH=1)"
    git -C "$ROOT" fetch -q origin main 2>/dev/null || warn "origin 에서 main 을 받지 못했습니다 (오프라인?)"
    if git -C "$ROOT" rev-parse -q --verify origin/main >/dev/null; then
      [[ "$(git -C "$ROOT" rev-parse HEAD)" == "$(git -C "$ROOT" rev-parse origin/main)" ]] \
        || die "로컬 main 이 origin/main 과 다릅니다. git pull / git push 로 맞추세요"
    fi
  fi
  [[ -z "$(git -C "$ROOT" status --porcelain)" ]] || die "커밋하지 않은 변경이 있습니다 (이미지는 커밋된 내용으로 만든다)"
  ok "브랜치 $branch @ $(git -C "$ROOT" rev-parse --short HEAD)"
}

current_tag() { git -C "$ROOT" rev-parse --short=10 HEAD; }

# VM 에서 health 확인 (게이트웨이 /health, 서비스 문서, /agent/info). 실패하면 1
VM_HEALTH_PATHS="/health /places/docs /planner/docs /agent/docs /safety/docs /routing/docs /trip/docs /docent/docs /account/docs /agent/info /routing/status"
vm_health() {
  local tries=${1:-30} out
  out=$(vm "port=$API_PORT; for i in \$(seq 1 $tries); do curl -fsS http://127.0.0.1:\$port/health >/dev/null && break; sleep 2; done
    fail=0; for p in $VM_HEALTH_PATHS; do
      code=\$(curl -s -w '\n%{http_code}' --max-time 10 http://127.0.0.1:\$port\$p | tail -1); echo \"\$code \$p\"; [ \"\$code\" = 200 ] || fail=1
    done
    echo \"agent \$(curl -s --max-time 5 http://127.0.0.1:\$port/agent/info)\"
    echo \"valhalla \$(curl -s --max-time 5 http://127.0.0.1:\$port/routing/status | head -c 200)\"
    exit \$fail") && { printf '%s\n' "$out" >&2; return 0; }
  printf '%s\n' "$out" >&2; return 1
}
