# docent — 사용자 맞춤 여행 가이드 설계

> 상태: **docent 서비스 API 구현됨** (2026-10-09, §5: 메모리·검색·페이지 읽기·가이드 노트 저장).
> **agent 쪽 구현됨** (2026-10-09, §8: 메모리 도구·`search_passages`·가이드 노트·웹 검색·가이드 준비 작업, 가짜 LLM 테스트까지).
> 실제 OpenAI 로 확인하는 것은 아직이다 (§8.4 절차). 구현하면서 바뀐 부분은 이 문서를 같이 고친다.

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
| `remember` | `{scope, category, text}` 저장. 같은 뜻의 항목이 있으면 갱신 (`replace_id`) |
| `forget` | 항목 삭제 (사용자가 "그건 아니야"라고 할 때) |
| `view_memory` | 전체 보기 |

구현: `docs/agent/README.md` §5 (도구·단계 kind `memory` "기억하기"·`forget`·`recall`), §8.1.

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
- 데모 전에 구룡포 장소 2~3곳으로 내장 검색 결과(한국어 지역 정보 양·출처)를 확인하고 이 절에 기록한다 (§8.4 절차).
- 구현 (§8.2): `AGENT_PROVIDER=openai`(Responses API)면 내장 `web_search`, 그 밖의 공급자(`local`, `chat`)는 docent `/docent/search`·`/docent/page` 를
  함수 도구 `web_search`·`read_page` 로 쓴다. 장소당·턴당 호출 수 상한이 있다.
- **Claude(`AGENT_PROVIDER=claude`)도 내장 검색**: Anthropic 웹 검색 서버 도구 (Sonnet 5.5 `web_search_20260209`, Haiku 5.5 `web_search_20250305`,
  위치 포항, `max_uses` = 장소·턴 상한, docs/agent/README.md §7.1). 답변 텍스트의 인용(`web_search_result_location`)은 OpenAI 처럼 출처가 되고,
  노트처럼 JSON 으로 답해 인용이 붙지 않을 때를 위해 검색 결과 URL 도 본 자료로 등록한다 (노트 `sources` 에 그 URL 을 넣으면 출처, 지어낸 URL 은 버림).
