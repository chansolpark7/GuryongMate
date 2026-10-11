# 운영 게이트웨이 이미지: 설정 파일·관제 대시보드·웹 앱을 이미지에 넣는다 (서버에 저장소 파일을 마운트하지 않음). docker-compose.prod.yml
# 웹 앱은 저장소에 커밋하지 않는 빌드 결과(app/build/web)라 추가 빌드 컨텍스트 webapp 으로 받는다 — deploy/build.sh 가
#   deploy/build_web.sh 로 빌드한 뒤  docker build --build-context webapp=app/build/web …  로 부른다.
FROM caddy:2.8-alpine
COPY routes.caddy /etc/caddy/routes.caddy
COPY Caddyfile.prod /etc/caddy/Caddyfile
COPY console /srv/console
COPY --from=webapp . /srv/app
