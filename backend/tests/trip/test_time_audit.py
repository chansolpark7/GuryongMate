"""여행 중 시간 판정 점검 (docs/trip/audit-2026-10-12.md) — trip 쪽 재현 테스트.

순수 함수(judge)는 DB 없이, 큐·전송은 test_api 와 같은 가짜 서비스·실제 DB 로 본다.
"""
from datetime import datetime

from app.trip import judge
from app.trip.clock import KST

from .test_api import HOUSE, PORT, behind_events, cause, item_id, roomy


def t(hhmm: str) -> datetime:
    return datetime.fromisoformat(f"2026-10-10T{hhmm}:00").replace(tzinfo=KST)


def _item(i: int, title: str, start: str, end: str, stay: int, status: str = "planned") -> dict:
    return {"id": i, "place_id": f"p{i}", "place": {"title": title}, "start_at": start, "end_at": end,
            "stay_minutes": stay, "status": status}


# 기준: 가옥거리 12:30~13:30 -> 구룡포항 13:36~14:36 -> 과메기문화관 14:42~15:42
BASE = {"version": 1, "days": [{"day": 1, "end_at": "15:42", "items": [
    _item(1, "가옥거리", "12:30", "13:30", 60, "done"), _item(2, "구룡포항", "13:36", "14:36", 60, "visiting"),
    _item(3, "과메기문화관", "14:42", "15:42", 60)]}]}


# ---------- 7. 밀림 추천안 ----------

def test_extend_stay_absorbs_late_arrival():
    """구룡포항(계획 13:36~14:36)에 14:30 에 늦게 도착해 15:20 까지 머묾. 재계산(규칙 B): 예정 종료(14:36)를 넘겼으니
    구룡포항 14:30~15:20(지금) -> 과메기문화관 15:26 (44분 늦음).
    추천안(체류 늘리기)을 승인하면 구룡포항이 지금 + 15분 이후에 끝나야 한다 — 계획 시작 + 체류 >= 15:35."""
    proj = {"new_violations": [], "delay_minutes": 44,
            "next": {"item_id": 3, "planned_start": "14:42", "eta": "15:26", "start_at": "15:26"},
            "plan": {"days": [{"day": 1, "end_at": "16:26", "items": [
                _item(1, "가옥거리", "12:30", "13:30", 60, "done"), _item(2, "구룡포항", "14:30", "15:20", 60, "visiting"),
                _item(3, "과메기문화관", "15:26", "16:26", 60)]}]}}
    track = judge.Track(state="visiting", item_id=2, since=t("14:30"))
    d = judge.judge_delay(proj, t("15:20"), behind_min=30, plan=BASE, day=1, track=track)
    sg = d.payload["suggest"]
    assert sg["kind"] == "extend_stay" and sg["item_id"] == 2
    assert sg["stay_minutes"] == 120  # 13:36 ~ 15:35 (15:20 + 15) -> 5분 올림. 승인 뒤 재계산 종료 = 계획 시작 + 120 = 15:36


def test_extend_stay_after_early_arrival_counts_from_planned_start():
    """구룡포항에 일찍(13:20, 계획 13:36) 와서 14:40 까지 머묾 -> 재계산 13:20~14:40(지금). 기준 일정은 계획 시작 + 체류에 끝나므로
    체류는 계획 시작부터 센다: 13:36 ~ 14:55 (지금 + 15) -> 80분 (logic-bugs-2026-10-12 T4).
    실제 도착부터 세면(95분) 기준 일정이 15:11 에 끝나 일찍 온 16분만큼 남은 일정이 더 밀린다.
    승인 뒤 재계산은 실제 시작 + 80 = 14:40 이 이미 지나 지금에 끝나므로 늦지 않다."""
    proj = {"new_violations": [], "delay_minutes": 10,
            "next": {"item_id": 3, "planned_start": "14:42", "eta": "14:46", "start_at": "14:46"},
            "plan": {"days": [{"day": 1, "items": [_item(2, "구룡포항", "13:20", "14:40", 60, "visiting"),
                                                   _item(3, "과메기문화관", "14:46", "15:46", 60)]}]}}
    d = judge.judge_delay(proj, t("14:40"), behind_min=5, plan=BASE, day=1,
                          track=judge.Track(state="visiting", item_id=2, since=t("13:20")))
    assert d.payload["suggest"]["stay_minutes"] == 80


