# 로직 버그 점검 — 2026-10-12

실사용(서버 테스트 계정)에서 로직 버그가 이어져, 영역별 조사 에이전트 6개로 전수 점검했다. 조사는 코드 수정 없이 코드 경로 추적과 일회성 컨테이너의 순수 함수 실행·로컬 DB 조회로 재현했고, 아래 '확인됨'은 재현 입력이나 코드 경로로 증명된 것만이다. 증명 못 한 것은 §8 '의심'에 따로 둔다.

- 심각도: **상** 사용자가 틀린 일정·안내·알림을 받거나 못 받음, 데이터·권한 문제 / **중** / **하** 표시·드문 조건
- 데모: `docs/demo/README.md` §3 장면 ①~④ 기준 영향
- 상태: `수정` (브랜치) / `보류` (사유)

조사의 계기가 된 버그(이미 고침): 운영시간 원문에 범위가 둘 이상이면 검사 안 함 (`fix/open-hours-multi-range`), 여행 중 앱은 계획 기준·Agent 는 진행 기준 경고 (`fix/trip-warnings-recommend`), 여행 중 추천 [추가] 가 지난 일정 앞에 들어감 (같은 브랜치).

## 1. planner (일정 계산·검증·변경)

| ID | 버그 | 심각도 | 데모 | 위치 | 상태 |
|---|---|---|---|---|---|
| P1 | 머무는 곳 없이 진행 상태만 있으면(앞 항목을 건너뛰고 다음을 done) 건너뛴 planned 항목이 다시 '다음'이 되어 지금 시각에 끼어든다. 재현: A planned, B done, C planned, 13:00 → A 13:00~14:00, next=A | 상 | 없음 | `validation.py` anchor_id None 분기, `next_item_id`, `ops._anchor` | |
| P2 | 휴무 `매주 월~목요일` 을 목요일만으로 읽음 (앞쪽 '요일' 생략 범위 미지원). 피어라계단 미디어아트 월요일 방문에 경고 없음 | 상 | 없음 | `validation.closed_weekdays` | |
| P3 | `매주 월요일 / 매월 셋째 토요일, 일요일` 을 매주 일요일 휴무로 읽음 (`,` 로 먼저 나눠 '매월 셋째'가 떨어짐) | 중 | 없음 | `validation.closed_weekdays` | |
| P4 | 운영시간 ※ 문구의 운영 기간·계절 변경 무시 (`※ 매년 8월 중순~9월 말까지 개장`, `※ 하절기(6~8월)는 20:00 까지`) → 틀린 판정 | 중 | 없음 | `validation.open_hours_for` | |
| P5 | '공휴일 다음날'·공휴일 휴무를 판정하지 않음. 과메기문화관은 데모 날짜 10/10(한글날 다음날)이 실제 휴관일 수 있음 | 중 | 사실 관계 | `validation.closed_weekdays` | 보류 (데모 계획이 바뀜, 발표 후) |
| P6 | 제안 후 사용자가 도착(visiting)한 항목을 지우거나 옮기는 제안도 그대로 승인됨 (`progress_changed` 는 done 만 봄) | 중 | 없음 | `ops.progress_changed` | |
| P7 | 첫 항목 고정 시각이 day_start 보다 이르면 "앞 일정 때문에 … 늦음" error (앞 일정이 없음) | 하 | 없음 | `validation.build_schedule` | |
| P8 | 요일 토큰(`(월요일 휴무)`) 뒤의 쉬는 구간이 모든 날에서 빠짐 | 하 | 없음 | `validation.open_hours_for` | |

## 2. trip (여행 중 판정·이벤트)

