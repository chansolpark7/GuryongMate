# routing 서비스 — 길찾기 API

OpenStreetMap(OSM) 포항 데이터로 **직접 띄운 길찾기 엔진(Valhalla)** 을 감싼 API다. 외부 지도 API를 쓰지 않는다.
이동 수단은 **도보·차량** 두 가지다 (대중교통은 지원하지 않는다).

- 코드: `backend/app/routing/` · 엔진: `services/valhalla/` · 테스트: `backend/tests/routing/` · API 문서: `/routing/docs`
- 쓰는 곳: planner 일정 계산(이동시간), 앱 지도(경로선·안내), Agent(위험 구역 우회 설명)

```
planner ──POST /routing/legs──┐
앱·Agent ──POST /routing/route─┤──► routing ──► valhalla (내부 전용, :8002)
                               │       └──GET /safety/avoid──► safety (위험 구역)
```

**왜 직접 띄우나**
1. planner는 일정을 한 번 바꿀 때마다 구간 수십 개의 이동시간을 계산한다 (추천 위치 계산은 위치마다 다시). 외부 API로는 호출량·지연이 감당이 안 된다.
2. 위험 구역(침수·방파제)을 피하는 경로를 요청마다 계산할 수 있다. 외부 API로는 불가능하다.
3. 키·할당량·네트워크와 상관없이 같은 입력에 같은 결과 → 시연·테스트가 안정적이다.
4. 지도에 그리는 경로선과 플래너가 계산한 시간이 같은 엔진에서 나온다.

지도 화면·장소 검색은 Kakao를, 실제 차량 내비는 Kakao Navi를 그대로 쓴다. 이 서비스는 그 둘을 대신하지 않는다.

---

## 1. 엔드포인트

| 메서드 | 경로 | 하는 일 | 주 사용처 |
|---|---|---|---|
| `POST` | `/routing/legs` | 구간 목록(최대 300)의 이동시간·거리 | planner |
| `POST` | `/routing/matrix` | 출발지 × 도착지(각 최대 50) 이동시간 행렬 | Agent 추천·순서 비교 |
| `POST` | `/routing/route` | 경유지(2~20)를 잇는 경로선 + 한국어 회전 안내 + 위험 구역 확인·우회 | 앱 지도, Agent |
| `GET` | `/routing/status` | 엔진 상태·데이터 시각 | 운영 확인 |

공통 규칙
- 좌표는 `{"lat": 35.99, "lng": 129.56}` (WGS84, Kakao 지도와 같아서 변환이 필요 없다).
- 이동 수단 `mode`: `walk`(기본) | `car`.
- 걷기 옵션 (`legs`·`matrix`·`route` 공통)
  - `walking_speed_kmh`: 0.5~10. 생략하면 4.2 (= 70m/분, 어르신 동행 기준. Valhalla 기본 5.1보다 느리게).
  - `easy_walk`: `true`면 계단·언덕을 피하고 3.6km/h로 계산한다 ("오래 걷기 부담" 사용자).
- 정의되지 않은 필드는 422다.
- **실패하지 않는다.** 엔진이 꺼졌거나 그 구간의 길을 못 찾으면 직선거리 × 1.3으로 근사하고 `source: "estimate"`로 표시한다.
  엔진 응답 여부는 `engine.ok`로 따로 알려 준다.
  - `source: "road"` = 실제 길로 계산 / `"estimate"` = 근사 (앱은 "약 9분"처럼 표시)
- 시간(`minutes`)은 분 단위 올림이다. 같은 지점이면 0. 차량 시간에는 **주차·승하차 시간이 들어 있지 않다** (planner가 5분을 더한다).

