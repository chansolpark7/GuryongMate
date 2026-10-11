# account — 로그인·사용자 API 명세

Google 계정 로그인과 ID/비밀번호 로그인을 제공하고, 다른 서비스가 확인할 **토큰(JWT)**을 발급한다.
다른 서비스(planner, agent …)는 account를 호출하지 않고 공용 코드(`app.core.auth`)로 토큰 서명만 확인한다.

결정 사항
- **공개 서버**(docs/deploy/README.md §3)는 가입(`POST /auth/signup`)을 게이트웨이에서 관리 토큰으로만 연다. 팀원·멘토 계정은 `deploy/create_user.sh` 로 만든다. Google 로그인(계정 자동 생성)은 서버에서 켜지 않는다.
- 로그인 방식마다 **별도 계정**이다. 같은 이메일로 Google 로그인과 ID/비밀번호 가입을 하면 서로 다른 계정이 된다.
- 토큰은 **access(1시간) + refresh(30일)**를 쓴다. refresh는 한 번 쓰면 새로 바뀐다(rotation).
- 비밀번호 찾기(재설정)는 이번 범위에서 빼고, 로그인한 상태에서 비밀번호 변경만 지원한다.
- **앱은 로그인 필수**다. 개발·테스트 중에만 `AUTH_REQUIRED=false`로 토큰 없이 쓸 수 있다(§5).

---

## 1. 공통 규칙

- 경로 앞부분은 `/auth`(로그인 흐름)와 `/users`(내 정보)다. 앱은 게이트웨이(`:8000`)로 접속한다.
- 본문은 JSON이고, 정의되지 않은 필드가 있으면 422다.
- 오류 형식은 다른 서비스와 같다: `{"detail": {"code": "...", "message": "...", ...}}`

| HTTP | code | 언제 |
|---|---|---|
| 422 | `invalid_request` | 스키마 위반 (필수값 누락, 형식·길이, 모르는 필드). 추가 필드 `errors: [{loc, msg}]` |
| 422 | `weak_password` | 비밀번호 규칙 위반 (§3.1) |
| 409 | `login_id_taken` | 이미 쓰는 아이디 |
| 401 | `unauthorized` | `/users/*` 호출에 access 토큰이 없거나 잘못됨·만료 |
| 401 | `invalid_credentials` | 아이디·비밀번호가 틀림 (아이디가 없는 경우와 구분하지 않는다) |
| 429 | `too_many_attempts` | 로그인 실패가 5번 이어져 잠김. 추가 필드 `retry_after`(초) |
| 401 | `invalid_google_token` | Google ID 토큰 검증 실패 (서명·발급자·대상·만료·이메일 미인증) |
| 503 | `google_unavailable` | Google 공개키를 가져올 수 없음 |
| 401 | `invalid_refresh_token` | refresh 토큰이 없거나 만료·폐기됨 |
| 401 | `refresh_token_reused` | 이미 쓴 refresh 토큰을 다시 씀. 탈취로 보고 그 계정의 모든 로그인을 끊는다 |
| 409 | `no_password` | 비밀번호가 없는 계정(Google 계정)에 비밀번호 변경 요청 |

---

## 2. 토큰

| | access token | refresh token |
|---|---|---|
| 형식 | JWT (HS256, 서버 공용 `JWT_SECRET`) | 무작위 문자열 (서버에는 해시만 저장) |
| 유효 기간 | 1시간 (`ACCOUNT_ACCESS_TTL_MIN`) | 30일 (`ACCOUNT_REFRESH_TTL_DAYS`) |
| 쓰는 곳 | 모든 API 요청 `Authorization: Bearer <access>` | `POST /auth/refresh`, `POST /auth/logout`만 |
| claim | `sub`(user id), `iat`, `exp`, `typ: "access"` | — |

앱의 흐름
1. 로그인하면 두 토큰을 안전한 저장소(Keychain/Keystore, Flutter `flutter_secure_storage`)에 저장한다.
2. API 응답이 401 `unauthorized`면 `POST /auth/refresh`로 새 토큰 쌍을 받아 원래 요청을 한 번 다시 보낸다.
3. refresh도 401이면 로그인 화면으로 보낸다.
4. 로그아웃하면 `POST /auth/logout`을 부르고 저장한 토큰을 지운다.

