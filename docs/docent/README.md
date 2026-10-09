# docent — 사용자 맞춤 여행 가이드 설계

> 상태: **docent 서비스 API 구현됨** (2026-10-09, §5: 메모리·검색·페이지 읽기·가이드 노트 저장). agent 쪽 도구(`remember`·`web_search`·가이드 준비 작업 등)는 아직 없다.
> 구현하면서 바뀐 부분은 이 문서를 같이 고친다.

우리 서비스의 두 번째 특징은 **사용자 맞춤형 여행 가이드**다. 같은 장소라도 "역사·사진을 좋아하고 어머니와 함께 오는 서윤"에게 맞는 이야기를 한다.
지금 places 에 모은 관광 정보(소개문·오디오 가이드 대본)만으로는 깊이와 다양성이 부족하므로 **외부 검색**으로 자료를 보강한다.

핵심 흐름은 세 단계다.

```
① 사용자 이해          ② 여행 전 자료 준비                    ③ 여행 중 가이드
메모리 문서     ──►   LLM 이 자료 검색 → 장소별 가이드 노트   ──►   도착 카드 → 준비된 노트로 즉시 해설
(관심사·스타일·        (내부 근거 + 외부 검색, 출처 포함)            추가 질문 → 노트·검색으로 답변
 동행자·이미 아는 것)                                                  피드백("더 짧게") → 메모리 갱신
```

- **준비는 여행 전에, 전달은 여행 중에.** 현장에서 검색·생성을 하면 느리고 품질이 흔들린다. 여행 전에 만들어 둔 가이드 노트를 현장에서 바로 꺼낸다.
- **판단·저장은 docent 서비스, LLM 작업은 agent.** 지금 구조(planner 가 저장·검증, agent 가 LLM)와 같은 원칙이다. 서비스끼리 코드를 import 하지 않는다 (`docs/COLLABORATION.md` §2).

---

## 1. 사용자 메모리 문서

LLM 이 대화에서 알게 된 사용자 정보를 **문서로 정리해 두고** 모든 턴(계획·동행)에서 참고한다.

### 1.1 범위와 분류

| 범위 | 내용 | 예 |
|---|---|---|
| `user` (계정 단위, 여행 사이에 유지) | 관심사, 해설 스타일, 음식 취향, 피하고 싶은 것 | "역사·근대 건축과 사진을 좋아함", "해설은 핵심만 짧게" |
| `plan` (이번 여행 단위) | 동행자, 이번 여행 목적, 이동 제약, 이미 본 것 | "어머니(60대) 동행, 오래 걷기 부담", "과메기는 꼭 먹고 싶어함" |

| `category` | 쓰임 |
|---|---|
| `interest` 관심사 | 자료 검색 방향, 해설에서 강조할 점 |
| `style` 해설 스타일 | 길이(짧게/자세히), 톤, 수치·연도 선호 |
| `companion` 동행자·제약 | 걷기·계단·휴식, 아이 눈높이 |
| `food` 음식 | 추천·식당 |
| `avoid` 피하고 싶은 것 | 추천·해설에서 제외 |
| `known` 이미 아는 것·본 것 | 같은 설명 반복 방지 |
| `feedback` 반응 | "이 이야기 좋았어요" 같은 반응 |

### 1.2 규칙
- **사용자가 말했거나 확인한 것만** 저장한다. 추측은 저장하지 않는다 (나이·건강 같은 민감한 추론 금지).
- 항목마다 근거(`source`: 대화 메시지 id 또는 `user_edit`)와 시각을 남긴다.
- 사용자가 앱에서 보고 지울 수 있다 (투명성). 탈퇴 시 삭제 (`DELETE /docent/me`).
- 시스템 프롬프트에는 **요약본**(분류별 몇 줄)만 넣는다. 전체는 도구로 본다.
- 충돌하면 최신 것이 이긴다 ("자세히 해줘" → 이전 "짧게"를 대체).

### 1.3 LLM 도구 (agent)

| 도구 | 동작 |
|---|---|
| `remember` | `{scope, category, text}` 저장. 같은 뜻의 항목이 있으면 갱신 |
| `forget` | 항목 삭제 (사용자가 "그건 아니야"라고 할 때) |
| `view_memory` | 전체 보기 |

---

## 2. 자료 검색

