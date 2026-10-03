# 구룡포 디지털트윈(DT) 안전 데이터 API 명세

> 출처: E-UM 디지털트윈 포항 API 자료실
> - 침수: https://pohang-eum.kr/data/list/7 (`type=7`)
> - 방파제: https://pohang-eum.kr/data/list/8 (`type=8`)
> - 상세 페이지: `https://pohang-eum.kr/data/list/detail/<서비스ID>`
> - 원본 명세 JSON: [`raw/`](raw/) — 자료실이 내부적으로 쓰는 `GET https://pohang-eum.kr/netro/api/detail?apiId=<서비스ID>` 응답 그대로
>
> 수집일: 2026-10-04. 아래 내용은 **문서에 적힌 것**과 **직접 호출해서 확인한 것**을 구분해 표시한다.

---

## 0. 요약

| 서비스 ID | 기능 | Endpoint (`https://genix.pohang-eum.kr`) | 추가 파라미터 | 성격 |
|---|---|---|---|---|
| INT_S05_001 | 종류별 전체 수위계 장비 목록 | `GET /dpg/water/devices` | — | 정적(장비) |
| INT_S05_002 | 특정 지표면 수위계 장비 | `GET /dpg/water/roadDevices` | `id` | 정적 |
| INT_S05_003 | 특정 맨홀(하수관) 장비 | `GET /dpg/water/holeDevices` | `id` | 정적 |
| INT_S05_004 | 특정 하천 수위계 장비 | `GET /dpg/water/riverDevices` | `id` | 정적 |
| INT_S05_005 | 특정 강우량계 장비 | `GET /dpg/water/rainDevices` | `id` | 정적 |
| **INT_S05_006** | **전체 수위계 실시간 수집 정보** | `GET /dpg/sensor/latest/sensorLevel` | `id` | **실시간** |
| INT_S06_001 | 보행자 예측 관련 종류별 장비 목록 | `GET /dpg/pedestrian/device/list` | — | 정적 |
| INT_S06_002 | 특정 자외선 측정 장비 | `GET /dpg/uv/devices` | `id` | 정적 |
| INT_S06_003 | 특정 스피커 장비 | `GET /dpg/speaker/devices` | `id` | 정적 |
| INT_S06_004 | 특정 AI CCTV | `GET /dpg/ai-cctv/devices` | `id` | 정적 |
| **INT_S06_005** | **자외선 실시간 수집 정보** | `GET /dpg/sensor/latest/uvIndex` | — | **실시간** |
| **INT_S06_006** | **시간대별 이상행동 발생수 통계** | `GET /dpg/aicctv/avghour/datas` | — | **준실시간(시간 단위)** |

- 모두 `GET`, REST API, JSON.
- 서비스에서 실제로 쓸 값은 굵게 표시한 3개(S05_006, S06_005, S06_006)이고, 나머지는 장비 위치 메타데이터다(초기 1회 적재로 충분).

### ✅ 2026-10-04 전체 활용신청·동작 확인
개인계정 키(`DT_SERVICE_KEY`)로 12개 전부 신청 후 호출. **모두 권한 통과(40104 없음).**

| 상태 | API |
|---|---|
| ✅ 200 정상 | S05_001, S05_004(id=1), S05_006, S06_001, S06_003(id=1), S06_004(id=1), S06_005, S06_006 |
| ⚠️ 400 "장비 데이터가 없습니다" | S05_002(road)·S05_003(hole)·S05_005(rain)·S06_002(uv) — **권한 아님, `id=1` 장비가 없을 뿐.** 유효 id면 정상 (예: `roadDevices?id=7` ✅) |

→ 결론: **12개 모두 사용 가능.** "특정 장비" API는 전체목록 API로 커버돼 보조용.

---

## 1. 공통 요구 사항

### 인증
- 모든 API에 **`serviceKey` (query parameter, 필수)**.
- 키는 자료실 상세 페이지의 **"API 활용신청"** 으로 발급(로그인 필요). → **팀에서 아직 발급 안 받았으면 가장 먼저 신청.**
- ✅ 확인: 키 없이 호출 시 `HTTP 401`
  ```json
  {"rspns_rslt": {"rslt_cd": "40102", "rslt_msg": "유효하지 않은 이용자 서비스 키"}, "rspns_bdy": null}
  ```

