"""v1.1 시각화 레이어 테스트 — textviz, chart_store, charts 스모크."""

import pytest

from why_moved.cache.chart_store import ChartStore
from why_moved.engine import charts
from why_moved.engine.textviz import score_bar, sparkline


class TestTextviz:
    def test_sparkline_shape(self):
        s = sparkline([1, 2, 3, 4, 5, 6, 7, 8])
        assert s[0] == "▁"
        assert s[-1] == "█"

    def test_sparkline_flat(self):
        assert set(sparkline([5, 5, 5])) == {"▄"}

    def test_sparkline_empty(self):
        assert sparkline([]) == ""

    def test_sparkline_compresses_to_width(self):
        assert len(sparkline(list(range(100)), width=20)) == 20

    def test_score_bar(self):
        bar = score_bar(70, width=10)
        assert bar.count("█") == 7
        assert bar.endswith("70")

    def test_score_bar_none(self):
        assert "—" in score_bar(None)


class TestChartStore:
    def test_save_and_serve(self, tmp_path):
        store = ChartStore(str(tmp_path))
        cid = store.save(b"\x89PNG-data", "tool", "005930", "20260703")
        assert store.exists("tool", "005930", "20260703") == cid
        assert store.path(cid).read_bytes() == b"\x89PNG-data"

    def test_deterministic_id(self, tmp_path):
        store = ChartStore(str(tmp_path))
        assert store.chart_id("a", "b") == store.chart_id("a", "b")
        assert store.chart_id("a", "b") != store.chart_id("a", "c")

    def test_path_traversal_blocked(self, tmp_path):
        store = ChartStore(str(tmp_path))
        assert store.path("../etc/passwd") is None


_SERIES = [{"date": f"202606{d:02d}", "close": 100 + d, "volume": 10} for d in range(1, 21)]


class TestCharts:
    def _assert_png(self, data: bytes):
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(data) > 5000

    def test_price_with_disclosures(self):
        png = charts.price_with_disclosures(
            "테스트", _SERIES,
            [{"rcept_dt": "20260610", "type_name": "유상증자", "no": 1},
             {"rcept_dt": "20260610", "type_name": "전환사채 발행", "no": 2},
             {"rcept_dt": "20260615", "type_name": "합병", "no": 3}],
        )
        self._assert_png(png)

    def test_price_without_disclosures(self):
        self._assert_png(charts.price_with_disclosures("테스트", _SERIES, []))

    def test_health_radar(self):
        scores = {"value": 40, "growth": 90, "profitability": 70, "stability": 85, "dividend": 10}
        self._assert_png(charts.health_radar("테스트", scores, "B+"))

    def test_flow_with_price(self):
        flows = [
            {"date": f"2026.06.{d:02d}", "close": 100 + d, "inst_shares": 1000 * (-1) ** d, "frgn_shares": 500}
            for d in range(1, 21)
        ]
        self._assert_png(charts.flow_with_price("테스트", _SERIES, flows))

    def test_risk_card(self):
        signals = [{"severity": "경고", "title": "전환사채 반복 발행"}]
        self._assert_png(charts.risk_card("테스트", "경고", signals, 15))


class TestToolChartIntegration:
    async def test_why_moved_includes_chart_url(self, mock_ctx):
        from why_moved.tools.why_moved_tool import why_moved

        result = await why_moved(mock_ctx, "삼성전자")
        assert result["chart_url"].startswith("http://testserver/charts/")
        assert result["trend_sparkline_30d"]

    async def test_chart_events_map_to_markers(self, mock_ctx):
        from why_moved.tools.why_moved_tool import why_moved

        mock_ctx.dart.search_disclosures.return_value = [
            {"rcept_no": "1", "report_nm": "주요사항보고서(유상증자결정)", "rcept_dt": "20260610"},
            {"rcept_no": "2", "report_nm": "전환사채권발행결정", "rcept_dt": "20260615"},
        ]
        result = await why_moved(mock_ctx, "삼성전자")
        events = result["chart_events"]
        assert [e["no"] for e in events] == [1, 2]
        assert events[0]["type"] == "유상증자"
        assert events[0]["url"].startswith("https://dart.fss.or.kr")
        assert "chart_events" in result["chart_hint"]

    async def test_stock_health_scores_visual(self, mock_ctx):
        from why_moved.tools.stock_health_tool import stock_health

        mock_ctx.dart.get_financials.return_value = [
            {"sj_div": "IS", "account_nm": "매출액", "thstrm_amount": "1,000", "frmtrm_amount": "900"},
            {"sj_div": "IS", "account_nm": "영업이익", "thstrm_amount": "100", "frmtrm_amount": "90"},
            {"sj_div": "BS", "account_nm": "자본총계", "thstrm_amount": "500"},
            {"sj_div": "BS", "account_nm": "부채총계", "thstrm_amount": "400"},
        ]
        result = await stock_health(mock_ctx, "삼성전자")
        assert "█" in result["scores_visual"]
        assert result["chart_url"]


class TestNewsFactor:
    async def test_news_factor_added(self, mock_ctx):
        from why_moved.tools.why_moved_tool import why_moved

        mock_ctx.market.get_stock_news.return_value = [
            {"title": "삼성전자 대규모 수주", "press": "테스트일보", "datetime": "202607031000",
             "url": "https://n.news.naver.com/article/1/2"},
        ]
        result = await why_moved(mock_ctx, "삼성전자")
        assert any(f["type"] == "news" for f in result["factors"])
        assert any("뉴스" in s["title"] for s in result["sources"])

    async def test_intraday_note_when_market_open(self, mock_ctx):
        from why_moved.tools.why_moved_tool import why_moved

        mock_ctx.market.get_intraday_quote.return_value = {
            "price": 310000, "change_pct": 1.5, "market_status": "OPEN",
        }
        result = await why_moved(mock_ctx, "삼성전자")
        assert "장중" in result["explanation"]

    async def test_sector_factor(self, mock_ctx):
        from why_moved.tools.why_moved_tool import why_moved

        mock_ctx.market.get_industry_compare.return_value = {
            "peers": [
                {"name": "SK하이닉스", "code": "000660", "change_pct": 5.0},
                {"name": "한미반도체", "code": "042700", "change_pct": 4.0},
            ],
            "researches": [],
        }
        result = await why_moved(mock_ctx, "삼성전자")  # price_move 기본 +8.22%
        assert any(f["type"] == "sector" for f in result["factors"])
