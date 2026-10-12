"""테스트는 서비스 컨테이너 종류와 상관없이 전체 모듈을 한 앱으로 띄운다 (모듈 간 HTTP 호출을 ASGI 로 대체하기 위함)."""
import os

os.environ["SERVICE"] = "all"
os.environ["PLANNER_SAFETY_WATCH_SEC"] = "0"  # 백그라운드 주기 점검은 끈다 (테스트가 직접 tick 을 부른다)
# 계획 삭제 뒤 agent·docent 정리 호출이 compose 의 게이트웨이(실행 중인 스택)로 가지 않게 닫힌 포트로 (바로 실패 -> 로그만).
# 정리까지 보는 테스트는 get_plan_cleaner 를 같은 앱으로 바꾼다 (tests/planner/test_plans.py)
os.environ["PLANNER_SERVICES_BASE_URL"] = "http://127.0.0.1:9"
os.environ["TRIP_TICK_SEC"] = "0"  # trip 백그라운드 틱도 끈다 (compose 의 trip 컨테이너에서 테스트를 돌려도)
for _kind in ("CHAT", "EVENT", "GUIDE"):  # 운영 설정(backend.env)의 LLM 호출 수 제한은 끈다 (test_limits 가 직접 켠다)
    for _win in ("HOUR", "DAY"):
        os.environ[f"AGENT_LIMIT_{_kind}_{_win}"] = "0"
# 가이드 자동 준비는 끈다 (제안 승인 뒤 백그라운드 작업이 대본 LLM 을 먹지 않게. test_guide_auto 가 직접 켠다)
os.environ["AGENT_GUIDE_AUTO"] = "false"
