"""테스트는 서비스 컨테이너 종류와 상관없이 전체 모듈을 한 앱으로 띄운다 (모듈 간 HTTP 호출을 ASGI 로 대체하기 위함)."""
import os

os.environ["SERVICE"] = "all"
os.environ["PLANNER_SAFETY_WATCH_SEC"] = "0"  # 백그라운드 주기 점검은 끈다 (테스트가 직접 tick 을 부른다)
