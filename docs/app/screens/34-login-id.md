# 34 Login ID — 아이디 로그인 (P0)

Figma: 화면 `73:6084` · Spec `73:6144` · 잠김 34-S1 `73:8131` · 흐름 F11

## 목적
account 서비스 로그인. 기존 앱의 "이메일 = X-User-Id"를 대체한다 (앱은 로그인 필수, `AUTH_REQUIRED=true`).

## 진입
01 [아이디로 계속하기]. 35 에서 뒤로.

## 레이아웃
흰 배경 · Back · "아이디로 로그인" 24/32 · "구룡메이트 계정으로 계속해요" 14 · 필드 2개(라벨 14 Bold + 입력 350×52 r12 #F3F4F6) · 비밀번호 보기 아이콘 · [로그인] Primary 52 · "처음이신가요? **가입하기**".
오류: 입력 테두리 1.5px Danger + 아래 alert 아이콘 + 12 Danger 문구.

## 동작
| 동작 | API | 결과 |
|---|---|---|
| 로그인 | `POST /auth/login {"login_id": 소문자, "password"}` | 200 `TokenResponse{access_token, refresh_token, expires_in, user}` → `flutter_secure_storage` 저장 → 02/29 (스택 교체) |
| 가입하기 | — | 35 |

| 오류 | 표시 |
|---|---|
| 401 `invalid_credentials` | 비밀번호 필드 아래 "아이디 또는 비밀번호가 맞지 않아요" (어느 쪽인지 말하지 않음), 비밀번호 비우기 |
| 429 `too_many_attempts {retry_after}` | 34-S1: Red 박스 "로그인을 여러 번 실패해 잠겼어요 · HH:MM에 다시 시도할 수 있어요", 버튼 비활성 "로그인 (mm:ss 남음)" 카운트다운 |
| 422 `invalid_request` | `errors[].loc` 의 필드 아래 |
| 503 / 오프라인 | 스낵바 "서버에 연결하지 못했어요" |

## 토큰 (account §2)
- 모든 요청 `Authorization: Bearer <access>`.
- 401 `unauthorized` → `POST /auth/refresh {refresh_token}` 한 번 → 새 쌍 저장 후 원래 요청 재시도 → 또 401(`invalid_refresh_token`/`refresh_token_reused`)이면 토큰 삭제 → 01.
- 동시에 여러 요청이 401 이면 refresh 는 한 번만 (나머지는 대기 후 재시도) — refresh 는 1회용(rotation)이라 두 번 보내면 `refresh_token_reused` 로 모든 로그인이 끊긴다.
- SSE(agent chat)도 같은 헤더. 스트림 중 401 은 없다고 보고, 시작 전 401 이면 위 규칙.
