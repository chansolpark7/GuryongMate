# events — trip 이벤트 → 앱 표현

원본: `docs/trip/README.md` §3~4 (이벤트 7종, 개입 단계 0~3, Event Policy). 판단은 서버가 하고 앱은 **받아서 그리기만** 한다.
이벤트는 동행 모드(진행 중인 여행일)에서만 온다. 여행 전 안전 경고는 이벤트가 아니라 planner 경고로 계획 화면 배지·배너에 보인다(25).

## 1. 매핑

| `type` | level | severity | 상태 갱신 | 토스트 | 상단 배너 | 카드 | AI 대화 메시지 | 알림 목록(24) | 화면 |
|---|---|---|---|---|---|---|---|---|---|
| `day_started` / `day_ended` | 0 | info | 모드 전환 | — | — | — | — | 숨김 | 14 / 30 |
| `arrived` | 0 + 2 | info | 항목 → visiting, 패널 "지금" | — | — | **도착 해설 카드** `card{kind:"docent", place_id, item_id}` | — | ✓ "…에 도착했어요" + [해설 듣기] | 16 |
| `departed` | 0 + 1 | info | 항목 → done, 패널 "이동 중" | **"다음은 {장소}, {수단} n분"** [경로 보기] | — | — | — | ✓ | 15 |
| (가벼운 지연) | 0 | — | `delay_minutes`·`next.eta` → ETA 칩 Amber | — | — | — | — | — | 14 |
| `delay` | 3 | warning | ETA 칩 Red | — | — | — | **선제 메시지 + 제안 카드** `agent{session_id, message_id, proposal_id}` | ✓ + 제안 상태 | 19 (→ 20, 21) |
| `safety_raised` | 3 | warning/danger | 해당 카드 배지 상승 | — | Context Banner(25) | — | **선제 메시지 + 근거 + 제안** | ✓ + 제안 상태 | 22 (→ 21) |
| `safety_eased` | 1 | info | 배지 하강 | **"{장소} {근거} 주의가 해제됐어요"** (Green 아이콘) | 배너 Eased 4초 후 사라짐 | — | — | ✓ | 14/25 |
| `near_hazard` | 1 | danger/warning | 지도 구역 강조 | — | **위험 구역 경고 패널(최상단)** + 진동 | — | — | ✓ + [지도] | 23 (→ 28) |

- `message` 는 서버가 만든 고정 문구다. 토스트·경고 패널·알림 목록 제목에 그대로 쓴다. 앱이 문구를 지어내지 않는다 (예외: 패널의 버튼·라벨).
- `status=suppressed` 이벤트는 그리지 않는다. `queued` 는 아직 선제 턴이 안 끝난 것이라 AI 메시지 없이 알림 목록에만 "준비 중".
- 3단계 이벤트의 대화 내용은 이벤트가 아니라 **Agent 세션**에 있다 → `GET /agent/sessions/{agent.session_id}` 로 메시지·카드를 불러온다.

## 2. 여러 이벤트가 겹칠 때 표시 우선순위

서버 Event Policy 와 같은 순서: **안전 > 일정 영향 > 안내**.

1. `near_hazard` — 무엇이 떠 있든 최상단 패널로 덮는다. 열린 시트·카드는 그대로 두고 그 위에 그린다.
2. `safety_raised` (3단계)
3. `delay` (3단계)
4. `arrived` 카드
5. `departed`·`safety_eased` 토스트

- 화면에 **동시에 하나씩만**: 경고 패널 1 + (카드 또는 선제 미리보기) 1 + 토스트 1. 낮은 것은 뒤에 줄 서고, 높은 것이 닫히면 이어서 보인다.
- 도착 카드가 떠 있는데 3단계가 오면: 카드는 접어서(작은 "해설 듣기" 칩) 패널 위에 남기고 선제 미리보기를 띄운다.
- 토스트는 4초, 같은 종류가 연달아 오면 마지막 것만.
- 앱을 오래 꺼 두었다가 열어 이벤트가 쌓여 있으면: 지난 0~1단계는 알림 목록에만 넣고(토스트 생략), 아직 처리되지 않은 3단계(제안 pending)만 미리보기로 알린다. `near_hazard` 는 지금 위치가 아직 구역 안일 때만 다시 띄운다(다음 위치 응답으로 판단).

## 3. AI 가 먼저 말을 건 상태

