#!/bin/bash

set -euo pipefail

# 스크립트 위치 기준으로 실행 (호출 위치·심볼릭 링크 무관)
cd "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

# 시크릿은 .env(gitignore)로 런타임 주입 — 없으면 compose 해석·기동 모두 불가
if [ ! -f .env ]; then
  echo ".env 파일이 없습니다. .env.example을 참고해 DART_API_KEY, PUBLIC_BASE_URL을 설정한 뒤 다시 실행하세요" >&2
  exit 1
fi

# compose 설정에서 컨테이너명·공개 포트·프로젝트명을 읽는다 (환경별 compose 파일 차이 흡수)
CONTAINER_NAME="$(docker compose config 2>/dev/null | awk '$1=="container_name:"{print $2; exit}')"
HEALTH_PORT="$(docker compose config 2>/dev/null | awk '$1=="published:"{gsub(/"/,"",$2); print $2; exit}')"
CURRENT_PROJECT="$(docker compose config 2>/dev/null | awk '/^name: /{print $2; exit}')"

# 1) 서비스는 그대로 둔 채 새 이미지를 먼저 빌드한다 (다운타임 = 컨테이너 재기동 시간만).
#    추가 빌드 옵션은 그대로 전달된다. 예: ./build.sh --no-cache
docker compose build --pull "$@"

# 2) 과거 다른 compose 프로젝트명으로 만들어진 컨테이너가 이름을 선점 중이면 제거한다.
#    (compose가 자기 소속으로 인식하지 못해 container_name 충돌을 일으킨다)
if [ -n "$CONTAINER_NAME" ] && docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  OWNER_PROJECT="$(docker container inspect "$CONTAINER_NAME" --format '{{index .Config.Labels "com.docker.compose.project"}}')"
  if [ "$OWNER_PROJECT" != "$CURRENT_PROJECT" ]; then
    echo "다른 compose 프로젝트('${OWNER_PROJECT:-없음}') 소속 기존 컨테이너 제거: $CONTAINER_NAME"
    docker container rm --force "$CONTAINER_NAME"
  fi
fi

# 3) 새 이미지로 교체 기동 (캐시 볼륨 why-moved-cache는 유지됨)
docker compose up -d --force-recreate --remove-orphans
docker compose ps

# 4) 기동 확인 (최대 60초 대기 — 프리웜은 데몬 스레드라 health 응답을 막지 않음)
if [ -n "$HEALTH_PORT" ]; then
  for _ in $(seq 1 30); do
    if curl -fsS "http://localhost:${HEALTH_PORT}/health" >/dev/null 2>&1; then
      echo "health OK: http://localhost:${HEALTH_PORT}/health"
      exit 0
    fi
    sleep 2
  done
  echo "health check 실패 — 'docker compose logs'로 기동 로그를 확인하세요" >&2
  exit 1
fi
