"""환경설정. 모든 외부 설정은 환경변수로 주입받는다."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    dart_api_key: str
    cache_db_path: str
    http_timeout_seconds: float
    server_host: str
    server_port: int
    public_base_url: str

    @staticmethod
    def from_env() -> "Settings":
        port = int(os.environ.get("SERVER_PORT") or os.environ.get("PORT") or "8000")
        return Settings(
            dart_api_key=os.environ.get("DART_API_KEY", ""),
            cache_db_path=os.environ.get("CACHE_DB_PATH", "data/cache.db"),
            http_timeout_seconds=float(os.environ.get("HTTP_TIMEOUT_SECONDS", "5")),
            server_host=os.environ.get("SERVER_HOST", "0.0.0.0"),
            # PaaS(PlayMCP in KC 등)가 PORT를 주입하는 관례 지원
            server_port=port,
            # 차트 URL 생성용 공개 베이스 URL (배포 시 환경변수로 지정)
            public_base_url=os.environ.get("PUBLIC_BASE_URL", f"http://localhost:{port}").rstrip("/"),
        )


def get_settings() -> Settings:
    settings = Settings.from_env()
    if not settings.dart_api_key:
        raise RuntimeError(
            "DART_API_KEY not configured. "
            "https://opendart.fss.or.kr 에서 무료 API 키를 발급받아 .env에 설정하세요."
        )
    return settings