- ✅ 확인: 키는 **URL 인코딩된 값 그대로** query에 넣어야 한다 (`%2B`, `%2F`, `%3D` 유지).
  디코딩한 값(`+`, `/`, `=`)을 보내거나 두 번 인코딩하거나 헤더로 보내면 모두 `40102`.
- 오류 코드 (직접 확인)

  | `rslt_cd` | 메시지 | 의미 |
  |---|---|---|
  | 40102 | 유효하지 않은 이용자 서비스 키 | 키 자체가 틀림 / 인코딩 잘못 |
  | 40104 | 요청한 API 에 대한 권한 없음 | 키는 유효하지만 해당 API 활용 승인이 안 됨 |

- 2026-10-04: E-UM도 **API별로 따로 활용신청**해야 함을 확인.
  - 개인계정 키(`DT_SERVICE_KEY`)로 **활용신청한 `INT_S05_001`만 ✅ 200 정상**, 나머지 11개는 여전히 `40104`.
  - 즉 실시간 수위(S05_006)·방파제(S06_*) 등 **실제로 쓸 API는 아직 신청·승인 전.**
  - **할 일:** 적어도 **INT_S05_006(실시간 수위), INT_S06_006(이상행동), INT_S06_005(자외선), INT_S06_001·INT_S05_001(장비목록)** 활용신청.

### ✅ INT_S05_001 실제 응답 (2026-10-04, 구룡포 실장비)
원본: [`raw/live/water_devices.json`](raw/live/water_devices.json). 명세의 샘플과 달리 **실제 장비 목록**이 온다. 성공 응답 구조는 명세대로 `{id, status, data}`.

| 목록 | 수 | 장비 (id · name · 위도 · 경도) |
|---|---|---|
| `roadList` 지표면 | 5 | 1 포항 지표면 수위계_25#001 35.986115,129.549245 · 2 …#002 35.988698,129.553056 · 3 …#003 35.98947,129.554646 · 4 …#004 35.99069,129.556057 · 5 …#005 35.991109,129.557354 |
| `holeList` 맨홀 | 3 | 1 포항시 스마트맨홀 #01 35.985929,129.549278 · 2 #02 35.987373,129.551115 · 3 #03 35.991235,129.557327 |
| `riverList` 하천 | 1 | 1 포항 이도 하천수위계_25#002 35.987244,129.550467 |
| `rainList` 강우 | 1 | 1 포항 이도 강우량계_25#001 35.984987,129.546115 |

→ 침수 센서는 구룡포항 북측(구룡포리)~병포리 일대에 **총 10대**. 이 좌표로 구역·영향반경 매핑을 시작할 수 있다.

### 응답 형식
- 문서상 성공 응답 공통 envelope:
  ```json
  { "id": "1", "status": "success", "data": { ... } }
  ```
- ⚠️ 실제 오류 응답은 문서와 **다른 envelope**(`rspns_rslt` / `rspns_bdy`)를 쓴다. 성공 응답도 문서와 다를 수 있으므로 **키 발급 후 실제 응답으로 재확인** 필요. 어댑터는 두 형식 모두 처리할 것.

### 문서에 없는 것 (키 발급 후 확인 필요)
- 갱신 주기(센서 수집 간격), 호출 제한(rate limit)
- `value` 단위, `level` 등급 기준(몇 단계, 임계값)
- `sensorType` 값 전체 목록(문서 예시는 `HOLE`만 있음)
- 좌표계: 샘플이 `35.98…, 129.55…` 이므로 **WGS84(위경도)** 로 보임
- "특정 장비" API의 `id`를 안 넣으면 전체가 나오는지
- 시간대 (`dateTime`, `hour` 값은 KST로 추정)

---

## 2. 침수예측 및 대응 (type 7)

