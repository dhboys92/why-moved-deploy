"""시세·수급·밸류에이션 어댑터 — 네이버 금융 공개 API 기반.

KRX 정보데이터시스템이 2025년부터 로그인 필수로 바뀌어(검증: LOGOUT 응답),
무인증 공개 소스인 네이버 금융 API로 구현한다. 모든 데이터는 종가 기준이며
응답에 기준일을 명시한다.

사용 엔드포인트 (모두 무료·무인증):
- api.finance.naver.com/siseJson.naver        : 일별 OHLCV (종목·지수)
- m.stock.naver.com/api/stock/{code}/basic     : 종목 기본정보 (시장 구분)
- m.stock.naver.com/api/stock/{code}/integration : PER/PBR/배당수익률
- m.stock.naver.com/api/stocks/marketValue/{market} : 시총 상위 목록
- finance.naver.com/item/frgn.naver            : 기관·외국인 순매매량
"""

import ast
import asyncio
import re

import httpx

from why_moved.cache.store import TTLCache
from why_moved.common.errors import UpstreamError

PRICE_TTL = 3600
SNAPSHOT_TTL = 86400
_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
_SNAPSHOT_TOP_N = 200          # 시장별 시총 상위 N종목 (스크리너 대상)
_SNAPSHOT_CONCURRENCY = 10


def _num(raw: str | float | None) -> float | None:
    """'25.02배', '0.54%', '309,500' → float."""
    if raw is None:
        return None
    s = re.sub(r"[,배%원 ]", "", str(raw))
    try:
        return float(s)
    except ValueError:
        return None


