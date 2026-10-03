"""테스트는 서비스 컨테이너 종류와 상관없이 전체 모듈을 한 앱으로 띄운다 (모듈 간 HTTP 호출을 ASGI 로 대체하기 위함)."""
import os

os.environ["SERVICE"] = "all"