### POST /routing/legs
```json
{"mode": "walk",
 "legs": [{"from": {"lat": 35.989305, "lng": 129.555642}, "to": {"lat": 35.990621, "lng": 129.560455}},
          {"from": {"lat": 35.990621, "lng": 129.560455}, "to": {"lat": 35.992740, "lng": 129.561799}}]}
```
(구룡포항 → 일본인 가옥거리 → 과메기문화관)
```json
{"mode": "walk",
 "legs": [{"minutes": 9, "distance_m": 625, "source": "road"},
          {"minutes": 10, "distance_m": 584, "source": "road"}],
 "engine": {"ok": true, "error": null}}
```
- 요청 안의 고유 지점으로 엔진 행렬을 **한 번만** 부른다. 계산 결과는 하루 동안 캐시한다 (근사값은 캐시하지 않는다).
- 한 지점이 도로에서 너무 멀어 행렬 전체가 실패하면 구간별로 다시 묻는다. 그 구간만 `estimate`가 된다.

### POST /routing/matrix
```json
{"mode": "car", "sources": [{"lat": …, "lng": …}], "targets": [{…}, {…}]}
```
응답 `matrix[i][j]`는 `sources[i] → targets[j]`의 `{minutes, distance_m, source}`다.

### POST /routing/route
```json
{"mode": "walk",
 "stops": [{"lat": 35.9893, "lng": 129.5556}, {"lat": 35.9906, "lng": 129.5605}, {"lat": 35.9927, "lng": 129.5618}],
 "hazards": "avoid",
 "steps": true}
```
| 필드 | 설명 |
|---|---|
| `stops` | 출발 → 경유 → 도착 순서 (2~20개). 순서는 바꾸지 않는다 |
| `hazards` | `ignore`(기본): 위험 구역을 보지 않음 / `report`: 경로가 지나는 위험 구역만 알림 / `avoid`: 우회 |
| `now` | 위험 구역 판단 기준 시각 (테스트·시연용, safety에 그대로 넘긴다) |
| `steps` | 회전 안내 포함 (기본 `true`) |

응답 (실제 결과를 줄임. safety를 폭풍 mock으로 띄우고 구룡포항 → 구룡포교 방향 해안 도로, `hazards: "avoid"`)
```jsonc
{"mode": "walk", "source": "road", "minutes": 12, "distance_m": 791,
 "geometry": {"type": "LineString", "coordinates": [[129.555705, 35.989352], …]},   // [lng, lat] GeoJSON 순서
 "legs": [{"minutes": 12, "distance_m": 791, "steps": […]}],
 "hazards": {
   "checked": true,
   "on_route": [{"id": "flood_1", "name": "구룡포교_하천수위계 주변", "kind": "flood", "risk": "caution", "reason": "…"}],
   "avoided":  [{"id": "flood_8", "name": "구룡포파출소_지표면 수위계 주변", "kind": "flood", "risk": "caution", "reason": "구룡포파출소_지표면 수위계 수위 등급 2 (주의)"}],
   "baseline": {"minutes": 8, "distance_m": 549},       // 우회하지 않은 원래 경로
   "detour_minutes": 4,
   "note": "출발지·경유지·도착지가 들어 있는 위험 구역은 우회하지 못했다."},
 "engine": {"ok": true, "error": null}}
```
같은 구간을 우회 없이 요청했을 때의 `steps` (8분 · 549m)
```jsonc
[{"type": "start", "instruction": "출발해 50m 이동", "street": null, "distance_m": 46, "seconds": 40, "begin_index": 0},
 {"type": "keep_left", "instruction": "왼쪽 길 유지 후 40m 이동", "street": null, "distance_m": 40, "seconds": 38, "begin_index": 4},
 {"type": "slight_left", "instruction": "호미로로 왼쪽 방향 후 340m 이동", "street": "호미로", "distance_m": 343, "seconds": 298, "begin_index": 10},
 {"type": "left", "instruction": "좌회전 후 10m 이동", "street": null, "distance_m": 8, "seconds": 7, "begin_index": 19},
 {"type": "right", "instruction": "동해안 자전거길로 우회전 후 110m 이동", "street": "동해안 자전거길", "distance_m": 111, "seconds": 96, "begin_index": 21},
 {"type": "arrive", "instruction": "왼쪽에 목적지", "street": null, "distance_m": 0, "seconds": 0, "begin_index": 23}]
```
- `geometry`는 경로 전체 한 줄이다. 각 안내의 `begin_index`가 `geometry.coordinates`에서 그 안내가 시작되는 위치다 (다음 안내 표시·현재 위치 매칭에 쓴다).
- 엔진이 없거나 길을 못 찾으면 `source: "estimate"`, `geometry`는 경유지를 직선으로 이은 선, `steps`는 빈 목록이다.