class MarketDataClient:
    def __init__(self, cache: TTLCache, timeout: float = 5.0):
        self._cache = cache
        self._timeout = timeout

    async def _get_text(self, url: str, params: dict | None = None) -> str:
        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers=_UA) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                return resp.text
        except httpx.HTTPError as exc:
            raise UpstreamError("시세(네이버)", str(exc)) from exc

    async def _get_json(self, url: str) -> dict | list:
        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers=_UA) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise UpstreamError("시세(네이버)", str(exc)) from exc

    async def _daily_rows(self, symbol: str, start: str, end: str) -> list[list]:
        """siseJson: [날짜, 시가, 고가, 저가, 종가, 거래량, 외국인소진율]"""
        text = await self._get_text(
            "https://api.finance.naver.com/siseJson.naver",
            {"symbol": symbol, "requestType": "1", "startTime": start, "endTime": end, "timeframe": "day"},
        )
        try:
            rows = ast.literal_eval(re.sub(r"\s", "", text))
        except (ValueError, SyntaxError) as exc:
            raise UpstreamError("시세(네이버)", f"응답 파싱 실패: {symbol}") from exc
        return [r for r in rows[1:] if r and str(r[0]).isdigit()]

    async def latest_trading_day(self) -> str:
        cached = self._cache.get("mkt:latest_day")
        if cached:
            return cached
        from datetime import datetime, timedelta

        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
        rows = await self._daily_rows("KOSPI", start, end)
        if not rows:
            raise UpstreamError("시세(네이버)", "최근 거래일 조회 실패")
        day = str(rows[-1][0])
        self._cache.set("mkt:latest_day", day, 600)
        return day

    async def get_price_move(self, code: str, date: str | None = None) -> dict:
        """{date, close, change_pct, volume, volume_ratio}"""
        from datetime import datetime, timedelta

        day = date or await self.latest_trading_day()
        key = f"mkt:price:{code}:{day}"
        cached = self._cache.get(key)
        if cached:
            return cached

        start = (datetime.strptime(day, "%Y%m%d") - timedelta(days=60)).strftime("%Y%m%d")
        rows = [r for r in await self._daily_rows(code, start, day) if str(r[0]) <= day]
        if not rows:
            raise UpstreamError("시세(네이버)", f"시세 없음: {code}")

        last = rows[-1]
        prev_close = float(rows[-2][4]) if len(rows) >= 2 else None
        close, volume = float(last[4]), int(last[5])
        change_pct = round((close - prev_close) / prev_close * 100, 2) if prev_close else 0.0
        volumes = [int(r[5]) for r in rows[:-1][-20:]]
        avg_volume = sum(volumes) / len(volumes) if volumes else 0
        result = {
            "date": str(last[0]),
            "close": int(close),
            "change_pct": change_pct,
            "volume": volume,
            "volume_ratio": round(volume / avg_volume, 1) if avg_volume > 0 else None,
        }
        self._cache.set(key, result, PRICE_TTL)
        return result

    async def _frgn_rows(self, code: str, pages: int = 3) -> list[dict]:
        """기관·외국인 일별 순매매량(주). [{date, close, inst_shares, frgn_shares}]"""
        key = f"mkt:frgn:{code}:{pages}"
        cached = self._cache.get(key)
        if cached:
            return cached

        rows: list[dict] = []
        for page in range(1, pages + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout, headers=_UA) as client:
                    resp = await client.get(
                        "https://finance.naver.com/item/frgn.naver",
                        params={"code": code, "page": page},
                    )
                    resp.raise_for_status()
                    html = resp.content.decode("euc-kr", errors="ignore")
            except httpx.HTTPError as exc:
                raise UpstreamError("수급(네이버)", str(exc)) from exc
            rows.extend(_parse_frgn_table(html))
        self._cache.set(key, rows, PRICE_TTL)
        return rows

    async def get_flow_rows(self, code: str, pages: int = 3) -> list[dict]:
        """기관·외국인 일별 순매매 원자료 (차트용). [{date, close, inst_shares, frgn_shares}]"""
        return await self._frgn_rows(code, pages)

    async def get_investor_flow(self, code: str, date: str | None = None) -> dict:
        """당일 기관·외국인 순매수 추정대금(원). {date, 기관, 외국인} (개인 데이터는 미제공)"""
        day = date or await self.latest_trading_day()
        target = f"{day[:4]}.{day[4:6]}.{day[6:]}"
        rows = await self._frgn_rows(code, pages=1)
        row = next((r for r in rows if r["date"] == target), None)
        if row is None:
            raise UpstreamError("수급(네이버)", f"수급 없음: {code} {day}")
        return {
            "date": day,
            "기관": int(row["inst_shares"] * row["close"]),
            "외국인": int(row["frgn_shares"] * row["close"]),
            "unit_note": "순매매 주식수 × 종가 추정대금이에요.",
        }

    async def get_investor_flow_range(self, code: str, days: int, end: str | None = None) -> dict:
        """최근 N일 기관·외국인 누적 순매수 추정대금(원)."""
        from datetime import datetime, timedelta

        end_day = end or await self.latest_trading_day()
        cut = datetime.strptime(end_day, "%Y%m%d") - timedelta(days=days)
        rows = await self._frgn_rows(code, pages=max(2, days // 7))
        inst = frgn = 0
        start_seen = end_day
        for r in rows:
            dt = datetime.strptime(r["date"], "%Y.%m.%d")
            if dt < cut or dt > datetime.strptime(end_day, "%Y%m%d"):
                continue
            inst += int(r["inst_shares"] * r["close"])
            frgn += int(r["frgn_shares"] * r["close"])
            start_seen = min(start_seen, dt.strftime("%Y%m%d"))
        return {"start": start_seen, "end": end_day, "기관": inst, "외국인": frgn, "연기금": 0}

    async def get_index_change(self, market: str, date: str | None = None) -> float:
        from datetime import datetime, timedelta

        day = date or await self.latest_trading_day()
        symbol = "KOSPI" if market.upper() == "KOSPI" else "KOSDAQ"
        key = f"mkt:index:{symbol}:{day}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        start = (datetime.strptime(day, "%Y%m%d") - timedelta(days=10)).strftime("%Y%m%d")
        rows = [r for r in await self._daily_rows(symbol, start, day) if str(r[0]) <= day]
        if len(rows) < 2:
            return 0.0
        prev, last = float(rows[-2][4]), float(rows[-1][4])
        change = round((last - prev) / prev * 100, 2) if prev else 0.0
        self._cache.set(key, change, PRICE_TTL)
        return change

    async def get_price_series(self, code: str, days: int = 60) -> list[dict]:
        """일별 종가 시계열 (차트·스파크라인용). [{date, close, volume}]"""
        from datetime import datetime, timedelta

        end = await self.latest_trading_day()
        key = f"mkt:series:{code}:{end}:{days}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=int(days * 1.6))).strftime("%Y%m%d")
        rows = await self._daily_rows(code, start, end)
        series = [
            {"date": str(r[0]), "close": float(r[4]), "volume": int(r[5])}
            for r in rows
        ][-days:]
        self._cache.set(key, series, PRICE_TTL)
        return series

    async def get_stock_news(self, code: str, limit: int = 5) -> list[dict]:
        """종목 뉴스 헤드라인. [{title, press, datetime, url}] — 제목만 사용(본문 미인용)."""
        key = f"mkt:news:{code}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached[:limit]
        try:
            data = await self._get_json(
                f"https://m.stock.naver.com/api/news/stock/{code}?pageSize={max(limit, 5)}"
            )
        except UpstreamError:
            return []  # 뉴스는 보조 요인 — 실패해도 응답은 계속
        items: list[dict] = []
        for group in data if isinstance(data, list) else []:
            for it in group.get("items", []):
                office, article = it.get("officeId"), it.get("articleId")
                items.append({
                    "title": (it.get("title") or "").replace("&quot;", '"').strip(),
                    "press": it.get("officeName", ""),
                    "datetime": it.get("datetime", ""),
                    "url": f"https://n.news.naver.com/article/{office}/{article}" if office and article else "",
                })
        self._cache.set(key, items, 600)  # 뉴스는 10분
        return items[:limit]

    async def get_intraday_quote(self, code: str) -> dict | None:
        """장중 현재가 (지연 시세). {price, change_pct, market_status} — 실패 시 None."""
        key = f"mkt:quote:{code}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            data = await self._get_json(f"https://m.stock.naver.com/api/stock/{code}/basic")
        except UpstreamError:
            return None
        price = _num(data.get("closePrice"))
        ratio = _num(data.get("fluctuationsRatio"))
        if price is None:
            return None
        # 하락이면 fluctuationsRatio가 양수로 오는 경우 대비: compareToPreviousPrice 코드로 부호 결정
        direction = ((data.get("compareToPreviousPrice") or {}).get("code") or "")
        if ratio is not None and direction == "5":  # 5=하락
            ratio = -abs(ratio)
        result = {
            "price": int(price),
            "change_pct": ratio,
            "market_status": data.get("marketStatus", ""),
        }
        self._cache.set(key, result, 60)  # 장중 시세는 1분
        return result

    async def get_industry_compare(self, code: str) -> dict | None:
        """동종업계 비교. {industry_name, peers: [{name, code, change_pct}]} — 실패 시 None."""
        key = f"mkt:industry:{code}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            data = await self._get_json(f"https://m.stock.naver.com/api/stock/{code}/integration")
        except UpstreamError:
            return None
        peers = []
        for item in (data.get("industryCompareInfo") or [])[:6]:
            ratio = _num(item.get("fluctuationsRatio"))
            direction = ((item.get("compareToPreviousPrice") or {}).get("code") or "")
            if ratio is not None and direction == "5":
                ratio = -abs(ratio)
            peers.append({
                "name": item.get("stockName", ""),
                "code": item.get("itemCode", ""),
                "change_pct": ratio,
            })
        researches = [
            {"title": r.get("title", ""), "brokerName": r.get("brokerName", ""), "writeDate": r.get("writeDate", "")}
            for r in (data.get("researches") or [])[:3]
        ]
        result = {"peers": peers, "researches": researches} if peers or researches else None
        if result:
            self._cache.set(key, result, PRICE_TTL)
        return result

    async def get_market_of(self, code: str) -> str:
        key = f"mkt:market:{code}"
        cached = self._cache.get(key)
        if cached:
            return cached
        data = await self._get_json(f"https://m.stock.naver.com/api/stock/{code}/basic")
        market = "KOSDAQ" if str(data.get("sosok")) == "1" else "KOSPI"
        self._cache.set(key, market, 86400)
        return market

    async def get_fundamental(self, code: str, date: str | None = None) -> dict:
        """{per, pbr, div} — 네이버 integration totalInfos 기준."""
        key = f"mkt:fund1:{code}"
        cached = self._cache.get(key)
        if cached:
            return cached
        data = await self._get_json(f"https://m.stock.naver.com/api/stock/{code}/integration")
        infos = {i.get("code"): i.get("value") for i in data.get("totalInfos", [])}
        result = {
            "per": _num(infos.get("per")),
            "pbr": _num(infos.get("pbr")),
            "div": _num(infos.get("dividendYieldRatio")) or 0.0,
        }
        self._cache.set(key, result, PRICE_TTL)
        return result

    async def get_fundamental_snapshot(self, date: str | None = None) -> list[dict]:
        """스크리너용 스냅샷 — 시장별 시총 상위 {_SNAPSHOT_TOP_N}종목.

        전종목이 아닌 상위 종목으로 커버리지를 제한한다 (응답에 명시할 것).
        """
        key = "mkt:snapshot"
        cached = self._cache.get(key)
        if cached:
            return cached

        targets: list[tuple[str, str, str, int]] = []  # (code, name, market, close)
        for market in ("KOSPI", "KOSDAQ"):
            for page in range(1, _SNAPSHOT_TOP_N // 100 + 1):
                data = await self._get_json(
                    f"https://m.stock.naver.com/api/stocks/marketValue/{market}?page={page}&pageSize=100"
                )
                for item in data.get("stocks", []):
                    if item.get("stockEndType") != "stock":
                        continue
                    targets.append((
                        item["itemCode"],
                        item.get("stockName", item["itemCode"]),
                        market,
                        int(_num(item.get("closePrice")) or 0),
                    ))

        semaphore = asyncio.Semaphore(_SNAPSHOT_CONCURRENCY)

        async def fetch(code: str, name: str, market: str, close: int) -> dict | None:
            async with semaphore:
                try:
                    fund = await self.get_fundamental(code)
                except UpstreamError:
                    return None
            return {"code": code, "name": name, "market": market, "close": close, **fund}

        results = await asyncio.gather(*(fetch(*t) for t in targets))
        rows = [r for r in results if r is not None]
        self._cache.set(key, rows, SNAPSHOT_TTL)
        return rows


def _parse_frgn_table(html: str) -> list[dict]:
    """frgn.naver 일별 테이블: 날짜, 종가, 전일비, 등락률, 거래량, 기관, 외국인, ..."""
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [
            re.sub(r"<[^>]*>", "", c).strip().replace(",", "")
            for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        ]
        if len(cells) < 7 or not re.match(r"20\d\d\.\d\d\.\d\d", cells[0]):
            continue
        try:
            rows.append({
                "date": cells[0],
                "close": float(cells[1]),
                "inst_shares": float(cells[5]),
                "frgn_shares": float(cells[6]),
            })
        except ValueError:
            continue
    return rows
