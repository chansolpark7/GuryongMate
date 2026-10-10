# 운영 게이트웨이 이미지: 설정 파일을 이미지에 넣는다 (서버에 저장소 파일을 마운트하지 않음). docker-compose.prod.yml
FROM caddy:2.8-alpine
COPY routes.caddy /etc/caddy/routes.caddy
COPY Caddyfile.prod /etc/caddy/Caddyfile
COPY console /srv/console