---

## 2. 위험 구역 우회 (`hazards`)

### 2.1 흐름
```
safety GET /safety/avoid  ─► 지금 피할 구역 다각형 (침수: 실측 등급이 오른 센서 주변 80m / 방파제: 구역 위험도)
          │
routing   ├─ 1. 일반 경로 계산
          ├─ 2. 경로가 지나는 구역 확인 → on_route          (report 는 여기서 끝)
          ├─ 3. 지나는 구역이 있으면, 경유지가 들어 있지 않은 구역을 모두 막고 다시 계산
          └─ 4. 우회 경로 + 원래 경로 요약(baseline) + 늘어난 시간(detour_minutes)
```
- **무조건 막는 방식**이다 (Valhalla `exclude_polygons`). "되도록 피하기"는 없어서 두 경로를 다 계산해 비교한다. 우회해서 몇 분 늘어나는지 보고 따를지는 Agent와 사용자가 정한다 (일정 변경은 승인 후에만).
- **출발·경유·도착 지점이 들어 있는 구역은 막지 않는다.** 막으면 길 자체가 없어진다. 그런 구역은 `on_route`에 남고 `note`로 알린다. 목적지 자체가 위험하면 경로가 아니라 **일정을 바꿔야** 하고, 이는 `/safety/assess` + Proposal이 맡는다.
- 방파제는 끝이 막힌 길이라 다른 곳으로 가다가 지날 일이 없다. 사실상 우회는 **침수 구역**에서만 일어난다.
- 우회 길이 없으면 원래 경로를 돌려주고 `note`에 이유를 쓴다. safety에 접근할 수 없으면 `checked: false`다 (안전하다는 뜻이 아니다).

### 2.2 어떤 구역을 피하나 (`/safety/avoid`, 자세한 내용: `docs/safety/README.md`)
| 구역 | 넣는 조건 | 다각형 |
|---|---|---|
| 침수 | **그 센서 자신의** 실측 수위 등급이 주의(2) 이상 | 센서 중심 반경 80m 원 (16각형) |
| 방파제 | `/safety/status`의 구역 위험도가 주의 이상 (특보·예보·CCTV) | 방파제 선 + 60m 버퍼 |

- 호우 예보·특보는 모든 침수 구역에 똑같이 적용되므로 넣지 않는다. 넣으면 센서가 몰린 중심가 도로(호미로 일대)가 전부 막힌다.
- 판단용 반경(200m)이 아니라 80m만 막는 것도 같은 이유다.

### 2.3 시연
실제 센서는 평상시라 피할 구역이 없다. safety를 폭풍 mock으로 띄우면 수협(위험)·파출소(주의)·구룡포교(주의) 센서와 두 방파제가 피할 구역이 된다.
```bash
# backend/.env 에 SAFETY_SOURCE=mock, SAFETY_MOCK_SCENARIO=storm 을 넣고
docker compose up -d safety
curl -X POST 127.0.0.1:8000/routing/route -H 'content-type: application/json' \
  -d '{"stops":[{"lat":35.9893,"lng":129.5556},{"lat":35.9872,"lng":129.5512}],"hazards":"avoid","steps":false}'
```

---

## 3. 한국어 안내 문구

Valhalla는 한국어 안내를 지원하지 않는다. 엔진이 주는 회전 종류·도로명·거리로 직접 만든다 (`narrative.py`).

