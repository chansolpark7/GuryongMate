#!/usr/bin/env bash
# 웹 앱(Flutter web)을 빌드한다 → app/build/web. 게이트웨이가 / 에서 서빙한다 (infra/gateway/routes.caddy).
#   deploy/build_web.sh                      운영과 같은 빌드 (DEMO=true, API 주소 = 페이지를 연 주소)
#   deploy/build_web.sh --dart-define=…      flutter build web 에 인자를 더 넘긴다
# deploy/build.sh 가 gateway 이미지를 만들기 전에 부른다. 개발 compose 는 이 결과를 그대로 마운트한다 (새로고침하면 반영).
source "$(dirname "$0")/lib.sh"
command -v flutter >/dev/null || die "flutter 가 없습니다. 웹 앱을 빌드하려면 노트북에 Flutter SDK 가 필요합니다 (app/README.md)"
OUT="$ROOT/app/build/web"
log "flutter build web ($(flutter --version 2>/dev/null | head -1))"
# --pwa-strategy=none: 서비스 워커 캐시를 끈다 (업데이트 뒤 옛 앱이 남지 않게. Flutter 가 '곧 없앨 옵션'이라고 경고만 한다)
# --no-web-resources-cdn: CanvasKit 을 외부 CDN(gstatic) 이 아니라 게이트웨이에서 받는다 (발표장 네트워크 대비)
# DEMO=true: 팀·멘토 시험용 — 설정의 데모·개발 그룹이 보인다
(cd "$ROOT/app" && flutter build web --release --pwa-strategy=none --no-web-resources-cdn --no-wasm-dry-run \
  --dart-define=DEMO=true "$@" >&2) || die "flutter build web 실패"
# flutter 가 pub get 때 다시 쓰는 생성 파일(app/windows/flutter/generated_* 등)이 줄바꿈만 바뀌어 '변경'으로 보이면 되돌린다
# (내용이 같은 것만. 안 그러면 다음 deploy/update.sh 가 '커밋하지 않은 변경'으로 멈춘다)
git -C "$ROOT" status --porcelain --untracked-files=no -- app | awk '$1=="M"{print $2}' | while read -r f; do
  if git -C "$ROOT" diff --quiet -- "$f"; then git -C "$ROOT" checkout -q -- "$f"; fi
done
# JS 빌드(canvaskit 렌더러)가 쓰지 않는 파일을 지운다: 브라우저는 canvaskit/canvaskit.wasm 또는 canvaskit/chromium/canvaskit.wasm 만 받는다.
# skwasm·wimp·webparagraph 는 --wasm 빌드용, *.symbols 는 디버그 기호 (43MB → 약 19MB)
rm -rf "$OUT"/canvaskit/skwasm* "$OUT"/canvaskit/wimp* "$OUT"/canvaskit/webparagraph
find "$OUT/canvaskit" -name '*.symbols' -delete
[[ -f "$OUT/index.html" && -f "$OUT/canvaskit/canvaskit.wasm" && -f "$OUT/canvaskit/chromium/canvaskit.wasm" ]] || die "빌드 결과가 이상합니다 ($OUT)"
ok "웹 앱 $(du -sh "$OUT" | cut -f1) → app/build/web"