### 3.1 대화 안 (시트가 열려 있을 때)
- 사용자 말풍선 없이 시작하는 assistant 메시지 위에 **선제 표시 줄**: ✨ "구룡메이트가 먼저 알려드려요 · 14:00" + 이벤트 칩(지연 Amber clock / 안전 Red shield). (Components_Companion › Proactive Message Header)
- 구분 방법: 대화 기록에는 숨김 `[이벤트]` 메시지가 보이지 않으므로, **이벤트의 `agent.message_id` 와 같은 id 의 메시지**에 표시 줄을 붙인다. 앱은 오늘 이벤트 목록을 들고 있다가 세션을 그릴 때 매칭한다.
- 새 선제 메시지가 오면 시트를 그 메시지로 스크롤하고 입력창은 그대로 둔다(사용자가 쓰던 글은 지우지 않는다).

### 3.2 대화 밖 (시트가 닫혀 있을 때) — 14-S3 (`73:7529`)
- AI FAB → **Has Suggestion** 변형(빨간 점, 기존 Components 의 FAB 변형).
- FAB 왼쪽 위에 **미리보기 말풍선**(232 폭, Purple 테두리): ✨ 구룡메이트 + 이벤트 칩 + 서버 `message` 한 줄 + "보기 ›". 8초 뒤 말풍선은 접히고 점만 남는다. 안전(`safety_raised`)은 접히지 않고 사용자가 닫을 때까지 남는다.
- 탭 → AI 시트를 Full 로 열고 해당 메시지로 스크롤. 점은 그 제안이 pending 이 아니게 되거나 시트에서 메시지를 보면 사라진다.
- 다른 화면(17 가이드 노트, 24 알림, 설정 등 push 화면)에 있을 때: FAB 가 없으므로 앱 상단에 같은 미리보기를 배너로(탭 → Shell 로 돌아가 시트 열기).
- 앱이 백그라운드면: 이번 범위에서 OS 알림은 하지 않는다 (백그라운드 위치·푸시 없음, trip §2.2). → [gaps.md](gaps.md) (b).

## 4. 이벤트를 받는 두 경로

1. **위치 전송 응답** — `POST /trip/plans/{id}/location` 의 `events` (그 요청으로 생긴 이벤트). `near_hazard`·`arrived`·`departed` 는 대부분 여기서 바로 온다. 받은 즉시 그린다.
2. **폴링** — `GET /trip/plans/{id}/events?after={마지막 id}&limit=50`. 위치와 상관없이 생기는 `delay`(시간 경과)·`safety_raised`·`safety_eased` 와, 3단계의 `agent` 결과(선제 턴이 끝나 `status` 가 `delivered` 가 되면 채워짐)를 받는다.

두 경로에서 같은 id 가 오면 한 번만 처리한다 (마지막 id 를 기억).

## 5. 폴링 시점

| 앱 상태 | 주기 |
|---|---|
| 동행 모드, 앱 화면 켜짐 (Today·Planner·Map·AI 시트 어디든) | **5초** (발표장에서는 5초 고정) |
| 동행 모드, push 화면(가이드 노트 등) | 10초 |
| 3단계 이벤트가 `queued` / `agent` 가 아직 null | 2초 (최대 60초, 그 뒤 5초) |
| 앱이 포그라운드로 돌아옴 | 즉시 1회 + `GET …/state` |
| 계획 모드 | 하지 않음 (Plan List·Planner 는 planner 경고 `GET …/safety/alerts?status=new` 를 화면 진입 시 1회) |

- `TripState` 는 15초마다 `GET …/state` (또는 위치 응답의 `state`).
- 이벤트를 화면에 보여주면(카드·토스트·경고가 실제로 보였거나 알림 목록에서 보이면) `POST …/events/{id}/seen`.
- 나중에 SSE 가 생기면 같은 처리기를 쓰고 폴링만 바꾼다 (trip 문서 "나중에 SSE").

## 6. 이벤트 후 갱신할 것

| 이벤트 | 다시 불러올 것 |
|---|---|
| `arrived`·`departed` | `GET …/state`, PlanDetail(진행 상태: `progress_version` 비교) |
| `delay`·`safety_raised` (agent 채워짐) | `GET /agent/sessions/{sid}` (메시지·카드) |
| 제안 승인 | PlanDetail, `GET /planner/plans/{id}/safety`, 지도 경로 |
| `safety_raised`·`safety_eased` | `GET /planner/plans/{id}/safety` (배지), 지도 `GET /safety/avoid` |
| `near_hazard` | `GET /safety/avoid` (구역 다각형) |