| `type` | 예 |
|---|---|
| `start` | 구룡포길로 출발해 120m 이동 |
| `straight` | 호미로를 따라 300m 직진 |
| `left` / `right` / `slight_left` / `sharp_right` … | 용주로7번길로 우회전 후 30m 이동 |
| `uturn` · `keep_left` · `merge` · `ramp_*` · `exit_*` | 유턴 후 50m 이동 |
| `steps` | 계단으로 40m 이동 |
| `roundabout` / `roundabout_exit` | 회전교차로에서 2번째 출구 |
| `arrive` | 목적지 도착 / 오른쪽에 목적지 / 왼쪽에 목적지 |

- 조사는 받침을 보고 붙인다 (구룡포길**로**, 구룡포항**으로**, 호미로**를**).
- 거리는 10m 단위, 1km부터 `1.2km`.
- 도로명이 없는 골목이 많아 "좌회전 후 30m 이동"처럼 나오는 경우가 많다. 음성 동행에서는 Agent가 주변 장소 이름을 붙여 다듬으면 된다.

---

## 4. 엔진 (`services/valhalla/`)

| 항목 | 값 |
|---|---|
| 이미지 | `ghcr.io/valhalla/valhalla-scripted:3.9.0` + `osmium-tool` |
| 데이터 | Geofabrik 대한민국 OSM(약 280MB)을 받아 포항 영역만 자름 (`128.95,35.80,129.65,36.40`) → 약 6MB |
| 첫 실행 | 다운로드·자르기·고도 데이터·타일 빌드 **약 3분** |
| 볼륨 | `guryongmate-valhalla` (이름 고정, worktree끼리 공유). 두 번째 실행부터 바로 뜬다 |
| 크기 | 타일 18MB + 고도 124MB |
| 메모리 | 약 60MB |

- `OSM_URL`, `OSM_BBOX`, `OSM_FILE` 환경변수로 원본·영역을 바꿀 수 있다 (`prepare.sh`). 영역을 바꾸면 `OSM_FILE`도 바꿔야 다시 빌드한다.
- **데이터 갱신**: `docker volume rm guryongmate-valhalla` 후 `docker compose up -d valhalla` (새 OSM을 받아 다시 빌드).
- 고도(`build_elevation`)는 `easy_walk`의 언덕 회피에 쓴다.
- 행정구역 DB는 끈다 (`build_admins: False`). 포항만 잘라서 국가·도 경계가 깨지고, 한국 기본값(우측통행)으로 충분하다.
- 처음 빌드할 때 여러 worktree에서 동시에 띄우지 않는다 (같은 볼륨에 동시에 쓴다).

---

## 5. 실제 길 vs 이전 근사 (2026-10-04, OSM 2026-10-03 기준)

`python scripts/routing/compare.py` 결과. 근사 = 이전 planner 방식 (직선 × 1.3, 도보 70m/분, 차량 500m/분, 주차 제외).

| 구간 (도보) | 근사 | 실제 길 | 차이 |
|---|---:|---:|---:|
| 구룡포항 → 구룡포장 | 4분 · 240m | 4분 · 247m | +0 |
| 구룡포장 → 일본인 가옥거리 | 11분 · 759m | 10분 · 707m | −1 |
| 일본인 가옥거리 → 근대문화역사관 | 2분 · 135m | 3분 · 131m | +1 |
| 근대문화역사관 → 과메기문화관 | 5분 · 325m | **9분 · 505m** | **+4** |
| 과메기문화관 → 구룡포해수욕장 | 12분 · 791m | 14분 · 953m | +2 |
| 구룡포해수욕장 → 호미곶 해맞이광장 | 166분 · 11.6km | 151분 · 9.9km | −15 |

차량은 시내 구간 ±2분, 호미곶 24분 → 16분이었다.

