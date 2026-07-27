"""market-data-collector 종목명 배치 조회 어댑터.

DART corpCode 마스터에 없는 종목(우선주·리츠·스팩 등)의 표시용 종목명을
collector의 `POST /api/v1/securities/names`(단축명 우선)로 보충한다.

보조 데이터 소스이므로 실패해도 예외를 올리지 않는다 — 호출부는 기존처럼
종목코드로 폴백한다.
"""

import httpx

from why_moved.cache.store import TTLCache

NAME_TTL = 7 * 86400  # 종목명은 사실상 불변 — corpCode와 동일하게 주 1회 갱신
_BATCH_MAX = 400      # collector BatchNameRequest max_length


class CollectorNameClient:
    def __init__(self, base_url: str, cache: TTLCache, timeout: float = 5.0):
        self._base_url = base_url.rstrip("/")
        self._cache = cache
        self._timeout = timeout

    async def lookup(self, items: list[tuple[str, str]]) -> dict[str, str]:
        """(market, code) 목록 → {code: 종목명}. 못 찾은 코드는 생략, 실패 시 부분 결과만."""
        found: dict[str, str] = {}
        missing: list[tuple[str, str]] = []
        for market, code in items:
            cached = self._cache.get(f"collector_name:{code}")
            if cached:
                found[code] = cached
            else:
                missing.append((market, code))

        if not missing:
            return found

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/api/v1/securities/names",
                    json={
                        "securities": [
                            {"exchange_code": market, "symbol": code}
                            for market, code in missing[:_BATCH_MAX]
                        ]
                    },
                )
                resp.raise_for_status()
                results = resp.json()["results"]
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            return found  # 보조 소스 — 실패해도 스크리너는 코드 폴백으로 동작

        for row in results:
            name = row.get("name")
            code = row.get("symbol")
            if name and code:
                found[code] = name
                self._cache.set(f"collector_name:{code}", name, NAME_TTL)
        return found
