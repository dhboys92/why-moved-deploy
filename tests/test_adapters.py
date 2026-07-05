"""어댑터 테스트 — respx로 HTTP를 모킹해 실제 네트워크 없이 검증."""

import io
import zipfile

import httpx
import pytest
import respx

from why_moved.adapters.corp_codes import CorpCodeResolver
from why_moved.adapters.dart import DartClient
from why_moved.adapters.kind import KindClient
from why_moved.adapters.market_data import MarketDataClient
from why_moved.cache.store import TTLCache
from why_moved.common.errors import StockNotFoundError, UpstreamError


@pytest.fixture
def cache(tmp_path):
    return TTLCache(str(tmp_path / "cache.db"))


class TestDartClient:
    @respx.mock
    async def test_search_disclosures(self, cache):
        respx.get(url__startswith="https://opendart.fss.or.kr/api/list.json").mock(
            return_value=httpx.Response(200, json={
                "status": "000", "message": "정상",
                "list": [{"rcept_no": "1", "report_nm": "유상증자결정", "rcept_dt": "20260701"}],
            })
        )
        client = DartClient("key", cache)
        result = await client.search_disclosures(corp_code="00126380", bgn_de="20260601")
        assert result[0]["report_nm"] == "유상증자결정"

    @respx.mock
    async def test_search_uses_cache_on_second_call(self, cache):
        route = respx.get(url__startswith="https://opendart.fss.or.kr/api/list.json").mock(
            return_value=httpx.Response(200, json={"status": "000", "list": []})
        )
        client = DartClient("key", cache)
        await client.search_disclosures(corp_code="X", bgn_de="20260601")
        await client.search_disclosures(corp_code="X", bgn_de="20260601")
        assert route.call_count == 1  # 두 번째는 캐시

    @respx.mock
    async def test_no_result_status_is_ok(self, cache):
        respx.get(url__startswith="https://opendart.fss.or.kr/api/list.json").mock(
            return_value=httpx.Response(200, json={"status": "013", "message": "조회된 데이타가 없습니다."})
        )
        client = DartClient("key", cache)
        assert await client.search_disclosures(corp_code="X") == []

    @respx.mock
    async def test_error_status_raises(self, cache):
        respx.get(url__startswith="https://opendart.fss.or.kr/api/list.json").mock(
            return_value=httpx.Response(200, json={"status": "020", "message": "한도 초과"})
        )
        client = DartClient("key", cache)
        with pytest.raises(UpstreamError):
            await client.search_disclosures(corp_code="X")

    @respx.mock
    async def test_financials_falls_back_to_ofs(self, cache):
        route = respx.get(url__startswith="https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json")
        route.side_effect = [
            httpx.Response(200, json={"status": "013"}),  # CFS 없음
            httpx.Response(200, json={"status": "000", "list": [{"account_nm": "매출액"}]}),
        ]
        client = DartClient("key", cache)
        rows = await client.get_financials("X", "2025", "11011")
        assert rows[0]["account_nm"] == "매출액"

    @respx.mock
    async def test_network_error_wrapped(self, cache):
        respx.get(url__startswith="https://opendart.fss.or.kr").mock(
            side_effect=httpx.ConnectError("boom")
        )
        client = DartClient("key", cache)
        with pytest.raises(UpstreamError):
            await client.get_company("X")


def _corp_zip() -> bytes:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <result>
      <list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name><stock_code>005930</stock_code></list>
      <list><corp_code>00126390</corp_code><corp_name>삼성물산</corp_name><stock_code>028260</stock_code></list>
      <list><corp_code>00164742</corp_code><corp_name>현대자동차</corp_name><stock_code>005380</stock_code></list>
      <list><corp_code>00266961</corp_code><corp_name>NAVER</corp_name><stock_code>035420</stock_code></list>
      <list><corp_code>99999999</corp_code><corp_name>비상장회사</corp_name><stock_code> </stock_code></list>
    </result>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml)
    return buf.getvalue()


class TestCorpCodeResolver:
    @respx.mock
    async def test_resolve_by_name_and_code(self, cache):
        respx.get(url__startswith="https://opendart.fss.or.kr/api/corpCode.xml").mock(
            return_value=httpx.Response(200, content=_corp_zip())
        )
        resolver = CorpCodeResolver("key", cache)
        by_name = await resolver.resolve("삼성전자")
        assert by_name.corp_code == "00126380"
        by_code = await resolver.resolve("005380")
        assert by_code.name == "현대자동차"

    @respx.mock
    async def test_unlisted_excluded_and_unknown_raises(self, cache):
        respx.get(url__startswith="https://opendart.fss.or.kr/api/corpCode.xml").mock(
            return_value=httpx.Response(200, content=_corp_zip())
        )
        resolver = CorpCodeResolver("key", cache)
        with pytest.raises(StockNotFoundError):
            await resolver.resolve("비상장회사")

    @respx.mock
    async def test_partial_match_only_when_unique(self, cache):
        respx.get(url__startswith="https://opendart.fss.or.kr/api/corpCode.xml").mock(
            return_value=httpx.Response(200, content=_corp_zip())
        )
        resolver = CorpCodeResolver("key", cache)
        corp = await resolver.resolve("현대자동")
        assert corp.name == "현대자동차"

    @respx.mock
    async def test_alias_naver(self, cache):
        respx.get(url__startswith="https://opendart.fss.or.kr/api/corpCode.xml").mock(
            return_value=httpx.Response(200, content=_corp_zip())
        )
        resolver = CorpCodeResolver("key", cache)
        assert (await resolver.resolve("네이버")).name == "NAVER"
        assert (await resolver.resolve("naver")).name == "NAVER"

    @respx.mock
    async def test_ambiguous_returns_candidates(self, cache):
        from why_moved.common.errors import AmbiguousStockError

        respx.get(url__startswith="https://opendart.fss.or.kr/api/corpCode.xml").mock(
            return_value=httpx.Response(200, content=_corp_zip())
        )
        resolver = CorpCodeResolver("key", cache)
        with pytest.raises(AmbiguousStockError) as exc_info:
            await resolver.resolve("삼성")
        assert set(exc_info.value.candidates) == {"삼성전자", "삼성물산"}

    @respx.mock
    async def test_etf_guidance(self, cache):
        from why_moved.common.errors import EtfNotSupportedError

        respx.get(url__startswith="https://opendart.fss.or.kr/api/corpCode.xml").mock(
            return_value=httpx.Response(200, content=_corp_zip())
        )
        resolver = CorpCodeResolver("key", cache)
        with pytest.raises(EtfNotSupportedError):
            await resolver.resolve("KODEX 200")


