"""tool 통합 테스트 — 어댑터 모킹, envelope 계약 검증."""

import pytest

from why_moved.common.envelope import DISCLAIMER
from why_moved.common.errors import DisclosureNotFoundError
from why_moved.tools.daily_digest_tool import daily_digest
from why_moved.tools.explain_disclosure_tool import explain_disclosure
from why_moved.tools.insider_signal_tool import insider_signal
from why_moved.tools.risk_check_tool import risk_check
from why_moved.tools.screen_stocks_tool import screen_stocks
from why_moved.tools.stock_health_tool import stock_health
from why_moved.tools.why_moved_tool import why_moved

DISCLOSURE = {
    "rcept_no": "20260703000001",
    "corp_name": "삼성전자",
    "report_nm": "주요사항보고서(유상증자결정)",
    "rcept_dt": "20260703",
}


def _assert_envelope(result: dict):
    """모든 tool 응답의 공통 계약: 출처 + 고지문구 (설계 §1 원칙 1·2)."""
    assert result["disclaimer"] == DISCLAIMER
    assert isinstance(result["sources"], list)


class TestWhyMoved:
    async def test_with_disclosure_factor(self, mock_ctx):
        mock_ctx.dart.search_disclosures.return_value = [DISCLOSURE]
        result = await why_moved(mock_ctx, "삼성전자")
        _assert_envelope(result)
        assert result["stock"]["name"] == "삼성전자"
        types = {f["type"] for f in result["factors"]}
        assert "disclosure" in types
        assert "flow" in types  # 기관 1.35조 순매수 > 10억 기준
        assert "유상증자" in result["explanation"] or "확인된 공개 요인" in result["explanation"]

    async def test_no_factors_is_honest(self, mock_ctx):
        mock_ctx.dart.search_disclosures.return_value = []
        mock_ctx.market.get_investor_flow.return_value = {
            "date": "20260703", "기관": 100, "외국인": -100, "unit_note": "",
        }
        result = await why_moved(mock_ctx, "삼성전자")
        assert "뚜렷한 공개 요인을 찾지 못했어요" in result["explanation"]

    async def test_flow_failure_does_not_crash(self, mock_ctx):
        mock_ctx.market.get_investor_flow.side_effect = RuntimeError("down")
        result = await why_moved(mock_ctx, "삼성전자")
        _assert_envelope(result)
        assert result["investor_flow"] is None


class TestRiskCheck:
    async def test_clean_stock(self, mock_ctx):
        result = await risk_check(mock_ctx, "삼성전자")
        _assert_envelope(result)
        assert result["risk_level"] == "안전"
        assert result["signals"] == []
        assert result["checked_rules"] == 15

    async def test_managed_stock_flagged(self, mock_ctx):
        mock_ctx.kind.is_managed.return_value = True
        result = await risk_check(mock_ctx, "삼성전자")
        assert result["risk_level"] == "위험"
        assert any(s["rule_id"] == "R03" for s in result["signals"])
        assert "🚨" in result["summary"]

    async def test_financial_failure_degrades_gracefully(self, mock_ctx):
        mock_ctx.dart.get_financials.side_effect = RuntimeError("dart down")
        result = await risk_check(mock_ctx, "삼성전자")
        _assert_envelope(result)
        assert "R02" in result["unavailable_rules"]


class TestExplainDisclosure:
    async def test_explains_latest(self, mock_ctx):
        mock_ctx.dart.search_disclosures.return_value = [DISCLOSURE]
        result = await explain_disclosure(mock_ctx, "삼성전자")
        _assert_envelope(result)
        assert result["disclosure"]["type"] == "유상증자"
        assert result["what_happened"]
        assert result["so_what"]
        assert result["why_care"]
        assert any(t["term"] == "유상증자" for t in result["terms"])

    async def test_keyword_filter(self, mock_ctx):
        other = {**DISCLOSURE, "report_nm": "현금ㆍ현물배당결정", "rcept_no": "2"}
        mock_ctx.dart.search_disclosures.return_value = [other, DISCLOSURE]
        result = await explain_disclosure(mock_ctx, "삼성전자", keyword="유상증자")
        assert result["disclosure"]["type"] == "유상증자"

    async def test_not_found_raises(self, mock_ctx):
        mock_ctx.dart.search_disclosures.return_value = []
        with pytest.raises(DisclosureNotFoundError):
            await explain_disclosure(mock_ctx, "삼성전자")