서비스명: `경상북도 포항시 구룡포읍_구룡포항 특화 침수예측 및 대응`

장비 종류 4가지

| 목록 키 | 장비 | 예시 장비명 / 위치 |
|---|---|---|
| `roadList` | 지표면 수위계 (도로 침수) | 하나과메기_지표면 수위계 — 35.986115, 129.549245 (병포리 585) |
| `holeList` | 관로 수위계 (스마트 맨홀) | 하나과메기_스마트맨홀 — 35.985929, 129.549278 (호미로 152) |
| `riverList` | 하천 수위계 | 구룡포교_하천수위계 — 35.987244, 129.550467 (병포리 157-279) |
| `rainList` | 강우량계 | 구룡포읍행정복지센터_강우량계 — 35.984987, 129.546115 (호미로 133) |

### INT_S05_001 각 종류별 전체 수위계 장비 정보 목록
`GET /dpg/water/devices?serviceKey=…`

| 요청 | 필수 | 설명 |
|---|---|---|
| `serviceKey` | Y | 인증키 |

응답 `data`
```text
data
├─ roadList[]  { id, name, latitude, longitude, address }
├─ holeList[]  { id, name, latitude, longitude, address }
├─ riverList[] { id, name, latitude, longitude, address }
└─ rainList[]  { id, name, latitude, longitude, address }
```

| 필드 | 크기 | 설명 |
|---|---|---|
| `id` | 8 | 장비 고유번호 |
| `name` | 50 | 장비명 |
| `latitude` | 20 | 위도 |
| `longitude` | 20 | 경도 |
| `address` | 100 | 상세 주소 |

### INT_S05_002 ~ 005 특정 장비 정보
| ID | Endpoint |
|---|---|
| S05_002 지표면 수위계 | `GET /dpg/water/roadDevices` |
| S05_003 맨홀 | `GET /dpg/water/holeDevices` |
| S05_004 하천 수위계 | `GET /dpg/water/riverDevices` |
| S05_005 강우량계 | `GET /dpg/water/rainDevices` |

요청: `serviceKey`(Y), `id`(Y, 장비 고유번호)
응답 `data`: `{ id, name, latitude, longitude, address }` (필드 의미는 S05_001과 동일)

### ★ INT_S05_006 전체 수위계 장비 실시간 수집 정보
`GET /dpg/sensor/latest/sensorLevel?serviceKey=…&id=…`

| 요청 | 필수 | 샘플 | 설명 |
|---|---|---|---|
| `serviceKey` | Y | | 인증키 |
| `id` | Y | `1` | 장비 고유번호 (문서상 필수지만 기능명은 "전체" — 실제 동작 확인 필요) |

**✅ 실제 응답: `id` 없이 호출 → 센서 10대 배열** (`data`는 list). 원본 [`raw/live/S05_006_level.json`](raw/live/S05_006_level.json). `id` 주면 해당 1대만.

| 필드 | 샘플 | 설명 |
|---|---|---|
| `id` | `2` | 장비 고유번호 |
| `eui` | `0017B2FFFE503373` | 장비 EUI (LoRa) |
| `name` | `하나과메기_스마트맨홀` | 장비명 |
| `latitude`/`longitude` | `35.985929`/`129.549278` | 위경도 |
| `address` | `… 호미로 152` | 주소 |
| `sensorType` | `HOLE` | **실제 값: `HOLE`·`ROAD`·`RIVER`·`RAIN` 4종** |
| `value` | `1394` | 실시간 측정값 (**단위 미기재**) |
| `level` | `1` | 측정값 등급 (**기준 미기재**) |

2026-10-04 02:xx 평상시 실측 (전 센서 `level 1`):

| sensorType | 대수 | 관측 value |
|---|---|---|
| HOLE 맨홀 | 3 | 895 ~ 1394 |
| ROAD 지표면 | 5 | 0 |
| RIVER 하천 | 1 | 0.0 |
| RAIN 강우 | 1 | 0.0 |