_SISE_BODY = """[['날짜', '시가', '고가', '저가', '종가', '거래량', '외국인소진율'],
["20260702", 290000, 315000, 286000, 286000, 38905074, 46.76],
["20260703", 309000, 310000, 305000, 309500, 31498600, 46.74]]"""


class TestMarketDataClient:
    @respx.mock
    async def test_price_move(self, cache):
        respx.get(url__startswith="https://api.finance.naver.com/siseJson.naver").mock(
            return_value=httpx.Response(200, text=_SISE_BODY)
        )
        m = MarketDataClient(cache)
        result = await m.get_price_move("005930", "20260703")
        assert result["close"] == 309500
        assert result["change_pct"] == round((309500 - 286000) / 286000 * 100, 2)

    @respx.mock
    async def test_market_of(self, cache):
        respx.get("https://m.stock.naver.com/api/stock/035720/basic").mock(
            return_value=httpx.Response(200, json={"sosok": "0"})
        )
        m = MarketDataClient(cache)
        assert await m.get_market_of("035720") == "KOSPI"

    @respx.mock
    async def test_fundamental_parsing(self, cache):
        respx.get("https://m.stock.naver.com/api/stock/005930/integration").mock(
            return_value=httpx.Response(200, json={"totalInfos": [
                {"code": "per", "value": "25.02배"},
                {"code": "pbr", "value": "4.30배"},
                {"code": "dividendYieldRatio", "value": "0.54%"},
            ]})
        )
        m = MarketDataClient(cache)
        fund = await m.get_fundamental("005930")
        assert fund == {"per": 25.02, "pbr": 4.3, "div": 0.54}

    @respx.mock
    async def test_upstream_error_wrapped(self, cache):
        respx.get(url__startswith="https://api.finance.naver.com").mock(
            side_effect=httpx.ConnectError("down")
        )
        m = MarketDataClient(cache)
        with pytest.raises(UpstreamError):
            await m.get_price_move("005930", "20260703")

    @respx.mock
    async def test_top_movers_filters_spac_and_etf(self, cache):
        def payload(stocks):
            return httpx.Response(200, json={"stocks": stocks})

        respx.get(url__startswith="https://m.stock.naver.com/api/stocks/up/KOSPI").mock(
            return_value=payload([
                {"stockEndType": "stock", "stockName": "급등전자", "itemCode": "000001",
                 "fluctuationsRatio": "29.9", "closePrice": "1,000",
                 "compareToPreviousPrice": {"code": "1"}},
                {"stockEndType": "stock", "stockName": "하나스팩10호", "itemCode": "000002",
                 "fluctuationsRatio": "25.0", "closePrice": "2,000"},
                {"stockEndType": "etf", "stockName": "KODEX 레버리지", "itemCode": "000003",
                 "fluctuationsRatio": "20.0", "closePrice": "3,000"},
            ])
        )
        respx.get(url__startswith="https://m.stock.naver.com/api/stocks/up/KOSDAQ").mock(
            return_value=payload([])
        )
        m = MarketDataClient(cache)
        movers = await m.get_top_movers("up")
        assert [x["name"] for x in movers] == ["급등전자"]  # 스팩·ETF 제외


_KIND_HTML = """<table>
<tr><td><a onclick="companysummary_open('123456')">부실기업</a></td></tr>
</table>"""


class TestKindClient:
    @respx.mock
    async def test_managed_lookup(self, cache):
        respx.post(url__startswith="https://kind.krx.co.kr/investwarn/adminissue.do").mock(
            return_value=httpx.Response(200, text=_KIND_HTML)
        )
        kind = KindClient(cache)
        assert await kind.is_managed("123456") is True
        assert await kind.is_managed("005930") is False

    @respx.mock
    async def test_failure_returns_none(self, cache):
        respx.post(url__startswith="https://kind.krx.co.kr").mock(
            side_effect=httpx.ConnectError("down")
        )
        kind = KindClient(cache)
        assert await kind.is_managed("123456") is None


class TestDartDateNormalization:
    @respx.mock
    async def test_elestock_dates_normalized(self, cache):
        respx.get(url__startswith="https://opendart.fss.or.kr/api/elestock.json").mock(
            return_value=httpx.Response(200, json={
                "status": "000",
                "list": [{"rcept_no": "1", "rcept_dt": "2026-07-01", "sp_stock_lmp_irds_cnt": "100"}],
            })
        )
        client = DartClient("key", cache)
        rows = await client.get_executive_holdings("X")
        assert rows[0]["rcept_dt"] == "20260701"