| ID | 버그 | 심각도 | 데모 | 위치 | 상태 |
|---|---|---|---|---|---|
| T1 | 계획 장소 옆을 걸어서 지나가기만 해도 도착 → 출발, 그 항목이 done (반경 60m+accuracy, 반경 안 90초만 봄). 저녁에 실제로 가면 도착이 안 남 | 상 | 없음 (검증 필요) | `trip/judge.py` 도착 판정 | |
| T2 | 앞 항목을 건너뛴 날 마지막 장소를 떠나면 건너뛴 항목이 '다음' → 운영시간·하루 초과 위반 선제 턴 (P1 과 같은 뿌리) | 상 | 없음 | `ops._anchor`, `next_item_id` | |
| T3 | 이미 알린 위반이 이어지는 동안 큐의 delay 를 지금 상황으로 고치는 처리(B6)가 돌지 않아, 떠난 곳 체류 늘리기·이미 도착한 곳을 '다음'으로 한 선제 턴이 나감 | 중 | 없음 | `trip/service.py` `_judge_delay`·`_refresh_queued_delay` | |
| T4 | 일찍 도착한 곳의 `extend_stay` 추천이 일찍 온 만큼 체류를 더 늘려, 승인하면 남은 일정이 그만큼 밀림 | 중 | 없음 (1분 차) | `trip/judge.py` behind 추천 | |
| T5 | 같은 날 가상 시각을 되돌리면 위치가 모두 `out_of_order` 로 버려지고 선제 턴이 쿨다운에 막힘 | 중 | 조건부 (run_demo `b`·`r`) | `judge.py` out_of_order, `policy.py` 쿨다운 | |
| T6 | 같은 좌표(수 m)의 두 항목이 이어지면 두 번째 도착이 안 나거나 아무 때나 남 (가옥거리 ↔ 피어라계단 5.5m) | 중 | 없음 | `judge.py` step | |
| T7 | 같은 틱에 안전 '상승'과 '해제'가 함께 오면 이미 풀린 위험으로 safety_raised 선제 턴이 나감 | 중 | 없음 | `trip/service.py` 경고 판정·dispatch | |
| T8 | 여행 중 계획을 지우면 여행일이 끝나지 않고 틱마다 실패 (자정 종료도 안 됨) | 하 | 없음 | `trip/service.py` tick | |

## 3. agent (하네스·도구·검토)

| ID | 버그 | 심각도 | 데모 | 위치 | 상태 |
|---|---|---|---|---|---|
| A1 | 동행 모드에서 초안이 생기는 순간 일정 시각 기준이 진행 기준 → 계획 기준으로 바뀜 (첫 도착 전, preview 의 live_actual 이 None) | 상 | 없음 | `agent/tools.py` current_view, `ops.live_actual` | |
| A2 | 초안이 없을 때 안전 점검은 계획 시각 기준이라, 늦어진 해안 방문을 '지난 일정'으로 보고 점검하지 않음 (S5 와 같은 뿌리) | 상 | 낮음 (장면 ④) | `planner/router.py` GET /safety | |
| A3 | 동행 모드 거부 규칙이 순서·날짜를 보지 않아 `add_item(order=1)` 이 지난 일정 자리에 들어감 | 중 | 없음 | `agent/tools.py` `_guard` | |
| A4 | 거부 규칙이 문자열 id·day(`"88"`)로 우회됨 (planner 는 숫자로 받아들임) | 중 | 없음 | `agent/tools.py` `_guard` | |
| A5 | 검토가 이미 지난 부분(다녀온 곳의 점심 시간 식당 없음 등)까지 should 로 내 검토 관문이 고칠 수 없는 걸 고치게 함 | 중 | 없음 | `agent/review.py` | |
| A6 | 여행 2일차 이후에도 add_item·recommend_places 의 day 기본값이 1 | 중 | 없음 | `agent/tools.py` | |
| A7 | '현재 상황' 블록의 다음 일정 시각(trip 스냅샷)과 '현재 일정'(대기 제안 반영) 시각이 다름 | 중 | 있음 (장면 ③ 대기 제안 뒤) | `agent/prompts.py` | |
| A8 | `start_time: "none"` 채움값이 같이 오면 머무는 곳의 체류 변경까지 통째로 거부, 다른 항목은 고정 시각이 조용히 풀림 | 하 | 있음 (장면 ③ 체류 늘리기) | `agent/tools.py` | |
| A9 | 다시 제안 턴이 변경 없이 끝나면 원래 제안이 승인 불가인 채 '승인 대기'로 남음 | 하 | 없음 | `agent/harness.py` | |
| A10 | 현재 상황 블록 "오늘 남은 일정 N곳"에 머무는 곳 포함 | 하 | 표시만 | `agent/prompts.py` | |

## 4. safety·routing