def test_extend_stay_early_arrival_t4():
    """재현 (T4): 계획 10:30·60분인 곳에 10:00 도착, 11:45 에 15분 밀림 -> 12:00 에 떠나는 안 = 계획 시작 기준 90분 (전에는 120)."""
    plan = {"version": 3, "days": [{"day": 1, "items": [_item(1, "A", "10:30", "11:30", 60, "visiting"),
                                                        _item(2, "B", "11:36", "12:36", 60)]}]}
    proj = {"new_violations": [], "delay_minutes": 15,
            "next": {"item_id": 2, "planned_start": "11:36", "eta": "11:51", "start_at": "11:51"},
            "plan": {"days": [{"day": 1, "items": [_item(1, "A", "10:00", "11:45", 60, "visiting"),
                                                   _item(2, "B", "11:51", "12:51", 60)]}]}}
    d = judge.judge_delay(proj, t("11:45"), behind_min=15, plan=plan, day=1,
                          track=judge.Track(state="visiting", item_id=1, since=t("10:00")))
    assert d.payload["suggest"]["kind"] == "extend_stay" and d.payload["suggest"]["stay_minutes"] == 90


def test_extend_stay_overstay_and_shift_next_not_in_past():
    """정상: 오래 머묾(재계산 종료 = 지금) -> 지금 + 15분까지, 이동 중 shift_next 는 지금보다 이르지 않음, 완료 항목은 건드리지 않음."""
    proj = {"new_violations": [], "delay_minutes": 40,
            "next": {"item_id": 3, "planned_start": "14:42", "eta": "15:16", "start_at": "15:16"},
            "plan": {"days": [{"day": 1, "items": [_item(2, "구룡포항", "13:36", "15:10", 60, "visiting"),
                                                   _item(3, "과메기문화관", "15:16", "16:16", 60)]}]}}
    d = judge.judge_delay(proj, t("15:10"), behind_min=30, plan=BASE, day=1,
                          track=judge.Track(state="visiting", item_id=2, since=t("13:36")))
    assert d.payload["suggest"]["stay_minutes"] == 110  # 13:36 ~ 15:25 (15:10 + 15) -> 5분 올림
    moving = judge.judge_delay({**proj, "next": {**proj["next"], "eta": "15:01"}}, t("15:10"), behind_min=30, plan=BASE,
                               day=1, track=judge.Track(state="moving", item_id=2, since=t("15:00")))
    assert moving.payload["suggest"] == {"kind": "shift_next", "item_id": 3, "title": "과메기문화관",
                                         "start_time": "15:10", "from_start": "14:42"}  # 예상 도착이 지났으면 지금
    done = judge.judge_delay(proj, t("15:10"), behind_min=30, plan=BASE, day=1,
                             track=judge.Track(state="visiting", item_id=1, since=t("12:30")))
    assert done.payload["suggest"]["kind"] == "shift_next"  # 완료(done) 항목의 체류는 늘리지 않는다


# ---------- 4. 이미 지난 지연 알림 (큐) ----------