- 시내 평지는 근사와 거의 같다. 차이는 **언덕·막힌 길**과 **먼 거리**에서 난다.
- **근대문화역사관 → 과메기문화관 (확인 필요)**: 실제로는 가옥거리에서 구룡포공원 계단으로 바로 올라가지만, 엔진은 서쪽 용주로로 돌아간다. OSM에 공원 계단·보행로는 있으나 가옥거리 쪽 길과 이어지지 않았거나, 과메기문화관 좌표가 서쪽 도로에 붙는 것으로 보인다. 현장 동선과 다르면 OSM을 고치거나(편집 후 다음 갱신 때 반영) 장소 좌표를 입구 쪽으로 옮긴다.

---

## 6. 실행·테스트

```bash
docker compose up -d --build routing          # routing + valhalla (첫 실행은 약 3분)
docker compose logs -f valhalla               # 빌드 진행
curl 127.0.0.1:8000/routing/status            # {"engine":{"ok":true,"version":"3.9.0",...}}
docker compose run --rm routing pytest tests/routing
GURYONG_API_URL=http://127.0.0.1:8000 python scripts/routing/compare.py [place_id ...] [--mode car] [--all-pairs]
```
- 게이트웨이(`infra/gateway/Caddyfile`)에 `/routing*`이 추가됐다. 기존 스택에서는 `docker compose restart gateway`가 필요하다.
- 테스트는 가짜 엔진·가짜 위험 구역을 쓴다. 실제 엔진이 없어도 돈다.

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `ROUTING_VALHALLA_URL` | `http://127.0.0.1:8002` (compose: `http://valhalla:8002`) | 엔진 주소 |
| `ROUTING_SAFETY_BASE_URL` | `http://127.0.0.1:8000` (compose: `http://gateway`) | 위험 구역 조회 |
| `ROUTING_WALKING_SPEED_KMH` | `4.2` | 기본 걷는 속도 |
| `ROUTING_VALHALLA_RETRY_SEC` | `20` | 엔진이 죽으면 이 시간 동안은 묻지 않고 바로 근사 |
| `ROUTING_CACHE_TTL_SEC` | `86400` | 구간 캐시 |

## 7. 구조

| 파일 | 역할 |
|---|---|
| `router.py` | API |
| `service.py` | 구간·행렬 계산과 캐시, 경로 조립, 위험 구역 비교·우회 |
| `valhalla.py` | 엔진 HTTP 클라이언트, 경로 모양(polyline6) 복원, 수단별 비용 옵션 |
| `hazards.py` | safety `/safety/avoid` 조회, 경로-다각형 교차 판정 |
| `narrative.py` | 한국어 안내 문구 |
| `estimate.py` | 직선거리 근사 |
| `schemas.py` · `config.py` | 요청·응답, 설정 |

## 8. 한계와 다음 할 일
- **대중교통은 없다.** 필요해지면 GTFS 없이 외부 API(ODsay 등)를 먼 구간에만 쓰는 방식을 검토한다.
- **실시간 교통이 없다.** 차량 시간은 도로 종류별 기본 속도다. 구룡포 규모에서는 충분하다.
- **구간별 수단 자동 선택은 아직 없다.** 지금은 계획 하나에 `walk` 또는 `car`. 먼 구간만 차량으로 계산하려면 planner에 "걷기 상한"을 두고 구간마다 수단을 고르면 된다.
- **위험 구역 우회는 planner 일정 시간에 넣지 않았다.** 미래 일정의 침수 실측은 알 수 없고, 일정 시간이 센서 값에 따라 흔들리지 않게 하기 위해서다. 여행 중 경로 안내(`route`)에서만 쓴다.
- Agent 도구(`경로 조회`)로 `route`를 노출하면 "침수 구역을 피하면 4분 더 걸려요" 같은 설명이 가능하다 (`docs/agent/README.md`).
- OSM 데이터 확인: 시연 동선(가옥거리·구룡포공원 계단·과메기문화관)을 현장과 맞춰 본다 (§5).
