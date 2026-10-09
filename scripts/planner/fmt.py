"""CLI 출력 도우미 (cli.py·services_cli.py 공용): 한글 폭을 맞춘 표, --json 출력."""
import json
import unicodedata
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

LEVEL = {"safe": "안전", "caution": "주의", "danger": "위험", "info": "참고", None: "판단 안 함"}


def width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s: str, n: int) -> str:
    return s + " " * max(0, n - width(s))


def table(headers: list[str], rows: list[list[str]], indent: str = "  ") -> None:
    cols = [max(width(str(x)) for x in col) for col in zip(headers, *rows)]
    for row in [headers, *rows]:
        print(indent + "  ".join(pad(str(x), w) for x, w in zip(row, cols)).rstrip())


def short(text: str | None, n: int = 60) -> str:
    """한 줄로 줄이고 n 자에서 자른다."""
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def dump(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def hhmm(iso: str | None) -> str:
    """ISO 시각 -> HH:MM (없으면 -)."""
    return iso[11:16] if iso and len(iso) >= 16 else (iso or "-")


def when(iso: str | None) -> str:
    """기록 시각(UTC 일 수 있음) -> 한국 시각 'MM-DD HH:MM' (없으면 -)."""
    if not iso:
        return "-"
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso[:16]
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(KST).strftime("%m-%d %H:%M")


def latlng(s: str) -> tuple[float, float]:
    """'35.99,129.56' -> (35.99, 129.56)."""
    try:
        lat, lng = (float(x) for x in s.split(","))
    except ValueError:
        raise SystemExit(f"좌표는 LAT,LNG 형식으로 주세요 (예: 35.9906,129.5604): {s}")
    return lat, lng