> ⚠️ **남은 공백 2개 (위험 판정의 핵심):**
> 1. **측정 시각 필드가 없다** → 데이터 신선도 판단 불가. 우리가 수집시각을 찍어 저장해야 한다.
> 2. **`level` 등급 체계·`value` 단위·임계값 미확인.** 평상시엔 전부 `level 1`뿐이라, level 2·3가 언제 뜨는지 알 수 없다. 비/만조 때 관측하거나 포털 문의로 확정. 그 전까진 우리 쪽 임계값(가정)으로 등급 재계산.
> - HOLE value(수백~천대)와 ROAD/RIVER value(0)는 **단위·기준이 서로 다른 것으로 보임** → sensorType별로 따로 해석.

---

## 3. 방파제 보행자 이동 예측 및 안전 경보 (type 8)

서비스명: `경상북도 포항시 구룡포읍_방파제 보행자 이동 예측 및 안전 경보`

### INT_S06_001 보행자 예측 관련 각 종류별 장비 정보 목록
`GET /dpg/pedestrian/device/list?serviceKey=…`

응답 `data`
```text
data
├─ uv_devices[] { id, name, latitude, longitude, address }
├─ speakers[]   { id, name, latitude, longitude, address }
└─ ai_cctvs[]   { id, name, angle, latitude, longitude, address }
```

**✅ 실제 응답** (원본 [`raw/live/S06_001_pedlist.json`](raw/live/S06_001_pedlist.json)): AI CCTV **15대**, 스피커 **8대**, 자외선 **1대**. 전부 `구룡포 북방파제`/`남방파제`로 이름에 구역이 들어있다 → **남/북 방파제 구분이 이름으로 가능**(S06_006의 south/north와 연결).

| 종류 | 수 | 좌표 범위(위도, 경도) |
|---|---|---|
| AI CCTV | 15 | 북 35.9857~35.9887 / 남 35.9826~35.9853, 129.557~129.560 |
| 스피커 | 8 | 북방파제 SG/SR, 남방파제 SG/SR (일부 좌표 공란) |
| 자외선 | 1 | 35.98524, 129.551826 (id=6) |

> 위험구역 폴리곤은 명세에 없다. CCTV 좌표 군집(북/남 방파제 선)으로 **우리가 구역을 정의**해야 한다.

### INT_S06_002 ~ 004 특정 장비 정보
| ID | Endpoint | 응답 `data` |
|---|---|---|
| S06_002 자외선 측정 장비 | `GET /dpg/uv/devices` | `{ id, name, latitude, longitude, address }` |
| S06_003 스피커 | `GET /dpg/speaker/devices` | `{ id, name, latitude, longitude, address }` |
| S06_004 AI CCTV | `GET /dpg/ai-cctv/devices` | `{ id, name, angle, latitude, longitude, address }` |

요청: `serviceKey`(Y), `id`(Y)

### ★ INT_S06_005 자외선 측정 장비 실시간 수집 정보
`GET /dpg/sensor/latest/uvIndex?serviceKey=…`

| 필드 | 크기 | 샘플 | 설명 |
|---|---|---|---|
| `dateTime` | 50 | `2026-07-08 17:21:17` | 측정일시 |
| `uvIndex` | 8 | `1.8` | 자외선 지수 |

**✅ 실측 (2026-10-04 02:36)**: `{"dateTime":"2026-10-04 02:36:37","uvIndex":0.4}` — **측정 시각 포함, 현재값 들어옴**(새벽이라 0.4). 단일 객체(장비 1대).

### ★ INT_S06_006 시간대별 이상행동 발생수 통계
`GET /dpg/aicctv/avghour/datas?serviceKey=…`

AI CCTV가 방파제 **위험구역** 안에서 감지한 이상행동 횟수를 시간 단위로 집계.

응답 `data`
```text
data
├─ south  (남방파제) { hour, warningFallDetectionCount, warningInsideDetectionCount, warningApproachCount, normalFallDetectionCount }
└─ north  (북방파제) { 같은 구조 }
```