- **실제 확인 — Claude Haiku 5.5** (2026-10-09, worktree 스택, `AGENT_CLAUDE_MODEL=claude-haiku-5-5`, effort `medium`, 장소당 웹 검색 상한 3, 장소 1~2곳씩 3번):

  | 장소 | 웹 검색 | 출처 (등급) | 시간 | 비용 |
  |---|---|---|---|---|
  | 구룡포 일본인 가옥거리 (×2) | 0, 0 | 관광공사 소개·오디오 가이드 2 (official 2) | 27초, 21초 | 약 $0.0040, $0.0033 |
  | 구룡포과메기문화관 (×2) | **1**, 0 | 관광공사 소개·오디오 가이드 2 (official 2) | 21초, 14초 | 약 $0.0135 (검색 $0.01 포함), $0.0019 |
  | 구룡포 근대문화역사관 | 0 | 관광공사 소개 1 (official 1) | 17초 | 약 $0.0020 |

  - 5곳 중 웹 검색은 1번뿐이었다: Haiku 는 관광공사 근거 조각(소개문·오디오 가이드)이 있으면 검색하지 않았다. 출처는 모두 official.
  - 그 한 번의 검색은 위 'URL 도 본 자료로 등록' 전이라 노트 출처에 들지 않았다 (JSON 답이라 인용 없음) → 그 뒤 고치고 가짜 응답 테스트로 확인했다.
    고친 뒤의 두 번은 검색을 하지 않아 실제 검색 출처는 아직 확인하지 못했다.
  - 한국어 지역 정보가 충분한지는 검색이 적어 판단하지 못했다. 검색을 더 하게 하려면 PREP 프롬프트에 무엇을 검색할지(최근 운영 정보 등)를 적거나 effort 를 올린다.

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
- **화면 형식 (2026-10-11)**: 앱이 가벼운 마크다운(문단·`**굵게**`)을 그린다. 음성 읽기는 이번 범위가 아니라 화면 기준으로 쓴다.
  - `long`: 주제별(내력 → 볼거리 → 사용자 맞춤 등) **3~4문단**, 문단 사이 빈 줄(`\n\n`), 문단 첫 문장에 요점. 핵심 고유명사·연도만 굵게 (한 문단에 한두 개).
  - `short`: 2~3문장 **한 문단** (줄바꿈 없이). 굵게는 한두 개.
  - `for_you`·`highlights`·`companion_tips`·`cautions` 는 목록 그대로, 항목마다 한 문장 (굵게·줄바꿈 없이). 제목(#)·표·URL·이모지는 어디에도 쓰지 않는다.
- 출처에 없는 사실은 쓰지 않는다. 확인 못 한 것은 빼거나 "전해지는 이야기로는"으로 구분한다.
- 원문을 길게 옮기지 않는다. 요약해서 새로 쓴다 (저작권). 오디오 가이드·관광공사 자료의 공공누리 조건은 확인해서 출처 표시를 따른다.

### 3.2 언제 만드나
- 앱의 **[가이드 준비]** 버튼 (수동, `ready` 가 아닌 곳 모두 — 실패한 곳 다시 시도 포함).
- **자동 준비 (구현 `feat/guide-auto`, 2026-10-11)** — 버튼을 누르지 않아도 도착 전에 노트가 준비되게:

  | 언제 | 누가 | 대상 |
  |---|---|---|
  | 제안 승인(`…/proposals/{id}/accept`), 추천 후보 [추가] 성공 뒤 | agent (응답 뒤 백그라운드, 승인 응답 시간 그대로) | 일정의 장소 전체 |
  | 여행 시작(`POST /trip/plans/{id}/start`) 성공 뒤 | trip → `POST /agent/plans/{id}/guide/prepare {"auto": true}` (응답 뒤, 그 사용자의 짧은 토큰) | 일정의 장소 전체 |
  | `arrived` 이벤트 (`docs/trip/README.md` §3.2) | trip → 같은 호출 + `place_ids: [그 장소]` | 도착한 장소 |
  | 여행 중 틱 (`docs/trip/README.md` §4.3), **앱 활동 중일 때만** (2026-10-11) | trip → `GET /docent/plans/{id}/notes` 로 보고 같은 호출 + `place_ids: [stale 인 곳]` | 오늘 남은 항목(planned·visiting) 중 노트가 `stale` 인 장소 — 관심사가 바뀐 뒤 도착 직후 옛 해설이 나오지 않게 미리 |

  - 자동 준비는 노트가 **`ready`·`pending`·`preparing`·`failed` 인 장소를 건너뛴다** → 노트가 없거나 `stale` 인 곳만. 실패(`failed`)는 자동으로 다시 시도하지 않는다 (앱의 [다시 시도]로만).
    `pending` 이 바로 표시되므로 같은 장소를 짧은 시간에 반복 요청해도 겹치지 않는다. 앱 도착 카드는 `pending`·`preparing` 이면 "가이드를 준비하고 있어요".
  - **계획당 작업 하나는 그대로**: 작업이 돌고 있으면 새 작업을 만들지 않고, 그 작업에 없는 대상을 `pending` 으로 표시해 두었다가 **작업이 끝나면 그 장소들로 한 번 더** 돌린다
    (`GuideJobView.queued`. 수동 버튼도 같다 — 예전에는 돌고 있는 동안 들어온 장소가 빠졌다).
  - 자동 준비도 `AGENT_LIMIT_GUIDE_*` 에 센다 (새로 준비할 곳이 있을 때만 한 번). 한도를 넘으면(429) 조용히 건너뛰고 로그만 — 승인·추가·여행 시작은 그대로 성공한다.
    agent 에 접근할 수 없어도 trip 은 로그만 남긴다.
  - `AGENT_GUIDE_AUTO=false` 면 자동 준비를 하지 않는다 (버튼으로만. 테스트 기본값).
- **다시 만들기**: 메모리의 `interest`·`style`·`companion` 이 바뀌면(추가·수정·삭제) docent 가 **자동으로** `ready` 노트를 `stale` 로 표시한다
  (user 범위 항목이면 그 사용자의 모든 계획, plan 범위 항목이면 그 계획). `POST …/stale` 로 직접 표시할 수도 있다. 현장에서는 `stale` 이어도 그대로 쓰고(빠름), 여유가 있을 때 다시 만든다.
  여행 중에는 trip 틱이 오늘 남은 장소의 `stale` 노트를 앱 활동 중일 때 미리 다시 준비한다 (위 표). pending 이 바로 표시되므로 같은 장소를 반복 요청하지 않고,
  요청이 실패(한도 초과 429·접근 불가)하면 로그만 남기고 그 여행일은 5분 동안 다시 묻지 않는다. 앱이 꺼져 있으면 하지 않는다 (비용). 장소가 일정에서 빠지면 노트는 남겨 둔다 (다시 넣을 수 있음).

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

실측 — Claude Haiku 5.5 (2026-10-09, 장소 5곳, 표는 §2.1):
- 장소당 LLM 호출 1번 (미리 넣은 근거 조각으로 바로 노트 JSON), 14~27초, 평균 약 20초. 장소 2곳 작업 39~51초.
- 토큰: 장소당 입력 약 6.5~7.6천 (근거 조각 포함, 같은 작업의 두 번째 장소부터 고정 규칙·도구 약 4.4천이 캐시) — 검색한 장소는 2.2만(검색 결과 포함),
  출력 3~6천 (thinking 요약 + 노트). 비용은 출력이 대부분이고 캐시 쓰기가 다음이다.
- **비용: 장소당 약 $0.002~0.004, 웹 검색 한 번이면 +$0.01** (5곳 합계 약 $0.025). 가격: 입력 $0.10 / 캐시 읽기 $0.01 / 5분 캐시 쓰기 $0.125 / 출력 $0.50 per 1M,
  웹 검색 $10 / 1,000회 (claude-api 스킬 문서). 장소 6곳이면 약 $0.02 (+검색 수 × $0.01). 같은 장소의 gpt-5.4-mini 실측은 아직 없다 (§8.4).
- 노트 품질 (표본 대조): 연도·인물(1883년 조일통상장정, 하시모토 젠기치, 2017년 개관 등)이 관광공사 근거 조각과 맞았고 지어낸 사실은 보이지 않았다.
  short 108~167자(30초 안), long 560~580자. 메모리 반영: 역사·사진 → for_you·highlights 에 역사 배경·사진 포인트,
  어머니 동행·오래 걷기 부담 → companion_tips 에 계단 속도·짧은 동선·쉴 곳, 자료에 없는 편의시설은 "자료에 없으니 확인"으로 구분했다.

구현은 §8.3 과 `docs/agent/README.md` §12 (요청·응답 형식, 장소별 진행·남은 시간).

---

## 4. 여행 중 가이드

| 상황 | 처리 | LLM |
|---|---|---|
| 도착 (`arrived` 이벤트, `docs/trip/README.md` §3) | 해설 카드 "이야기 들어볼까요? [짧게] [자세히]" | X |
| [짧게]/[자세히] 누름 | 가이드 노트의 `short`/`long` 을 **바로** 보여준다 (준비돼 있으면 LLM 없이). 메모리 `style` 이 "짧게"면 [짧게]를 기본으로 강조 | 노트가 없을 때만 즉석 생성 |
| 추가 질문 ("저 건물은 언제 지어졌어요?") | Agent 동행 모드 턴. 도구 `get_guide_note` → `search_passages` → (필요하면) `web_search` 순서 | O |
| 가이드 노트 화면에서 그 장소에 대해 질문 | 같은 대화 세션에 `place_id` 를 붙여 보낸다 (`docs/agent/README.md` §3). 여행 전에도 그 장소의 노트·근거부터, 메시지에 `place_id` 가 남아 앱이 장소별로 보여준다 | O |
| 반응 ("더 짧게 해줘", "이런 얘기 좋다") | `remember` 로 메모리 갱신 → 다음 장소부터 반영 | O |
| 이미 들은 이야기 | 메모리 `known` 에 기록 → 다음 노트·답변에서 반복하지 않음 | O |

Agent 동행 모드 도구 추가: `get_guide_note(place_id)`, `search_passages`, `web_search`, `read_page`, `remember`/`forget`/`view_memory`.

음성(TTS)은 이번 범위 밖이다. 붙이게 되면 노트의 `**`·빈 줄을 걷어 내고 읽는다.

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

---

## 8. agent 쪽 구현 (2026-10-09)

코드: `backend/app/agent/` (`tools.py` 도구, `harness.py` 대화 턴, `guide.py` 준비 작업, `llm.py` 내장 검색), 테스트: `backend/tests/agent/test_docent_tools.py`.
API·단계·설정 명세는 `docs/agent/README.md` §3.2·§5·§7·§11.3.1·§12.

### 8.1 메모리
- 계획·동행 모드 모두 매 턴 시스템 프롬프트에 `GET /docent/memory/summary?plan_id=` 요약을 넣는다 (메모리가 없으면 "(아직 없음)").
- `remember` 의 근거(`source`)는 `chat:{session_id}` (앱 31 은 `user_edit` 가 아니면 "대화"로 보여준다). 저장 뒤 같은 분류의 기존 항목을 LLM 에 돌려줘
  "짧게" → "자세히" 같은 충돌을 `forget`·`replace_id` 로 정리하게 한다. 길이 선호는 문구에 "짧게"·"자세히"를 넣게 해서 `style_length`(§5.1)가 잡히게 한다.
- 단계: `remember` → `memory` "기억하기" (detail = 저장한 문장, 항목마다 따로 — 앱 18 "기억했어요" 칩), `forget` → `forget`, `view_memory` → `recall`.

### 8.2 자료 검색
- `search_passages` → places `GET /places/passages?q=&place_id=&limit=` (최대 10). 단계 `research` "자료 찾기".
- 웹 검색 상한: 동행 모드 대화 한 턴 `AGENT_WEB_SEARCH_PER_TURN`(기본 2), 가이드 준비 장소 하나 `AGENT_WEB_SEARCH_PER_PLACE`(기본 3).
  내장 검색은 `max_tool_calls` = 남은 횟수로 묶고 검색 호출(`web_search_call`)마다 센다. 함수 도구는 `web_search`·`read_page` 를 합쳐 센다.
  내장 검색의 `search_context_size` 는 `AGENT_WEB_SEARCH_CONTEXT`(기본 `low`).
- 내장 검색 답변의 `url_citation` 인용은 모아서 가이드 노트 출처가 된다. 웹 검색은 계획 모드·선제 턴에는 쓰지 않는다.

### 8.3 가이드 준비 작업
- `POST /agent/plans/{plan_id}/guide/prepare` → 202, `GET …/guide/jobs/{job_id}` 로 장소별 상태·진행 수·남은 시간 (app gaps b9).
- 장소마다: 노트 `preparing` → 장소 상세 + 근거 조각을 미리 넣은 프롬프트 → (LLM 이 필요하면 근거 조각·웹 검색) → 노트 JSON → `ready` 저장.
- 출처는 실제로 본 자료(근거 조각·검색 결과·읽은 페이지·내장 검색 인용)에서만 고른다. 없으면 `failed` ("출처가 되는 자료를 찾지 못해 … 지어내지 않음").
  근거 조각 출처 주소는 `https://api.visitkorea.or.kr/` (한국관광공사 TourAPI, docent 등급 official). 등급은 docent 가 URL 로 다시 정한다 (§5.2).
- LLM·서비스 오류는 그 장소만 `failed`, 다음 장소로 넘어간다. 계획당 작업 하나. 작업 기록은 agent 프로세스 메모리 (재시작하면 노트 status 만 남는다).

### 8.4 실제 OpenAI 로 확인하는 절차 (아직 하지 않음 — API 크레딧이 생기면)
가짜 LLM 테스트만 끝난 상태다. 아래 명령은 **실제 OpenAI 를 부르고 비용이 든다** (예상: 장소 3곳 × 호출 2~4회, 내장 검색 3~9회).

1. 준비: `backend/.env` 에 `AGENT_PROVIDER=openai`, `AGENT_OPENAI_API_KEY=…` (`AGENT_OPENAI_API=responses`, `gpt-5.4-mini`. 운영 기본은 `claude`).
   전체 스택(`docker compose up -d --build`)과 장소 데이터·임베딩(places-init)이 있어야 한다. 시간 측정을 위해 다른 작업은 돌리지 않는다.
2. 계획과 메모리 (사용자 `seoyun`, 개발 모드 `X-User-Id`):
   ```bash
   export API=http://127.0.0.1:8013 U="X-User-Id: seoyun"
   # 장소 id 찾기: 일본인 가옥거리, 과메기문화관, 구룡포항
   curl -s "$API/places?q=일본인가옥거리&limit=1" -H "$U"; curl -s "$API/places?q=과메기문화관&limit=1" -H "$U"; curl -s "$API/places?q=구룡포항&limit=1" -H "$U"
   curl -s -X POST $API/planner/plans -H "$U" -H 'Content-Type: application/json' \
        -d '{"title":"가이드 확인","start_date":"2026-10-20","day_start":"10:00","day_end":"18:00"}'
   # 세 장소를 items 로 추가 (POST /planner/plans/{id}/items {"place_id"})
   # 메모리 3개 (scope user 2개, 동행자는 이번 여행 plan)
   curl -s -X POST $API/docent/memory -H "$U" -H 'Content-Type: application/json' -d '{"scope":"user","category":"interest","text":"역사·사진 좋아함","source":"user_edit"}'
   curl -s -X POST $API/docent/memory -H "$U" -H 'Content-Type: application/json' -d '{"scope":"plan","plan_id":<id>,"category":"companion","text":"어머니 동행, 오래 걷기 부담","source":"user_edit"}'
   curl -s -X POST $API/docent/memory -H "$U" -H 'Content-Type: application/json' -d '{"scope":"user","category":"style","text":"해설은 핵심만 짧게","source":"user_edit"}'
   ```
3. 준비 작업: `POST $API/agent/plans/<id>/guide/prepare` → `job_id`, `GET $API/agent/plans/<id>/guide/jobs/<job_id>` 를 3초마다 `status: done` 까지.
4. 기록할 것 (이 절 아래 표로):
   - 장소별 `status`·`seconds`·`web_searches`·`usage`(input·cached·output·reasoning·calls), 작업 전체 `usage`·걸린 시간. 비용 = 입력 $0.75 / 캐시 $0.075 / 출력 $4.50 per 1M (agent §7) + 내장 검색 호출 요금.
   - 노트(`GET $API/docent/plans/<id>/notes/<place_id>`) 품질: 사실이 출처와 맞는지(몇 개 표본 대조), 지어낸 내용 없는지, short 가 30초 안에 읽히는지, long 이 2~3분 흐름인지, qa 가 현장 질문다운지.
   - 출처 등급 분포: 장소별 `sources[].tier` 개수 (official·press·community·unknown), 근거 조각(관광공사) 대 웹 비율, 한국어 지역 정보가 충분한지.
   - 메모리 반영: "역사·사진 좋아함" → `for_you`·`highlights` 에 역사·사진 포인트, "어머니 동행, 오래 걷기 부담" → `companion_tips` 에 걷기·계단·쉼터,
     "해설은 핵심만 짧게" → `short` 가 짧고 핵심 위주인지. 메모리를 빼고 같은 장소를 한 번 더(`force`) 만들어 비교하면 차이가 분명하다.
   - 실패한 장소가 있으면 `error` 와 로그(`docker compose logs agent | grep guide`).
5. 동행 모드 추가 질문도 한 번: trip 을 시작(또는 CLI `trip goto`)해 가옥거리에 머무는 상태에서 `"저 계단은 언제 만들어졌어요?"` →
   단계가 `가이드 노트 확인` → `자료 찾기` 순서인지, 웹 검색이 턴당 2회 안인지, `"더 짧게 해줘"` → `memory` 단계와 `style_length` 확인.

| 장소 | status | 시간 | 웹 검색 | 토큰(in/cached/out) | 출처 tier 분포 | 메모리 반영 | 메모 |
|---|---|---|---|---|---|---|---|
| 일본인 가옥거리 | | | | | | | |
| 과메기문화관 | | | | | | | |
| 구룡포항 | | | | | | | |