refresh 토큰은 **한 번만** 쓸 수 있다. 쓸 때마다 새 refresh 토큰이 나오고 이전 것은 폐기된다. 폐기된 토큰이 다시 오면(탈취 의심) 그 계정의 모든 refresh 토큰을 폐기한다.

### `TokenResponse`

```jsonc
{"access_token": "eyJ…", "token_type": "bearer", "expires_in": 3600,
 "refresh_token": "t1Z…", "refresh_expires_in": 2592000,
 "user": UserView,
 "created": false}               // /auth/google 에서 이번에 새 계정을 만들었으면 true (가입 직후 화면 분기용)
```

### `UserView`

```jsonc
{"id": "3f2c…",                  // 다른 서비스의 user_id (토큰의 sub)
 "provider": "password",         // password | google
 "login_id": "seoyun",           // password 계정만, google 계정은 null
 "email": "a@gmail.com",         // google 계정만 (Google 이 확인한 이메일), password 계정은 null
 "display_name": "서윤",
 "created_at": "…"}
```

---

## 3. 로그인 흐름 `/auth`

| 메서드 | 경로 | 본문 | 성공 | 오류 |
|---|---|---|---|---|
| POST | `/auth/signup` | `{"login_id", "password", "display_name"?}` | 201 `TokenResponse` | 422 `invalid_request`·`weak_password`, 409 `login_id_taken` |
| POST | `/auth/login` | `{"login_id", "password"}` | 200 `TokenResponse` | 401 `invalid_credentials`, 429 `too_many_attempts` |
| POST | `/auth/google` | `{"id_token"}` | 200 `TokenResponse` | 401 `invalid_google_token`, 503 `google_unavailable` |
| POST | `/auth/refresh` | `{"refresh_token"}` | 200 `TokenResponse` (새 쌍) | 401 `invalid_refresh_token`·`refresh_token_reused` |
| POST | `/auth/logout` | `{"refresh_token"}` | 204 (없는 토큰이어도 204) | 422 |

### 3.1 ID/비밀번호
- `login_id`: 영문 소문자·숫자·`.`·`_`·`-`로 4~32자이고, 영문이나 숫자로 시작한다. 대문자로 보내면 소문자로 바꿔 저장·비교한다.
- `password`: 8~128자이고 영문과 숫자를 하나 이상씩 포함하며, 아이디와 같으면 안 된다. 어기면 422 `weak_password`다.
- `display_name`: 1~30자이고, 생략하면 `login_id`를 쓴다.
- 비밀번호는 argon2id로 해시해서 저장한다.
- 로그인이 **5번 연속 실패**하면 그 계정을 **15분** 잠근다. 잠긴 동안에는 맞는 비밀번호도 429다. 성공하면 실패 횟수가 초기화된다.

### 3.2 Google
앱이 Google Sign-In SDK(`google_sign_in`)로 받은 **ID 토큰**을 보내면 서버가 검증한다. 서버 쪽 리다이렉트 흐름은 쓰지 않는다.
- 검증 항목: Google 공개키 서명(RS256), `iss`가 `accounts.google.com`, `aud`가 우리 OAuth 클라이언트 ID 중 하나(`ACCOUNT_GOOGLE_CLIENT_IDS`), 만료되지 않음, `email_verified=true`
- Google 계정 고유 ID(`sub`)로 계정을 찾고, 없으면 만든다(`created: true`). 이메일과 이름은 로그인할 때마다 Google 값으로 갱신한다(`display_name`은 사용자가 바꾼 적이 없을 때만).
- **준비물 (계정 소유자가 Google Cloud 콘솔에서)**: OAuth 동의 화면과 OAuth 클라이언트 ID(Android(패키지명 + SHA-1), iOS(번들 ID), Web). 앱 설정의 `serverClientId`에는 Web 클라이언트 ID를 쓰고, 서버의 `ACCOUNT_GOOGLE_CLIENT_IDS`에는 실제로 토큰 `aud`로 오는 ID들을 쉼표로 넣는다.

