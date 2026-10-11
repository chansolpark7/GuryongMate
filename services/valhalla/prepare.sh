#!/usr/bin/env bash
# 포항 영역 OSM 파일이 없으면 받아서 자른 뒤, 원래 Valhalla 진입점으로 넘긴다.
# 타일은 /custom_files 볼륨에 남으므로 두 번째 실행부터는 바로 뜬다.
#   OSM_URL   원본 (기본: Geofabrik 대한민국, 약 280MB)
#   OSM_BBOX  자를 영역 minlon,minlat,maxlon,maxlat (기본: 포항시 + 여유)
#   OSM_FILE  잘라 둔 파일 이름. 바꾸면 (예: 영역을 넓히면) 새로 받아 다시 빌드한다
set -o errexit -o pipefail -o nounset

CUSTOM_FILES="/custom_files"
OSM_URL="${OSM_URL:-https://download.geofabrik.de/asia/south-korea-latest.osm.pbf}"
OSM_BBOX="${OSM_BBOX:-128.95,35.80,129.65,36.40}"
OSM_FILE="${OSM_FILE:-pohang.osm.pbf}"

mkdir -p "${CUSTOM_FILES}"
target="${CUSTOM_FILES}/${OSM_FILE}"

if [[ ! -f "${target}" ]]; then
  # 다른 영역으로 잘라 둔 파일이 있으면 지운다 (Valhalla 는 폴더의 모든 .pbf 로 빌드한다)
  rm -f "${CUSTOM_FILES}"/*.pbf
  src="/tmp/source.osm.pbf"
  echo "INFO: OSM 원본 받는 중: ${OSM_URL}"
  curl --location --fail --show-error --silent --output "${src}" "${OSM_URL}"
  echo "INFO: 영역 자르는 중: ${OSM_BBOX}"
  # 임시 파일은 .pbf 가 아닌 폴더에 둔다 (Valhalla 가 폴더의 .pbf 를 모두 읽고, osmium 은 확장자로 형식을 판단한다)
  mkdir -p /tmp/extract
  osmium extract --bbox "${OSM_BBOX}" --strategy complete_ways --overwrite \
    --output "/tmp/extract/${OSM_FILE}" "${src}"
  mv "/tmp/extract/${OSM_FILE}" "${target}"
  rm -f "${src}"
  # 데이터가 바뀌었으니 기존 타일을 다시 만든다
  export force_rebuild=True
fi

exec /valhalla/scripts/docker-entrypoint.sh "$@"