def test_queued_delay_is_refreshed_after_moving_on(env):
    """가옥거리에서 35분 늦어 밀림 delay 가 생겼지만 Agent 가 응답 중이라 큐에 남음 -> 그 사이 가옥거리를 떠나 구룡포항 도착
    (규칙 B: 예정 종료 전이라 따라잡음, 큐의 것은 버림) -> 나중에 보내지는 선제 턴은 떠난 곳(가옥거리)의 체류를 늘리자거나 이미 도착한 구룡포항을 '다음'으로 말하면 안 된다."""
    roomy(env)
    env.agent.busy = True
    [d] = env.tick("14:05")["events"]
    assert d["payload"]["suggest"]["item_id"] == item_id(env, 1) and d["payload"]["next"]["item_id"] == item_id(env, 2)
    walk = env.stay(PORT, "14:15", 5)
    assert [e["type"] for e in walk] == ["departed", "arrived"]
    env.agent.busy = False
    # 늦게 온 구룡포항은 예정 종료(14:29)에 떠난다고 본다 (규칙 B) -> 더는 늦지 않아 큐의 delay 는 보내지 않는다
    env.tick("14:25")
    assert [e for _, e, _ in env.agent.received if e["type"] == "delay"] == []
    assert behind_events(env)[0]["status"] == "suppressed"
    # 구룡포항에 계속 머물러 다시 늦으면 그때의 다음·추천안으로 묻는다. 큐에서 버려진 delay(35분)는 알린 것이 아니라
    # 기준이 되지 않는다 -> 처음부터 세어 15분(TRIP_BEHIND_MIN)이면 묻는다 (지난 지연 35분 + 15 까지 기다리지 않음)
    assert env.tick("14:40")["events"] == []  # 구룡포항 예정 종료 14:29 -> 과메기문화관 11분 늦음
    [again] = env.tick("14:45")["events"]
    assert again["payload"]["delay_minutes"] == 16 and cause(again) == "delay:behind:1:0:1"
    sent = [e for _, e, _ in env.agent.received if e["type"] == "delay"]
    assert len(sent) == 1
    assert sent[0]["payload"]["next"]["item_id"] == item_id(env, 3)
    assert sent[0]["payload"]["suggest"]["item_id"] == item_id(env, 2)
    assert "구룡포항 도착이" not in sent[0]["message"]


def test_queued_delay_dropped_when_no_longer_late(env):
    """35분 늦어 밀림 delay 가 큐에 있는 동안 일정이 미뤄짐(다른 경로로 승인) -> 더는 늦지 않으므로 보내지 않는다."""
    roomy(env)
    env.agent.busy = True
    [d] = env.tick("14:05")["events"]
    env.planner.set_stay(env.plan_id, HOUSE, 110)  # 일정이 미뤄짐 (지연 0 근처)
    env.agent.busy = False
    env.tick("14:06")
    assert env.state()["delay_minutes"] < 30
    assert [e for _, e, _ in env.agent.received if e["type"] == "delay"] == []
    assert behind_events(env)[0]["status"] == "suppressed"


def test_departed_message_uses_fresh_next():
    """정상: 출발 문구의 다음 장소·예상 도착은 같은 요청의 재계산 결과, 지난 예상 도착이면 분을 말하지 않는다."""
    assert judge.departed_message("가옥거리", {"eta": "14:20"}, "구룡포항", "walk", t("14:12")) == \
        "가옥거리을(를) 떠났어요. 다음은 구룡포항, 도보 약 8분 (14:20 도착 예정)."
    assert judge.departed_message("가옥거리", {"eta": "14:10"}, "구룡포항", "walk", t("14:12")) == \
        "가옥거리을(를) 떠났어요. 다음은 구룡포항 (14:10 도착 예정)."



# ---------- 1. 일찍 도착 (오픈 전) ----------

def test_arrive_before_opening_is_not_delay(env):
    """첫 장소 짬홍(10:00 개장, 계획 12:30)에 09:30 도착해 기다림 -> 지연 선제 턴이 나오면 안 된다 (audit B3)."""
    env.start("09:20")
    assert [e["type"] for e in env.stay("kto-2834330", "09:30", 3)] == ["arrived"]
    assert env.tick("09:40")["events"] == []
    assert "delay" not in env.types()
    assert env.state()["delay_minutes"] < 0  # 계획보다 일찍 (여유)