class TestStockHealth:
    async def test_healthy_output(self, mock_ctx):
        mock_ctx.dart.get_financials.return_value = [
            {"sj_div": "IS", "account_nm": "매출액", "thstrm_amount": "1,000", "frmtrm_amount": "900"},
            {"sj_div": "IS", "account_nm": "영업이익", "thstrm_amount": "100", "frmtrm_amount": "90"},
            {"sj_div": "IS", "account_nm": "당기순이익", "thstrm_amount": "80"},
            {"sj_div": "BS", "account_nm": "자본총계", "thstrm_amount": "500"},
            {"sj_div": "BS", "account_nm": "부채총계", "thstrm_amount": "400"},
        ]
        result = await stock_health(mock_ctx, "삼성전자")
        _assert_envelope(result)
        assert result["grade"] not in ("N/A", "F")
        assert result["checks"]
        assert "통과했어요" in result["narrative"]

    async def test_no_financials_honest(self, mock_ctx):
        mock_ctx.dart.get_financials.return_value = []
        result = await stock_health(mock_ctx, "삼성전자")
        assert result["data_basis"]["fiscal_period"] == "재무 데이터 없음"


class TestInsiderSignal:
    async def test_executive_buy_event(self, mock_ctx):
        mock_ctx.dart.get_executive_holdings.return_value = [{
            "rcept_no": "1", "rcept_dt": "20260701", "repror": "홍길동",
            "isu_exctv_ofcps": "대표이사", "sp_stock_lmp_irds_cnt": "10,000",
        }]
        result = await insider_signal(mock_ctx, "삼성전자", days=30)
        _assert_envelope(result)
        assert result["events"][0]["action"] == "매수"
        # 표 기반 UI용 평탄화 필드
        assert result["events"][0]["detail"] == "10,000주 · 사유 미표기 (신호 보통)"
        assert "매수 1건" in result["summary"]

    async def test_no_events(self, mock_ctx):
        result = await insider_signal(mock_ctx, "삼성전자")
        assert result["events"] == []
        assert "없었어요" in result["summary"]


class TestDailyDigest:
    async def test_important_disclosure_selected(self, mock_ctx):
        minor = {**DISCLOSURE, "report_nm": "기타경영사항", "rcept_no": "2", "corp_name": "기타회사"}
        mock_ctx.dart.search_disclosures.return_value = [minor, DISCLOSURE]
        result = await daily_digest(mock_ctx, date="20260703")
        _assert_envelope(result)
        assert len(result["top_disclosures"]) == 1  # 중요도 3 미만은 제외
        assert result["top_disclosures"][0]["type"] == "유상증자"

    async def test_watchlist_bonus(self, mock_ctx):
        a = {**DISCLOSURE, "corp_name": "회사A", "rcept_no": "1"}
        b = {**DISCLOSURE, "corp_name": "회사B", "rcept_no": "2"}
        mock_ctx.dart.search_disclosures.return_value = [a, b]
        result = await daily_digest(mock_ctx, date="20260703", watchlist=["회사B"])
        assert result["top_disclosures"][0]["company"] == "회사B"
        assert result["top_disclosures"][0]["in_watchlist"] is True


class TestScreenStocks:
    async def test_screening(self, mock_ctx):
        mock_ctx.market.get_fundamental_snapshot.return_value = [
            {"code": "005930", "market": "KOSPI", "per": 25.0, "pbr": 4.3, "div": 0.54, "close": 309500},
            {"code": "000660", "market": "KOSPI", "per": 8.0, "pbr": 0.9, "div": 4.2, "close": 100000},
        ]
        mock_ctx.resolver.name_map.return_value = {"005930": "삼성전자", "000660": "SK하이닉스"}
        result = await screen_stocks(mock_ctx, "배당수익률 4% 이상 PBR 1 이하 코스피")
        _assert_envelope(result)
        assert result["total_matched"] == 1
        assert result["results"][0]["name"] == "SK하이닉스"

    async def test_unparseable_condition(self, mock_ctx):
        result = await screen_stocks(mock_ctx, "느낌 좋은 종목")
        assert result["results"] == []
        assert "이해하지 못했어요" in result["note"]