| 출처 | 도구 | 내용 | 신뢰 |
|---|---|---|---|
| places 근거 조각 (있음) | `search_passages` → `GET /places/passages?q=&place_id=` | 관광공사 소개문, 오디오 가이드 대본 | 공식 (높음) |
| 장소 상세 (있음) | `get_place` | 운영시간·메뉴·이미지 | 공식 |
| **외부 웹 검색 (새로)** | `web_search` → `GET /docent/search?q=` | 지자체·박물관 공식 페이지, 언론, 블로그 후기 | 출처별 등급 |
| **페이지 읽기 (새로)** | `read_page` → `GET /docent/page?url=` | 검색 결과 본문 (텍스트만, 길이 제한) | 출처별 등급 |

### 2.1 외부 검색 공급자 — OpenAI 내장 웹 검색으로 결정 (2026-10-09)
- **가이드 노트 준비·여행 중 추가 질문의 외부 검색은 OpenAI Responses API 내장 `web_search` 도구를 쓴다.** agent 의 기본 LLM 이 이미 OpenAI(Responses API)라
  키 발급 없이 바로 쓸 수 있고, 검색·페이지 읽기·요약을 모델이 한 번에 하며 출처 URL 인용(`url_citation`)이 붙는다. 공지사항 같은 페이지도 대부분 이것으로 읽는다.
- 인용 URL 은 docent 의 출처 등급 규칙(§2.2, `app/docent/tiers.py`)으로 분류해 노트 `sources` 에 저장한다 (신뢰도 표시 유지).
- 브라우저(Chromium) 기반 읽기는 하지 않는다 (서버 메모리·속도·보안 부담. 로그인·JavaScript 전용 페이지는 범위 밖).
- 아래 네이버 연동(`/docent/search`)과 페이지 읽기(`/docent/page`)는 지우지 않고 **대안**으로 둔다: 로컬 LLM(`AGENT_PROVIDER=local`)일 때, 또는 내장 검색의 한국 지역 정보가 부족할 때.
- 데모 전에 구룡포 장소 2~3곳으로 내장 검색 결과(한국어 지역 정보 양·출처)를 확인하고 이 절에 기록한다.

#### 대안: 네이버 검색 API (구현됨)
docent 가 공급자를 감싸서 같은 형식으로 돌려준다 (agent 는 공급자를 모른다). 공급자 키는 docent 서비스에만 둔다.

- 인터페이스: `app/docent/search.py` 의 `SearchProvider` (`name`, `async search(q, limit) -> [RawResult]`). 공급자를 바꾸거나 늘리려면 이것을 구현하고 `get_search_provider` 에서 고른다.
- **네이버**(`NaverSearch`): 키 `DOCENT_NAVER_CLIENT_ID` / `DOCENT_NAVER_CLIENT_SECRET` (`backend/.env`). 키가 없으면 `/docent/search` 는 `503 search_unavailable`, 나머지 API 는 그대로 동작.
  - 웹문서(`webkr`)·뉴스(`news`)·블로그(`blog`) 세 가지를 동시에 부르고 **번갈아 섞는다** (웹문서 1위 → 뉴스 1위 → 블로그 1위 → 웹문서 2위 …). 같은 URL 은 한 번만.
  - 제목·요약의 `<b>` 강조 태그와 HTML 엔티티를 지운다. 뉴스는 네이버 뉴스 주소 대신 언론사 원문 주소(`originallink`)와 발행일(`pubDate`), 블로그는 작성일(`postdate`)을 `published` 로.
  - 호출 한도: 애플리케이션당 하루 25,000회. 검색 한 번에 3회를 부르지만 같은 질의는 하루 캐시라 해커톤 규모에서는 충분하다.

처음 검토한 후보:

| 후보 | 장점 | 고려할 점 |
|---|---|---|
| 네이버 검색 API (웹문서·블로그·뉴스·백과) | 한국 지역 정보가 가장 많음 | 결과가 요약 조각뿐 → `read_page` 필요, 블로그 비중 큼 |
| OpenAI Responses API 내장 `web_search` | agent 가 이미 Responses API 사용, 구현 최소 | OpenAI 에 묶임 (로컬 LLM 일 때 못 씀), 출처 형식이 다름 |
| Tavily·Brave 등 검색 API | 본문 추출까지 한 번에 | 한국 지역 정보 커버리지 확인 필요 |

권장: **인터페이스는 공급자 중립**으로 두고 처음엔 하나만 붙인다. 한국 지역 정보가 중요하므로 네이버 검색 API 를 1순위로 검토한다. 호출 한도·약관은 붙이기 전에 확인한다.

### 2.2 검색 결과 형식과 출처 등급

