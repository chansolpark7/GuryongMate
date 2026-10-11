"""Valhalla 회전 안내(maneuver)를 한국어 문구로 바꾼다. Valhalla 는 한국어 안내를 지원하지 않는다.

maneuver type 번호: https://valhalla.github.io/valhalla/api/route/api-reference/ (0~43)
"""

# type 번호 -> (공개 type 이름, 동작 문구). 문구의 {to} 는 "구룡포길로" 처럼 조사가 붙은 길 이름
_TURNS: dict[int, tuple[str, str]] = {
    9: ("slight_right", "오른쪽 방향"), 10: ("right", "우회전"), 11: ("sharp_right", "오른쪽으로 크게 꺾어"),
    16: ("slight_left", "왼쪽 방향"), 15: ("left", "좌회전"), 14: ("sharp_left", "왼쪽으로 크게 꺾어"),
    12: ("uturn", "유턴"), 13: ("uturn", "유턴"),
    18: ("ramp_right", "오른쪽 진입로"), 19: ("ramp_left", "왼쪽 진입로"),
    20: ("exit_right", "오른쪽 진출로"), 21: ("exit_left", "왼쪽 진출로"),
    23: ("keep_right", "오른쪽 길 유지"), 24: ("keep_left", "왼쪽 길 유지"),
    25: ("merge", "합류"), 37: ("merge", "오른쪽에서 합류"), 38: ("merge", "왼쪽에서 합류"),
}
_STRAIGHT = {7, 8, 17, 22}  # becomes, continue, ramp straight, stay straight
_ARRIVE = {4: "목적지 도착", 5: "오른쪽에 목적지", 6: "왼쪽에 목적지"}
_SPECIAL = {
    28: ("ferry", "배 타기"), 29: ("ferry", "배에서 내리기"),
    39: ("elevator", "엘리베이터 이용"), 41: ("escalator", "에스컬레이터 이용"),
    42: ("building", "건물로 들어가기"), 43: ("building", "건물에서 나오기"),
}


def _jong(word: str) -> int | None:
    """마지막 글자의 받침 번호 (0 = 받침 없음). 한글이 아니면 None."""
    c = word.strip()[-1:]
    if not c or not "가" <= c <= "힣":
        return None
    return (ord(c) - 0xAC00) % 28


def ro(word: str) -> str:
    """'구룡포길' -> '구룡포길로', '호미로' -> '호미로로', '해안도로' -> '해안도로로', '입구' -> '입구로'."""
    j = _jong(word)
    return word + ("로" if j in (None, 0, 8) else "으로")  # 8 = ㄹ 받침


def eul(word: str) -> str:
    j = _jong(word)
    return word + ("를" if j in (None, 0) else "을")


def fmt_distance(m: float) -> str:
    if m >= 1000:
        return f"{m / 1000:.1f}km"
    return f"{max(10, round(m / 10) * 10)}m"


def describe(m: dict) -> tuple[str, str]:
    """(공개 type 이름, 한국어 문구). m 은 Valhalla maneuver (length 단위 km)."""
    t = m.get("type", 0)
    names = m.get("street_names") or m.get("begin_street_names") or []
    street = names[0] if names else None
    dist = fmt_distance(float(m.get("length", 0)) * 1000)

    if t in _ARRIVE:
        return "arrive", _ARRIVE[t]
    if t in (1, 2, 3):
        return "start", f"{ro(street)} 출발해 {dist} 이동" if street else f"출발해 {dist} 이동"
    if t in _STRAIGHT or t == 0:
        return "straight", f"{eul(street)} 따라 {dist} 직진" if street else f"{dist} 직진"
    if t in _TURNS:
        key, act = _TURNS[t]
        return key, f"{ro(street)} {act} 후 {dist} 이동" if street else f"{act} 후 {dist} 이동"
    if t == 40:
        return "steps", f"계단으로 {dist} 이동"
    if t == 26:
        n = m.get("roundabout_exit_count")
        return "roundabout", f"회전교차로에서 {n}번째 출구" if n else "회전교차로 진입"
    if t == 27:
        return "roundabout_exit", f"회전교차로에서 {ro(street)} 나가 {dist} 이동" if street else f"회전교차로에서 나가 {dist} 이동"
    if t in _SPECIAL:
        return _SPECIAL[t]
    return "other", f"{dist} 이동"
