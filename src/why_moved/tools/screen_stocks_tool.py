"""screen_stocks — 자연어 스크리너 (설계 §2.7)."""

from dataclasses import asdict

from why_moved.common.envelope import envelope, source
from why_moved.context import AppContext
from why_moved.engine.screener_parser import apply_conditions, parse_conditions


async def screen_stocks(ctx: AppContext, condition: str, limit: int = 20) -> dict:
    conditions, unsupported = parse_conditions(condition)

    if not conditions:
        payload = {
            "parsed_conditions": [],
            "unsupported_conditions": unsupported or [condition],
            "results": [],
            "total_matched": 0,
            "note": (
                "조건을 이해하지 못했어요. 지금은 배당수익률·PER·PBR·주가·시장(코스피/코스닥) "
                "조건을 지원해요. 예: '배당수익률 4% 이상, PBR 1 이하 코스피'"
            ),
        }
        return envelope(payload, [])

    rows = await ctx.market.get_fundamental_snapshot()
    names = await ctx.resolver.name_map()
    matched = apply_conditions(rows, conditions, limit)

    # DART 마스터에 없는 종목(우선주·리츠·스팩 등)은 collector 마스터에서 이름 보충
    missing = [(r["market"], r["code"]) for r in matched if r["code"] not in names]
    if missing and ctx.names is not None:
        names = {**names, **(await ctx.names.lookup(missing))}

    day = await ctx.market.latest_trading_day()
    payload = {
        "parsed_conditions": [asdict(c) for c in conditions],
        "unsupported_conditions": unsupported,
        "results": [
            {
                "name": names.get(r["code"], r["code"]),
                "code": r["code"],
                "market": r["market"],
                "close": r["close"],
                "per": r["per"] or None,
                "pbr": r["pbr"] or None,
                "div": r["div"],
            }
            for r in matched
        ],
        "total_matched": len(matched),
        "note": (
            f"{day} 종가 기준이며, 검색 대상은 코스피·코스닥 시가총액 상위 각 200종목(총 400종목)이에요. "
            "조건에 맞는 종목의 나열일 뿐, 매수 대상 목록이 아니에요. "
            "각 종목은 risk_check로 위험신호를 함께 확인해 보세요."
        ),
    }
    return envelope(payload, [source("네이버 금융 밸류에이션 (시총 상위 400종목)", "https://m.stock.naver.com", day)])
