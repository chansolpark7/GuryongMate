# 17 Guide Note — 가이드 노트 (P0)

Figma: 화면 `73:2587` · Spec `73:2726` · 흐름 F4

## 목적
여행 전에 준비한 사용자 맞춤 해설을 **기다림 없이** 보여준다. "당신에게" 포인트로 맞춤이라는 것을 드러내고, 출처로 신뢰를 준다.

## 진입
16 [짧게]/[자세히], 14 [✨ 이야기 듣기], 24 도착 알림, 32 준비된 행 탭(미리보기). push 화면.
2026-10-11 추가: 14 지도 핀 작은 창 [✨ 설명 듣기], Map 장소 시트 펼침의 [✨ 설명 듣기](동행 중 오늘 장소). 노트가 없으면 지금처럼 "준비된 해설이 없어요" + [지금 설명 듣기].

## 레이아웃 (흰 배경, 스크롤)
| 구역 | 내용 |
|---|---|
| App Bar | Back · 제목 "현장 가이드" · 부제 장소명 · 오른쪽 🔊 (TTS, 이번 범위 밖 → 비활성 + 탭 시 "음성은 준비 중이에요") |
| 태그 줄 | 장소 종류 칩(`place.content_type`) · Purple 칩 "✨ 내 관심사 반영 · MM/DD 준비"(`prepared_at`) |
| Segment | [짧게 · 30초 \| 자세히 · 2~3분] 40 r12 #F3F4F6, 선택 흰 바탕 |
| 본문 | `short` 또는 `long` 16/26 |
| 당신에게 | #F2EFFF 박스 r14, ✨ Purple 제목, `for_you[]` 점 목록 |
| 사진 포인트 | camera 아이콘, `highlights[]` |
| 동행자 팁 | users 아이콘, 제목 "동행자 팁 · {동행자}" (메모리 `companion` 요약에서 사람 이름만, 없으면 "동행자 팁"), `companion_tips[]` |
| 알아두세요 | Amber alert, `cautions[]` |
| 이런 것도 물어보세요 | `qa[].q` 칩 (Purple 테두리 h30) |
| 출처 | `sources[]` 칩: tier `official` "공식", `press` "언론", `community` "방문 후기"(흰 바탕 회색), `unknown` 은 tier 표시 없이 제목만 |
| 하단 고정 | Input "이 장소에 대해 물어보기…" + mic + 보내기 |

빈 배열인 섹션은 통째로 숨긴다.

## 데이터
`GET /docent/plans/{plan_id}/notes/{place_id}` → GuideNote (`docs/docent/README.md` §3.1): `status, short, long, for_you, highlights, companion_tips, cautions, qa, sources, memory_version, prepared_at`.

## 상태
| status | 표시 |
|---|---|
| ready | 그대로 |
| stale | 태그 줄에 Amber 칩 "관심사가 바뀌어 다시 준비할 수 있어요" (현장에서는 그대로 읽는다) |
| pending · preparing | 본문 스켈레톤 + "해설을 준비하고 있어요" + [지금 설명 듣기] |
| failed · 404 | "준비된 해설이 없어요" + [✨ 지금 설명 듣기] (AI 턴) |
| 오류(503·오프라인) | 오류 뷰 + 다시 시도 |

## 동작
| 동작 | API | 다음 |
|---|---|---|
| Segment | — | 본문 교체, 선택을 로컬 기억(다음 장소 기본값). 메모리 저장은 하지 않는다(대화로 말했을 때만 저장 — docent §1.2) |
| qa 칩 / 입력 | AI 시트를 장소 컨텍스트로 열고 `POST /agent/sessions/{sid}/chat` | 18. qa 칩은 노트에 답(`qa[].a`)이 있어도 대화로 보낸다(대화 맥락·메모리 반응을 위해). 오프라인이면 `qa[].a` 를 칩 아래에 펼쳐 보여준다 |
| 출처 칩 | — | 외부 브라우저(url) |
| 🔊 | — | "음성은 준비 중이에요" |

## 카피
노트는 출처로 확인된 사실만 담는다(docent §3.1). 앱은 문장을 바꾸지 않는다.
