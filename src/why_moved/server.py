"""왜움직여? MCP 서버 — FastMCP 기반, Streamable HTTP.

PlayMCP에는 이 서버의 공개 엔드포인트를 등록한다 (호스팅은 개발자 몫).
"""

import json
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from why_moved import __version__
from why_moved.common.envelope import contains_forbidden_phrase
from why_moved.common.errors import WhyMovedError
from why_moved.config import get_settings
from why_moved.context import AppContext, build_context
from why_moved.tools.daily_digest_tool import daily_digest
from why_moved.tools.explain_disclosure_tool import explain_disclosure
from why_moved.tools.insider_signal_tool import insider_signal
from why_moved.tools.risk_check_tool import risk_check
from why_moved.tools.screen_stocks_tool import screen_stocks
from why_moved.tools.stock_health_tool import stock_health
from why_moved.tools.why_moved_tool import why_moved

logger = logging.getLogger("why_moved")

mcp = FastMCP(
    "왜움직여",
    # 공개 호스팅(PlayMCP in KC) 환경: 프록시 뒤에서 다양한 Host 헤더로 접근되므로
    # localhost 전용 DNS 리바인딩 보호를 끈다 (로컬 비밀 없음, 공개 서비스)
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    instructions=(
        "주식 초보자를 위한 공시·시세 통역 도구입니다. "
        "모든 응답은 DART·KRX 공개 데이터의 사실 요약이며 투자 권유가 아닙니다. "
        "응답의 sources와 disclaimer를 사용자에게 함께 전달해 주세요."
    ),
)

_ctx: AppContext | None = None


def _context() -> AppContext:
    global _ctx
    if _ctx is None:
        _ctx = build_context(get_settings())
    return _ctx


async def _safe(coro) -> dict[str, Any]:
    """도메인 에러를 사용자 친화 응답으로 변환하고, 권유성 표현 안전망을 통과시킨다."""
    try:
        result = await coro
    except WhyMovedError as exc:
        return {"error": str(exc)}
    except Exception:
        logger.exception("unexpected error")
        return {"error": "일시적인 오류가 발생했어요. 잠시 후 다시 시도해 주세요."}

    forbidden = contains_forbidden_phrase(json.dumps(result, ensure_ascii=False))
    if forbidden:
        logger.error("forbidden phrase detected: %s", forbidden)
        return {"error": "응답 생성 중 내부 정책 위반이 감지되어 전달을 중단했어요."}
    return result


@mcp.tool(
    name="why_moved",
    description=(
        "종목이 오늘(또는 지정일) 왜 올랐거나 내렸는지 설명합니다. "
        "KRX 시세·수급과 DART 공시를 연결해 초보자 언어로 3줄 요약합니다. "
        "예: '삼성전자 오늘 왜 떨어졌어?'"
    ),
)
async def why_moved_tool(query: str, date: str | None = None) -> dict:
    """query: 종목명 또는 6자리 종목코드. date: YYYYMMDD (선택)."""
    return await _safe(why_moved(_context(), query, date))


@mcp.tool(
    name="risk_check",
    description=(
        "종목의 위험신호를 15개 룰로 진단합니다 (감사의견, 자본잠식, 관리종목, "
        "CB 남발, 최대주주 변경, 횡령·배임 등). 투자 전 안전 점검용. "
        "예: 'OO전자 위험한 종목이야?'"
    ),
)
async def risk_check_tool(query: str) -> dict:
    """query: 종목명 또는 6자리 종목코드."""
    return await _safe(risk_check(_context(), query))


@mcp.tool(
    name="explain_disclosure",
    description=(
        "공시를 초보자 언어로 통역합니다: 무슨 일이야? / 그래서 뭐? / 나랑 무슨 상관? "
        "keyword로 공시 유형을, rcept_no로 특정 공시를 지정할 수 있습니다. "
        "예: '삼성전자 유상증자 공시 설명해줘'"
    ),
)
async def explain_disclosure_tool(company: str, keyword: str = "", rcept_no: str = "") -> dict:
    """company: 종목명/코드. keyword: 공시 제목 키워드(선택). rcept_no: DART 접수번호(선택)."""
    return await _safe(explain_disclosure(_context(), company, keyword, rcept_no))


@mcp.tool(
    name="stock_health",
    description=(
        "종목의 재무 건강을 5축 점수(가치/성장/수익성/건전성/배당)와 A~F 학점, "
        "쉬운 문장으로 진단합니다. DART 사업보고서 + KRX 밸류에이션 기준. "
        "예: '카카오 재무 상태 어때?'"
    ),
)
async def stock_health_tool(query: str) -> dict:
    """query: 종목명 또는 6자리 종목코드."""
    return await _safe(stock_health(_context(), query))


@mcp.tool(
    name="insider_signal",
    description=(
        "회사 내부자(임원·주요주주)와 5% 큰손의 실제 매매, 기관·외국인 수급을 보여줍니다. "
        "법적으로 공시된 '진짜 돈'의 움직임입니다. "
        "예: '이 회사 임원들이 최근에 주식 샀어?'"
    ),
)
async def insider_signal_tool(query: str, days: int = 30) -> dict:
    """query: 종목명 또는 6자리 종목코드. days: 조회 기간(기본 30일)."""
    return await _safe(insider_signal(_context(), query, days))


@mcp.tool(
    name="daily_digest",
    description=(
        "오늘(또는 지정일)의 주요 공시를 초보자용 3문항으로 요약합니다. "
        "watchlist에 관심종목을 주면 우선 반영합니다. "
        "예: '오늘 중요한 공시 뭐 있었어?'"
    ),
)
async def daily_digest_tool(date: str | None = None, watchlist: list[str] | None = None) -> dict:
    """date: YYYYMMDD (선택, 기본 오늘). watchlist: 관심 종목명 목록 (선택)."""
    return await _safe(daily_digest(_context(), date, watchlist))


@mcp.tool(
    name="screen_stocks",
    description=(
        "자연어 조건으로 종목을 검색합니다. 지원 조건: 배당수익률·PER·PBR·주가·시장. "
        "예: '배당수익률 4% 이상, PBR 1 이하 코스피 종목 찾아줘'"
    ),
)
async def screen_stocks_tool(condition: str, limit: int = 20) -> dict:
    """condition: 자연어 조건. limit: 최대 결과 수(기본 20)."""
    return await _safe(screen_stocks(_context(), condition, limit))


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "why-moved", "version": __version__})


@mcp.custom_route("/charts/{chart_id}.png", methods=["GET"])
async def chart(request: Request) -> Response:
    """tool 응답의 chart_url이 가리키는 PNG 서빙."""
    path = _context().charts.path(request.path_params["chart_id"])
    if path is None:
        return JSONResponse({"error": "chart not found (만료되었을 수 있어요)"}, status_code=404)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    settings = get_settings()
    mcp.settings.host = settings.server_host
    mcp.settings.port = settings.server_port
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