```jsonc
// GET /docent/search?q=구룡포 일본인가옥거리 역사&limit=5
{"results": [{"title": "…", "url": "https://…", "snippet": "…", "published": "2024-05-01",
              "tier": "official"}],     // official 공공·기관 | press 언론 | community 블로그·카페 | unknown
 "provider": "naver", "cached": false}
```
- `tier` 는 도메인 규칙으로 정한다 (`app/docent/tiers.py`, 모바일 주소의 `m.` 는 떼고 비교):
  - official: `go.kr`·`or.kr`·`re.kr`·`ac.kr`·`mil.kr` 로 끝나는 도메인, `grandculture.net`(한국향토문화전자대전)
  - press: 통신사·종합지·경제지·방송사, 대구·경북 지역지(매일신문·영남일보·경북일보·경북매일 등), 네이버·다음 뉴스
  - community: 네이버 블로그·카페·포스트·지식iN, 티스토리, 브런치, 다음 블로그·카페, SNS·유튜브, 나무위키·위키백과, 트립어드바이저 등
  - 그 밖은 unknown
- `limit` 은 1~10 (기본 5). `published` 는 공급자가 알려 줄 때만 `YYYY-MM-DD`.
- 사실(연도·인물·사건)은 official/press 로 확인된 것만 단정해서 말한다. community 정보는 "방문 후기로는 …"처럼 구분한다.
- 검색·페이지 결과는 캐시한다 (`docent_search_cache`. 같은 질의(공급자·검색어·limit)·같은 URL 은 하루, `DOCENT_CACHE_TTL_SEC`). 캐시에서 나오면 `"cached": true`. 실패한 결과는 캐시하지 않는다.

### 2.3 페이지 읽기 안전
`GET /docent/page?url=` 는 서버가 외부 URL 을 가져오므로 (`app/docent/page.py`):
- `http`/`https` 만, 사용자 정보가 든 주소(`user:pw@`) 금지 → `422 invalid_url`.
- **사설·루프백·링크로컬 IP 는 막는다** (SSRF) → `403 url_blocked`. 호스트를 직접 DNS 조회해서 나온 주소가 **하나라도** 공인(global) 유니캐스트가 아니면 막는다.
  사설(10/8·172.16/12·192.168/16)·루프백·링크로컬(169.254/16 클라우드 메타데이터 포함)·CGNAT(100.64/10)·0.0.0.0·멀티캐스트·예약, IPv6 루프백·ULA·링크로컬,
  IPv4-mapped(`::ffff:127.0.0.1`)·NAT64·6to4 안에 든 내부 IPv4, `2130706433`·`0x7f.1` 같은 숫자 표기, compose 서비스 이름(`gateway`, `db` → 사설 IP)까지.
- 검사한 IP 로 **직접 연결**한다 (Host 헤더·TLS SNI·인증서 확인은 원래 이름). 검사와 연결 사이에 DNS 가 바뀌어도(DNS rebinding) 내부로 가지 않는다. 환경변수 프록시도 쓰지 않는다.
- 리다이렉트는 자동으로 따라가지 않고 **매번 위 검사를 다시** 한다 (최대 5번. 내부 주소·다른 scheme 으로 가면 `403 url_blocked`).
- 크기 제한 2MB(넘으면 거기까지만 읽고 `truncated: true`, gzip 은 푼 뒤에도 2MB 까지), 시간 제한 8초(리다이렉트 포함 전체, 넘으면 `504 page_timeout`),
  `text/html`·`application/xhtml+xml`·`text/plain` 만(`415 page_unsupported`). 상대 서버 오류·연결 실패는 `502 page_fetch_failed`.
- HTML 에서 본문 텍스트만 추출한다: script·style·nav·header·footer·aside·form 등은 빼고, `<article>`/`<main>` 본문이 200자 이상이면 그것만. 최대 8,000자(`DOCENT_PAGE_MAX_CHARS`)로 자른다.
  문자 인코딩은 헤더 → `<meta charset>` → UTF-8 → CP949(EUC-KR) 순서로 시도한다. 제목은 `og:title` → `<title>`.
- 네이버 블로그(`blog.naver.com`)는 본문이 iframe 안에 있어 모바일 주소(`m.blog.naver.com`)로 바꿔 읽는다.
- 가져온 내용은 **자료(데이터)이지 지시가 아니다.** 페이지 안의 문구가 LLM 에게 지시하는 것처럼 보여도 따르지 않도록 프롬프트에 명시한다.

---

