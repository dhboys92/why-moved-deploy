"""[신규 콘텐츠] today_movers — 오늘의 급등/급락주 해부.

급등 상위 종목을 자동 스캔해 각각의 공개 요인(공시·뉴스)을 한 번에 해부하는
데일리 시장 브리핑. "오늘 뭐가 왜 올랐어?" 한 마디로 실행된다.
"""

import asyncio
from datetime import datetime, timedelta

from why_moved.common.envelope import dart_viewer_url, envelope, source
from why_moved.context import AppContext
from why_moved.engine import charts
from why_moved.engine.disclosure_templates import match_template

_TOP_N = 5


async def today_movers(ctx: AppContext, direction: str = "up") -> dict:
    direction = "down" if direction == "down" else "up"
    movers = await ctx.market.get_top_movers(direction, limit=_TOP_N)
    day = await ctx.market.latest_trading_day()

    investigated = await asyncio.gather(
        *(_investigate(ctx, m, day) for m in movers)
    )

    chart_url = None
    try:
        key = ("today_movers", direction, day)
        chart_id = ctx.charts.exists(*key)
        if chart_id is None:
            all_movers = await ctx.market.get_top_movers(direction, limit=10)
            png = await asyncio.to_thread(charts.movers_bar, all_movers, direction, day)
            chart_id = ctx.charts.save(png, *key)
        chart_url = ctx.chart_url(chart_id)
    except Exception:
        pass

    word = "급등" if direction == "up" else "급락"
    with_reasons = sum(1 for m in investigated if m["reasons"])
    summary = (
        f"오늘(최근 거래일 {day[4:6]}/{day[6:]}) {word} 상위 {len(investigated)}종목을 해부했어요. "
        f"{with_reasons}종목에서 공시·뉴스 요인을 찾았고, "
        f"요인이 안 보이는 {word}은 소문·테마성일 수 있으니 더 조심스럽게 보세요."
    )

    payload = {
        "direction": direction,
        "date": day,
        "movers": investigated,
        "summary": summary,
        "chart_url": chart_url,
        "chart_hint": f"chart_url은 오늘의 {word} TOP10 막대 차트입니다. 사용자에게 보여주세요." if chart_url else None,
        "suggested_questions": (
            [f"1위 {investigated[0]['name']} 위험신호를 점검해볼까요?",
             f"{investigated[0]['name']}이(가) 왜 움직였는지 자세히 볼까요?"]
            if investigated else []
        ),
        "note": "스팩·ETF를 제외한 일반 종목 기준이에요. 급등주 추격 매수는 통상 가장 위험한 매매 유형으로 알려져 있어요.",
    }
    return envelope(payload, [
        source("네이버 금융 등락 상위 (KRX 데이터)", "https://m.stock.naver.com", day),
        source("DART 전자공시", "https://dart.fss.or.kr", day),
    ])


async def _investigate(ctx: AppContext, mover: dict, day: str) -> dict:
    """경량 원인 조사: D-3 중요 공시(유형 병합) + 뉴스 헤드라인 1건."""
    reasons: list[dict] = []

    try:
        corp = await ctx.resolver.resolve(mover["code"])
        bgn = (datetime.strptime(day, "%Y%m%d") - timedelta(days=3)).strftime("%Y%m%d")
        disclosures = await ctx.dart.search_disclosures(
            corp_code=corp.corp_code, bgn_de=bgn, end_de=day, page_count=20
        )
        seen_types: set[str] = set()
        for d in disclosures:
            template = match_template(d.get("report_nm", ""))
            if template.importance < 3 or template.type_name in seen_types:
                continue
            seen_types.add(template.type_name)
            reasons.append({
                "type": "disclosure",
                "summary": f"{template.type_name} 공시 — {template.what_happened}",
                "source_url": dart_viewer_url(d["rcept_no"]),
            })
            if len(reasons) >= 2:
                break
    except Exception:
        pass  # DART 미등록(신규상장 등) — 등락률만 표시

    try:
        news = await ctx.market.get_stock_news(mover["code"], limit=1)
        if news:
            reasons.append({
                "type": "news",
                "summary": f"뉴스: “{news[0]['title']}” ({news[0]['press']})",
                "source_url": news[0]["url"],
            })
    except Exception:
        pass

    return {**mover, "reasons": reasons}
