"""대시보드 계정 초기화([데모 시작 상태로])가 쓰는 계획 사본이 재생 파일의 계획과 같은지 확인한다 (docs/console/redesign.md §3).
    python scripts/demo/check_console_plan.py      # 같으면 0, 다르면 1 — 다르면 아래 '고치기' 한 줄로 다시 복사
고치기: python -c "import json;p=json.load(open('scripts/trip/scenes/scene4_safety.json',encoding='utf-8'))['plan'];open('infra/gateway/console/demo_plan.json','w',encoding='utf-8',newline='\n').write(json.dumps(p,ensure_ascii=False,indent=2)+'\n')"
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
scene = json.loads((ROOT / "scripts/trip/scenes/scene4_safety.json").read_text(encoding="utf-8"))["plan"]
copy = json.loads((ROOT / "infra/gateway/console/demo_plan.json").read_text(encoding="utf-8"))
if scene != copy:
    sys.exit("다름: infra/gateway/console/demo_plan.json 을 scripts/trip/scenes/scene4_safety.json 의 plan 으로 다시 복사하세요")
print("같음: demo_plan.json == scene4_safety.json plan")
