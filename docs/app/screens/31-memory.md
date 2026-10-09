# 31 Memory — 나에 대해 (보기 · 지우기) (P1)

Figma: 화면 `73:5603` · Spec `73:5717`

- **목적**: 구룡메이트가 기억하는 것을 투명하게 보여주고 사용자가 지울 수 있게 한다 (docent §1.2).
- **진입**: 33 설정 → 나에 대해, 18 메모리 칩 "나에 대해 ›", 30 기억한 것.
- **레이아웃**: App Bar · 안내 박스(#F2EFFF) "구룡메이트가 대화에서 알게 된 것이에요. 말씀하셨거나 확인하신 것만 저장해요. 틀린 건 지워 주세요." · Segment [전체 | 이번 여행 | 모든 여행] · 분류 그룹 · 항목 행 358×62 r14: 내용 14 + "{범위} · {날짜} {출처}" 12 + 삭제 아이콘 · 24시간 안 항목 "새로" 칩 · 맨 아래 "모두 지우기"(Danger 텍스트).
- **데이터**: `GET /docent/memory?plan_id=` → 항목(`id, scope, category, text, source, created_at`) + `memory_version`.

| category | 그룹 제목 |
|---|---|
| interest | 관심사 |
| style | 해설 스타일 |
| companion | 동행자·제약 |
| food | 음식 |
| avoid | 피하고 싶은 것 |
| known | 이미 들은 이야기 |
| feedback | 반응 |

  범위: `user` "모든 여행", `plan` "이번 여행". 출처: 메시지 id → "대화", `user_edit` → "직접 수정".
- **동작**: 삭제 → `DELETE /docent/memory/{id}` (확인 없이) + 스낵바 "지웠어요 · 되돌리기"(4초, 되돌리기 = 같은 내용 `POST /docent/memory {scope, plan_id, category, text, source:"user_edit"}`) · 행 탭 → 수정 시트 `PATCH /docent/memory/{id}` · 모두 지우기 → 다이얼로그 → 항목마다 DELETE ([gaps](../gaps.md) b11).
- **빈 상태**: "아직 기억한 게 없어요. 대화하면서 알려주시는 취향을 여기에 모아요."