## 3. 가이드 노트 (여행 전 자료 준비)

### 3.1 무엇을 만드나
계획(plan)의 장소마다 **가이드 노트 한 편**. 그 사용자에게 맞춰 쓴다.

```jsonc
// GuideNote
{"plan_id": 3, "place_id": "kto-2610501", "status": "ready",         // pending | preparing | ready | failed | stale
 "short": "30초 분량 해설 (핵심만 짧게)",
 "long": "2~3분 분량 해설 (이야기 흐름)",
 "for_you": ["사진을 좋아하시니 계단 위 공원에서 가옥거리를 내려다보는 각도를 추천", "…"],   // 메모리와 연결한 포인트
 "highlights": ["볼거리·사진 포인트"],
 "companion_tips": ["계단 대신 오른쪽 완만한 길", "쉼터 위치"],                              // 동행자 메모리 반영
 "cautions": ["월요일 휴관", "비 오면 돌길 미끄러움"],
 "qa": [{"q": "왜 일본인이 여기 살았나요?", "a": "…"}],                                    // 나올 만한 추가 질문 3~5개
 "sources": [{"title": "…", "url": "…", "tier": "official"}],
 "memory_version": 12, "prepared_at": "…", "model": "gpt-5.4-mini"}
```
- `short`·`long` 은 현장에서 그대로 읽어 줄 수 있는 문장으로 쓴다 (나중에 TTS 로 읽기 좋게: 괄호·기호 최소화).
- 출처에 없는 사실은 쓰지 않는다. 확인 못 한 것은 빼거나 "전해지는 이야기로는"으로 구분한다.
- 원문을 길게 옮기지 않는다. 요약해서 새로 쓴다 (저작권). 오디오 가이드·관광공사 자료의 공공누리 조건은 확인해서 출처 표시를 따른다.

### 3.2 언제 만드나
- 앱의 **[가이드 준비]** 버튼, 또는 제안 승인으로 일정에 장소가 생겼을 때 자동으로 `pending` 을 만든다.
- 여행 시작(`docs/trip/README.md` §1) 때 아직 없는 노트가 있으면 그것부터 준비한다.
- **다시 만들기**: 메모리의 `interest`·`style`·`companion` 이 바뀌면(추가·수정·삭제) docent 가 **자동으로** `ready` 노트를 `stale` 로 표시한다
  (user 범위 항목이면 그 사용자의 모든 계획, plan 범위 항목이면 그 계획). `POST …/stale` 로 직접 표시할 수도 있다. 현장에서는 `stale` 이어도 그대로 쓰고(빠름), 여유가 있을 때 다시 만든다. 장소가 일정에서 빠지면 노트는 남겨 둔다 (다시 넣을 수 있음).

### 3.3 누가 어떻게 만드나 (agent 의 준비 작업)

```
POST /agent/plans/{plan_id}/guide/prepare   → 202 {"job_id"}     (앱·trip 이 부름)
GET  /agent/plans/{plan_id}/guide/jobs/{job_id}                   진행 상태
```
장소마다 LLM 작업 루프 (대화 세션과 별개, 계획당 작업 하나만):
1. `view_memory` + 장소 상세 + `search_passages` (내부 근거부터)
2. 부족하면 `web_search` → `read_page` (장소당 검색 최대 N회, 예: 3회)
3. GuideNote 를 JSON 으로 작성 → `PUT /docent/plans/{plan_id}/notes/{place_id}` 저장
4. 출처가 하나도 없으면 `failed` (지어내지 않는다)

비용·시간 감: 장소당 도구 호출 4~8회. gpt-5.4-mini 기준 장소 6곳이면 1~2분 정도 예상 (실측해서 이 문서에 기록한다). 여행 전에 미리 돌리므로 현장 지연에는 영향이 없다.

---

## 4. 여행 중 가이드

| 상황 | 처리 | LLM |
|---|---|---|
| 도착 (`arrived` 이벤트, `docs/trip/README.md` §3) | 해설 카드 "이야기 들어볼까요? [짧게] [자세히]" | X |
| [짧게]/[자세히] 누름 | 가이드 노트의 `short`/`long` 을 **바로** 보여준다 (준비돼 있으면 LLM 없이). 메모리 `style` 이 "짧게"면 [짧게]를 기본으로 강조 | 노트가 없을 때만 즉석 생성 |
| 추가 질문 ("저 건물은 언제 지어졌어요?") | Agent 동행 모드 턴. 도구 `get_guide_note` → `search_passages` → (필요하면) `web_search` 순서 | O |
| 반응 ("더 짧게 해줘", "이런 얘기 좋다") | `remember` 로 메모리 갱신 → 다음 장소부터 반영 | O |
| 이미 들은 이야기 | 메모리 `known` 에 기록 → 다음 노트·답변에서 반복하지 않음 | O |