| ID | 버그 | 심각도 | 데모 | 위치 | 상태 |
|---|---|---|---|---|---|
| S1 | 단기예보 발표 직후 hh:15~hh:59 에 지금 시각 예보가 비는데 안전으로 판정, 잘못된 '해제' 알림까지 | 상 | 없음 (`s4_live` 는 실제 시각에 따라 걸릴 수 있음) | `safety/risk.py` classify, `service._hours` | |
| S2 | 길찾기 실패·추정 시 구간을 직선으로 보고, 침수 구역을 지나는 차량 구간을 안전으로 판정 | 상 | 없음 | `planner/safety.py`, `travel.path` | |
| S3 | 센서 등급이 바뀌어도 fingerprint 가 같아 trip 의 피할 구역이 최대 약 3시간 갱신 안 됨 (실데이터) | 상 | 없음 | `safety/service.py` fingerprint, `trip` HazardCache | |
| S4 | 기상청 NODATA(03)를 빈 예보 정상 수신으로 처리 → 지도 안전, 일정 '예보 범위 밖' | 중 | 없음 | `safety/sources.py` | |
| S5 | planner 주기 점검·GET /safety 가 여행 중에도 계획 시각 기준 (preview 는 진행 기준) | 중 | 없음 | `planner/safety_watch.py`, `router.py` | |
| S6 | `recheck_from` 이 오늘·과거 날짜로 나옴 | 하 | 없음 | `safety/risk.py` | |
| S7 | 떨어진 시각(14시·18시)을 '14~19시'로 묶어 안내 | 하 | 없음 | `safety/risk.py` | |

## 5. docent·places·account

| ID | 버그 | 심각도 | 데모 | 위치 | 상태 |
|---|---|---|---|---|---|
| D1 | 탈퇴가 docent(메모리·노트)·trip 데이터를 지우지 않음 (개인정보 잔존) | 상 | 없음 | `account/router.py` SERVICES | |
| D2 | 계획 삭제 때 대화·노트·plan 메모리·여행 기록이 남고 진행 중 여행일이 끝나지 않음. 앱 확인창은 "AI 대화가 모두 사라져요" | 상 | 없음 | `planner/router.py` delete_plan, `trip` tick | |
| D3 | 가이드 준비 도중 메모리가 바뀌면 남은 장소가 옛 메모리로 ready 가 되고 다시 안 만들어짐 | 중 | 가능 (장면 ② "더 짧게"가 준비 중일 때) | `agent/guide.py`, `docent/router.py` `_mark_stale` | |
| D4 | agent 재시작 시 pending/preparing 노트가 영구 고착 (자동 준비가 건너뜀, 앱에 [다시 준비] 없음) | 중 | 재시작 시 | `agent/router.py` AUTO_SKIP | |
| D5 | 승인·추천 추가 뒤 자동 준비가 다녀온 곳·지난 날 장소까지 다시 만듦 (LLM 비용) | 중 | 거의 없음 | `agent/guide.py` plan_places | |
| D6 | docent 외부 검색·페이지 API 에 사용자별 호출 제한 없음 (공개 서버 비용·네이버 한도) | 중 | 없음 | `docent/router.py` | |
| D7 | 이용요금(fee)에 주차요금이 섞임 ("공연별 상이 \| 무료") | 하 | 없음 | `places/normalize.py` | |
| D8 | 근처 검색(near + query)이 가까운 순이 아니고 반경 안 무관한 장소로 채워짐 | 하 | 없음 | `agent/tools.py`, `places/router.py` hybrid | |

## 6. 앱 (Flutter)

