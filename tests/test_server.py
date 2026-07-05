"""서버 레이어 테스트 — _safe 래퍼 계약과 tool 등록."""

import pytest

from why_moved.common.errors import StockNotFoundError
from why_moved.server import _safe, mcp


class TestSafeWrapper:
    async def test_passes_through_normal_result(self):
        async def ok():
            return {"value": 1, "disclaimer": "d", "sources": []}

        result = await _safe(ok())
        assert result["value"] == 1

    async def test_domain_error_becomes_friendly_message(self):
        async def boom():
            raise StockNotFoundError("없는종목")

        result = await _safe(boom())
        assert "없는종목" in result["error"]

    async def test_unexpected_error_hidden(self):
        async def boom():
            raise RuntimeError("internal secret detail")

        result = await _safe(boom())
        assert "secret" not in result["error"]
        assert "일시적인 오류" in result["error"]

    async def test_forbidden_phrase_blocked(self):
        async def bad():
            return {"text": "이 종목을 지금 매수하세요"}

        result = await _safe(bad())
        assert "error" in result
        assert "매수하세요" not in str(result)


class TestToolRegistration:
    async def test_all_nine_tools_registered(self):
        tools = {t.name for t in await mcp.list_tools()}
        assert tools == {
            "why_moved", "risk_check", "explain_disclosure", "stock_health",
            "insider_signal", "daily_digest", "screen_stocks",
            "today_movers", "disclosure_quiz",
        }

    async def test_tool_descriptions_have_examples(self):
        for tool in await mcp.list_tools():
            assert "예:" in tool.description, tool.name