Agent 동행 모드 도구 추가: `get_guide_note(place_id)`, `search_passages`, `web_search`, `read_page`, `remember`/`forget`/`view_memory`.

음성(TTS)은 노트 `short`/`long` 을 그대로 읽으면 되므로 나중에 붙이기 쉽다 (이번 범위 밖, 시간이 남으면).

---

## 5. API (docent 서비스, `/docent`)

docent 는 **저장·검색·외부 요청 대행**만 한다 (LLM 없음, 결정론적). 코드: `backend/app/docent/`, 테스트: `backend/tests/docent/`, 문서: `/docent/docs`.

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/docent/memory?plan_id=` | 메모리 전체 `{items, memory_version, style_length}`. user 범위 + (plan_id 가 있으면) 그 plan 범위, 최신순 |
| POST | `/docent/memory` | `{scope, plan_id?, category, text, source}` 추가 → 201. 같은 범위·분류에 **글자가 같은** 항목이 있으면 새로 만들지 않고 source·시각만 갱신 → 200. 같은 뜻의 다른 문장은 agent 가 id 로 PATCH |
| PATCH·DELETE | `/docent/memory/{id}` | 수정(`category`·`text`·`source` 중 일부)·삭제 → 204 (앱 편집, agent `forget`) |
| GET | `/docent/memory/summary?plan_id=` | 프롬프트용 요약 `{text, count, memory_version, style_length}` |
| GET | `/docent/search?q=&limit=` | 외부 검색 (§2.1~2.2) |
| GET | `/docent/page?url=` | 페이지 본문 `{url, title, text, truncated, tier, fetched_at, cached}` (§2.3) |
| GET | `/docent/plans/{plan_id}/notes` | 노트 목록 `[{plan_id, place_id, status, memory_version, prepared_at, updated_at}]` |
| GET·PUT | `/docent/plans/{plan_id}/notes/{place_id}` | 노트 조회·저장 (PUT 은 전체 교체, 없으면 만든다 201) |
| POST | `/docent/plans/{plan_id}/notes/{place_id}/stale` | 다시 만들기 표시 (`ready` 만 `stale` 로, 나머지 상태는 그대로) |
| DELETE | `/docent/me` | 탈퇴: 이 사용자의 메모리·노트 삭제 → 204 (검색 캐시는 사용자와 무관) |

테이블(`docent_*`): `docent_memory`, `docent_memory_version`(사용자별 메모리 번호), `docent_guide_note`, `docent_search_cache`.

### 5.1 메모리 규칙
- `scope=plan` 이면 `plan_id` 필수, `scope=user` 면 `plan_id` 를 넣지 않는다 (아니면 422). `text` 1~500자, `source` 1~100자 필수.
- `memory_version`: 사용자 메모리 내용이 바뀔 때(추가·수정·삭제)마다 1 오른다 (user·plan 범위 공통 번호). source 만 바뀐 수정은 올리지 않는다.
  가이드 노트를 만들 때 본 번호를 노트의 `memory_version` 에 넣는다.
- 요약 형식: 분류마다 한 줄, 최신 5개, 최신이 앞. 순서는 해설 스타일 → 관심사 → 동행자·제약 → 피하고 싶은 것 → 음식 → 이미 아는 것·본 것 → 반응. 메모리가 없으면 빈 문자열.
  ```
  - 해설 스타일: 해설은 핵심만 짧게
  - 관심사: 사진; 역사·근대 건축
  - 동행자·제약: 계단은 피하고 싶음; (이번 여행) 어머니(60대) 동행
  ```
- `style_length`: 해설 길이 선호 `short` | `long` | `null` (앱 16 의 [짧게]/[자세히] 강조, app gaps b19). 보이는 `style` 항목을 최신부터 보며
  문구에 짧·핵심·간단·요약이 있으면 `short`, 자세·길게·깊이·상세가 있으면 `long` (둘 다 있거나 둘 다 없으면 다음 항목). agent 의 `remember` 는
  길이 선호를 저장할 때 "짧게"·"자세히" 같은 말을 문구에 넣는다.

### 5.2 가이드 노트 저장 규칙 (PUT)
- 본문: §3.1 GuideNote 의 `short`·`long`·`for_you`·`highlights`·`companion_tips`·`cautions`·`qa`·`sources` + `status`·`memory_version`·`model`·`error`(failed 이유).
- `status=ready` 는 `short`·`long` 과 **출처 하나 이상**이 있어야 한다 (없으면 422. 출처가 없으면 `failed` 로 저장한다).
- 출처마다 `tier` 는 **보낸 값을 무시하고** docent 가 `url` 의 도메인 규칙(§2.2, `tiers.py`)으로 다시 정해 저장한다 (LLM 이 붙인 등급을 믿지 않는다). `url` 이 없으면 `unknown`.
- 본문을 비워 보낸 `pending`·`preparing`·`failed` 는 **이전 본문을 남긴다** (다시 만드는 중이거나 실패해도 현장에서 이전 노트를 쓸 수 있게).
- `prepared_at` 은 서버가 `ready` 로 저장한 시각.

### 5.3 인증·계획 소유·오류
- 인증·호출 규칙은 다른 서비스와 같다 (`app.core.auth`, 게이트웨이 경유).
- 계획 소유 확인은 planner `GET /planner/plans/{id}` 를 사용자 인증 그대로 HTTP 로 부른다 (`DOCENT_API_BASE_URL`, compose 는 `http://gateway`).
  다른 사용자의 계획이면 `404 plan_not_found`, planner 에 접근할 수 없으면 `503 planner_unavailable`. 메모리 PATCH·DELETE 는 docent 에 저장된 사용자 id 로 확인한다.
