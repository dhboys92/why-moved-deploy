"""애플리케이션 컨텍스트 — 어댑터·캐시를 한 곳에서 조립한다."""

from dataclasses import dataclass
from pathlib import Path

from why_moved.adapters.corp_codes import CorpCodeResolver
from why_moved.adapters.dart import DartClient
from why_moved.adapters.kind import KindClient
from why_moved.adapters.market_data import MarketDataClient
from why_moved.cache.chart_store import ChartStore
from why_moved.cache.store import TTLCache
from why_moved.config import Settings


@dataclass(frozen=True)
class AppContext:
    dart: DartClient
    market: MarketDataClient
    kind: KindClient
    resolver: CorpCodeResolver
    charts: ChartStore
    public_base_url: str

    def chart_url(self, chart_id: str) -> str:
        return f"{self.public_base_url}/charts/{chart_id}.png"


def _app_version() -> str:
    from why_moved import __version__

    return __version__


def build_context(settings: Settings) -> AppContext:
    cache = TTLCache(settings.cache_db_path)
    charts_dir = str(Path(settings.cache_db_path).parent / "charts")
    return AppContext(
        dart=DartClient(settings.dart_api_key, cache, settings.http_timeout_seconds),
        market=MarketDataClient(cache, settings.http_timeout_seconds),
        kind=KindClient(cache, settings.http_timeout_seconds),
        resolver=CorpCodeResolver(settings.dart_api_key, cache),
        charts=ChartStore(charts_dir, salt=_app_version()),
        public_base_url=settings.public_base_url,
    )