| ID | 버그 | 심각도 | 데모 | 위치 | 상태 |
|---|---|---|---|---|---|
| F1 | 다른 클라이언트(진행 스크립트·대시보드)가 여행을 시작해도 앱이 동행 모드로 안 바뀜 (inactive 면 state 폴링 안 함) | 상 | 있음 (`s2_start`) | `trip_controller.dart` `_scheduleState` | |
| F2 | 동행 중 다른 클라이언트가 제안을 승인하면 앱 계획이 다음 도착·출발까지 옛 일정 | 상 | 있음 (`s3_accept`) | `trip_controller.dart` | |
| F3 | 여행 종료 요약이 계획을 만든 제안까지 '함께 바꾼 일정'으로 세고 모든 장소에 '바꿈' | 상 | 있음 (`end`) | `trip_end_screen.dart` | |
| F4 | 마지막 장소에 머무는 중인데 "다음이 마지막 일정이에요" | 중 | 있음 (`s4_clear`) | `today_screen.dart` | |
| F5 | 여러 날 계획에서 2일차 시작 시트가 1일차 날짜·장소 수를 보여줌 | 중 | 없음 | `trip_controller.dart` today | |
| F6 | 이벤트가 50개를 넘으면 옛 이벤트를 새것처럼 다시 알리고(경고·진동 포함) 알림 목록은 가장 오래된 50개만 | 중 | 없음 | `trip_controller.dart`, `event_inbox_screen.dart` | |
| F7 | 여러 날 여행에서 지난 날 이벤트가 알림 목록·배지·종료 요약에 섞임 | 중 | 없음 | `trip_end_screen.dart`, `event_inbox_screen.dart` | |
| F8 | 늦게 도착한 옛 계획 GET 응답이 방금 승인한 계획을 덮어씀 (version 가드 없음) | 중 | 타이밍 | `plan_controller.dart` | |
| F9 | 승인 '실행 취소'가 구간 이동 수단을 되돌리지 않음 | 중 | 없음 | `restore_ops.dart` | |
| F10 | 같은 장소를 지웠다 넣은 제안이 '교체 구룡포항 → 구룡포항', 삭제+추가 따로 | 하 | 없음 | `proposal_card.dart` | |
| F11 | 승인된 제안 비교 보기에서 새로 들어간 장소의 위험 배지가 없음 (임시 음수 id) | 하 | 없음 | `compare_screen.dart` | |
| F12 | 안전 관련 시각을 기기 시간대로 보여줌 (KST 아닌 브라우저에서 Today 와 다름) | 하 | 없음 | `safety_widgets.dart` 등 | |

## 7. 수정 방침

- 내일(10-13) 발표 데모가 바뀌지 않게, 각 수정은 각본 데모 테스트(`tests/trip/test_scenes.py`)와 run_demo 기대 결과로 확인한다.
- P5 는 데모 날짜(10/10)의 과메기문화관 경고가 새로 생기므로 발표 후 (사용자 결정 2026-10-12).
- T1 은 도착 판정 규칙이 바뀌므로 각본 run_demo 로 장면 ②~④ 도착·출발이 그대로인지 확인한 뒤 넣는다 (사용자 결정).

## 8. 의심 (증명 못 함, 이번에는 고치지 않음)

- planner: 자정 넘는 방문의 요일 판정, `max_travel_minutes` 만 done 구간 포함, 오늘이 아닌 day 의 projection, ops 로 status 변경 시 diff 가 숨김, 머무는 중 마감 경과로 새 위반
- trip: `shift_next` 승인 후 1분 늦음도 `fixed_time_missed` 단계 3, 첫 도착 전 이동시간 무시, 머무는 항목이 빠진 뒤 재계산 404, 제안 카드 없는 선제 턴 뒤 기준, 자동 종료 뒤 같은 날 다시 열기, 선제 턴 전송이 모든 여행일 틱을 막음
- agent: 이어받은 대기 제안 ops 가 `_guard` 를 다시 안 거침, 선제 턴 오류 재시도 시 말풍선 중복, projection 에 위치 미전달, repropose 호출 수 차감 경합, 여행 중 update_plan 허용
- safety: 직선 대체의 거짓 경보, 특보 수신 실패 시 침수 safe·방파제 None, 점검 중 버전 변경 시 last_situation 갱신, better_times 가 다른 날 제안, 가상 snapshot 공유 객체, 가상↔실제 시각 차 반올림
- docent·account: 폐업 장소 잔존, 탈퇴 뒤 access 토큰, 동시 가입 500, 대화 중 데이터 지우기 FK, 짧게·자세히 메모리 공존, `agent_usage` 초기화 안 됨
- 앱: 위치 응답과 state 폴링 순서 경쟁, 추천 추가·실행 취소 뒤 TripState 재조회 없음, accept 파싱 실패 시 applying 고착, 큰 음수 이동 시간, 기간 밖 active 여행, 기기 시각으로 오늘 판정 (`planner_shell.dart:121`)