- 오류 형식은 planner 와 같다: `{"detail": {"code", "message", ...}}`. 요청 형식 오류는 `422 invalid_request`.

| 코드 | 상태 | 뜻 |
|---|---|---|
| `memory_not_found` / `note_not_found` / `plan_not_found` | 404 | 없거나 다른 사용자의 것 |
| `search_unavailable` | 503 | 검색 키 없음 또는 공급자 오류 (`reason`) |
| `planner_unavailable` | 503 | 계획 소유를 확인할 수 없음 |
| `invalid_url` | 422 | http/https 가 아님, 형식 오류, 사용자 정보 포함 |
| `url_blocked` | 403 | 내부·사설 주소 (리다이렉트 포함) |
| `page_unsupported` | 415 | HTML·텍스트가 아님 |
| `page_fetch_failed` | 502 | 연결 실패, 상대 오류(`upstream_status`), 리다이렉트 초과 |
| `page_timeout` | 504 | 8초 초과 |

### 5.4 설정 (`backend/.env`, 접두사 `DOCENT_`)
| 변수 | 기본 | |
|---|---|---|
| `DOCENT_NAVER_CLIENT_ID` / `_SECRET` | (비어 있음) | 네이버 검색 API 키 |
| `DOCENT_CACHE_TTL_SEC` | 86400 | 검색·페이지 캐시 |
| `DOCENT_PAGE_TIMEOUT_SEC` / `_MAX_BYTES` / `_MAX_CHARS` / `_MAX_REDIRECTS` | 8 / 2MB / 8000 / 5 | 페이지 읽기 제한 |
| `DOCENT_API_BASE_URL` | compose 가 `http://gateway` | planner 호출 |

## 6. 이번 일정에서의 범위 (D-4 기준 권장)

| 우선 | 범위 |
|---|---|
| **꼭** | 메모리 문서(도구 3개 + 프롬프트 요약), `search_passages` 도구, 가이드 노트 준비 작업(내부 근거 + 외부 검색 1개 공급자), 도착 카드 → 노트 표시, 추가 질문 답변 |
| 있으면 좋음 | 앱에서 메모리 보기·지우기, `stale` 자동 재생성, 출처 등급 규칙 정교화 |
| 나중 | TTS 음성, 사진 포인트 이미지, 노트 공유 |

데모 장면 ②("일본인가옥거리 도착 → 관심사 반영 해설 → '핵심만 짧게' 선호 기억")가 이 범위로 그대로 시연된다.

## 7. 결정 필요
- [x] 외부 검색 공급자 (§2.1): 네이버 검색 API 로 시작 (인터페이스는 공급자 중립)
- [x] 메모리를 docent 에 둘지 agent(`agent_*` 테이블)에 둘지: docent 에 둔다 (계획·동행 모두 쓰고, 앱 편집 API 가 필요해서)
- [ ] 가이드 준비 자동 시작 시점 (제안 승인 직후 vs [가이드 준비] 버튼만)