---

## 4. 내 정보 `/users/me`

모두 access 토큰이 필요하다. 개발 모드에서도 이 경로는 토큰 없이 쓸 수 없다.

| 메서드 | 경로 | 본문 | 성공 | 오류 |
|---|---|---|---|---|
| GET | `/users/me` | | 200 `UserView` | 401 `unauthorized` |
| PATCH | `/users/me` | `{"display_name"}` | 200 `UserView` | 401, 422 |
| PUT | `/users/me/password` | `{"current_password", "new_password"}` | 200 `TokenResponse` | 401 `unauthorized`·`invalid_credentials`, 409 `no_password`, 422 `weak_password` |
| DELETE | `/users/me` | | 204 | 401, 503 `upstream_unavailable` |

- **비밀번호 변경**: 그 계정의 모든 refresh 토큰을 폐기한다(다른 기기 로그아웃). 지금 기기용 새 토큰 쌍을 응답으로 준다.
- **탈퇴**: 사용자의 **모든 데이터**를 지운다. account가 같은 토큰으로 `DELETE /planner/me`, `DELETE /agent/me`를 먼저 부른 뒤 계정과 토큰을 지운다. 다른 서비스 삭제가 실패하면 계정을 지우지 않고 503 `upstream_unavailable`을 돌려준다(다시 시도하면 된다).

---

## 5. 다른 서비스의 인증 (`app.core.auth`)

planner·agent는 `CurrentUser` 의존성으로 사용자 ID를 얻는다.

| 상황 | `AUTH_REQUIRED=true` (앱·데모·운영) | `AUTH_REQUIRED=false` (개발·테스트) |
|---|---|---|
| 올바른 access 토큰 | 토큰의 `sub` | 토큰의 `sub` |
| 토큰이 잘못됨·만료 | 401 `unauthorized` | 401 `unauthorized` |
| 토큰 없음 | 401 `unauthorized` | `X-User-Id` 헤더 값 (없으면 `anonymous`) |

- Agent가 planner·places를 호출할 때는 **받은 요청의 인증 정보를 그대로 전달**한다(`Authorization` 또는 개발 모드의 `X-User-Id`).
- 각 서비스는 탈퇴용으로 `DELETE /<서비스>/me`를 제공한다. 그 사용자의 데이터를 모두 지우고 204를 돌려준다.
  - planner: 계획(일정·규칙·제안·추천 포함)을 지운다.
  - agent: 대화 세션과 메시지를 지운다.

## 6. 설정 (`backend/.env`)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `JWT_SECRET` | `dev-only-change-me` | 공용. **운영에서는 반드시 바꾼다** |
| `AUTH_REQUIRED` | `false` | 공용. 앱·데모는 `true` |
| `ACCOUNT_GOOGLE_CLIENT_IDS` | (빈 값) | 허용할 Google OAuth 클라이언트 ID (쉼표 구분). 비면 Google 로그인은 401 |
| `ACCOUNT_ACCESS_TTL_MIN` | `60` | |
| `ACCOUNT_REFRESH_TTL_DAYS` | `30` | |
| `ACCOUNT_MAX_FAILURES` / `ACCOUNT_LOCK_MIN` | `5` / `15` | 로그인 잠금 |

## 7. 구조·테이블

| 파일 | 역할 |
|---|---|
| `backend/app/account/models.py` | `account_user`, `account_refresh_token` |
| `backend/app/account/router.py` | `/auth/*`, `/users/me` |
| `backend/app/account/google.py` | Google ID 토큰 검증 (공개키 캐시) |
| `backend/app/account/security.py` | 비밀번호 해시, 토큰 발급·회전 |

## 8. CLI

```bash
python scripts/planner/cli.py signup seoyun        # 비밀번호는 입력받음
python scripts/planner/cli.py login seoyun
python scripts/planner/cli.py whoami
python scripts/planner/cli.py logout
```
토큰은 `~/.guryongmate/session.json`에 서버 주소별로 저장하고, 401이면 refresh로 한 번 자동 갱신한다. 로그인하지 않았으면 개발 모드처럼 `--user`(X-User-Id)를 쓴다.
