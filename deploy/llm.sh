#!/usr/bin/env bash
# Agent LLM 바꾸기 (발표 중 비상: Claude 오류·크레딧 부족 -> 각본 LLM). agent 컨테이너만 다시 만든다 (약 6~8초)
#   deploy/llm.sh scripted     각본 LLM (LLM 호출 없음, 데모 문장만. docs/agent/README.md §7.2)
#   deploy/llm.sh claude       Claude API 로 되돌리기
source "$(dirname "$0")/lib.sh"
parse_common "$@"; set -- "${ARGS[@]}"
need_vm
p=${1:?scripted | claude | openai | local}
vmc "grep -q '^AGENT_PROVIDER=' backend.env && sed -i 's/^AGENT_PROVIDER=.*/AGENT_PROVIDER=$p/' backend.env || echo 'AGENT_PROVIDER=$p' >> backend.env"
vm_compose up -d --wait agent >/dev/null
vm "curl -s http://127.0.0.1:$API_PORT/agent/info"; echo