| 필드 | 크기 | 샘플(남) | 설명 |
|---|---|---|---|
| `hour` | 50 | `2026-07-08 08:00:00` | 집계 시각(시간 단위) |
| `warningFallDetectionCount` | 8 | `0` | 위험구역 내 **실족 감지** |
| `warningInsideDetectionCount` | 8 | `0` | 위험구역 내 **침입 감지** |
| `warningApproachCount` | 8 | `37` | 위험구역 내 **접근 경고** |
| `normalFallDetectionCount` | 8 | `0` | **보행자 이상 감지** (일반 구역 쓰러짐으로 추정) |

**✅ 실측 확인**: `south`/`north` 각각 **시간대별 배열**(`hour` 오름차순). 원본 [`raw/live/S06_006_abnormal.json`](raw/live/S06_006_abnormal.json). 최신 시각 2026-10-03 18:00 → **약 하루 지연, 시간 단위 갱신**.

2026-10-03 실측 예:
| 구역 | 17:00 | 18:00 |
|---|---|---|
| 남방파제 | 침입 9, 접근경고 7, 보행자이상 1 | 보행자이상 5 |
| 북방파제 | 접근경고 2, 보행자이상 1 | 보행자이상 1 |

> 평상시에도 접근경고/침입이 두 자릿수로 찍힌다 → **절대값보다 "같은 시간대 평소 대비 급증"으로 위험을 판단**하는 게 안전. 과거 누적으로 시간대별 baseline을 만들 수 있다.

---

## 4. 우리 서비스 관점 분석

### 기획서(PROJECT.md)와의 차이 — 중요
| 기획서 가정 | 실제 공개 API |
|---|---|
| 침수 **예측** (강우+월파 통합 예측) | **예측값 API 없음.** 수위·맨홀·하천·강우 센서의 **현재값 + 등급**만 제공 |
| 방파제 보행자 **이동 예측** · 안전 **경보** | **예측/경보 API 없음.** AI CCTV 이상행동 **시간대별 횟수** + 자외선 지수만 제공 |
| 파고·월파 | 없음 (해수부 파고 예측 API 등 외부 데이터로 보완 필요) |

→ "DT가 예측해서 알려준다"가 아니라 **"DT 실측값을 우리가 해석해 일정 위험도로 바꾼다"** 로 서사를 바꿔야 한다. 평가표의 *"DT 데이터를 가공·해석해서 행동으로 연결"* 에는 오히려 더 잘 맞는다.

### 활용 가능한 신호
| 신호 | 출처 | 일정에 주는 의미 |
|---|---|---|
| 지표면/맨홀/하천 수위 `level` 상승 | S05_006 | 해당 센서 주변 저지대 동선·장소 침수 위험 → 우회/실내 대체 |
| 강우량계 값 | S05_006 (`sensorType` 강우) | 침수 전조, 야외 일정 영향 |
| 방파제 접근경고·침입·실족 횟수 급증 | S06_006 | 남/북 방파제 방문 자제·시간 조정 |
| 자외선 지수 | S06_005 | 어르신 동행 시 그늘·실내 일정 우선 (보조 신호) |
| 장비 위치 | S05_001, S06_001 | 센서 → 영향 반경 → 일정 장소 매핑 |

### 미결 / 다음 할 일
- [x] `serviceKey` 발급 + **12개 전부 활용신청·동작 확인** (실측 응답 `raw/live/`에 저장)
- [x] `sensorType` 실제 값 4종(HOLE/ROAD/RIVER/RAIN) 확인, south/north 배열 구조 확인
- [x] 실시간 자외선·이상행동은 현재값 들어옴 확인 (수위는 시각 필드 없음)
- [ ] **`level` 등급 체계·`value` 단위·임계값** 확인 (평상시 전부 level 1 → 포털 문의 또는 악천후 관측 필요) — **위험 판정의 최대 공백**
- [ ] 갱신 주기·호출 제한(rate limit) 확인 → 폴링 간격 결정
- [ ] 이상행동 시간대별 **baseline(평소값)** 산출용 과거 데이터 적재
- [ ] 수위 응답에 시각이 없으므로 **수집시각 자체 기록** 설계
