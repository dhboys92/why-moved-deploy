"""daily_digest — 오늘의 공시 3문항 다이제스트 (설계 §2.6)."""

from datetime import datetime

from why_moved.common.envelope import dart_viewer_url, envelope, source
from why_moved.context import AppContext
from why_moved.engine.disclosure_templates import match_template

_WATCHLIST_BONUS = 2
_TOP_N = 5


async def daily_digest(
    ctx: AppContext,
    date: str | None = None,
    watchlist: list[str] | None = None,
) -> dict:
    day = date or datetime.now().strftime("%Y%m%d")
    watch_names = set(watchlist or [])

    disclosures = await ctx.dart.search_disclosures(bgn_de=day, end_de=day, page_count=100)

    # v1.1: 시총 상위 종목 가중 — 이미 캐시된 스냅샷이 있으면 활용 (없으면 1회 구축)
    large_caps: set[str] = set()
    try:
        name_map = await ctx.resolver.name_map()
        code_by_name = {v: k for k, v in name_map.items()}
        snapshot = await ctx.market.get_fundamental_snapshot()
        large_caps = {r["code"] for r in snapshot}
    except Exception:
        code_by_name = {}

    scored = []
    for d in disclosures:
        template = match_template(d.get("report_nm", ""))
        score = template.importance
        corp_name = d.get("corp_name", "")
        if corp_name in watch_names:
            score += _WATCHLIST_BONUS
        if code_by_name.get(corp_name) in large_caps:
            score += 1  # 시총 상위(코스피·코스닥 각 200) 종목 공시 우선
        if score >= 3:
            scored.append((score, d, template))
    scored.sort(key=lambda x: x[0], reverse=True)

    top = [
        {
            "company": d.get("corp_name", ""),
            "title": d.get("report_nm", ""),
            "type": t.type_name,
            "what_happened": t.what_happened,
            "so_what": t.so_what,
            "why_care": t.why_care,
            "in_watchlist": d.get("corp_name") in watch_names,
            "url": dart_viewer_url(d.get("rcept_no", "")),
        }
        for _, d, t in scored[:_TOP_N]
    ]

    market_note = await _market_note(ctx)

    payload = {
        "date": day,
        "top_disclosures": top,
        "total_disclosures_today": len(disclosures),
        "market_note": market_note,
        "note": (
            "오늘 나온 공시 중 초보자에게 중요한 것만 골랐어요."
            if top else "오늘은 초보자에게 중요한 등급의 공시가 아직 없어요."
        ),
    }
    return envelope(payload, [source("DART 전자공시 (당일)", "https://dart.fss.or.kr", day)])


async def _market_note(ctx: AppContext) -> str:
    try:
        kospi = await ctx.market.get_index_change("KOSPI")
        kosdaq = await ctx.market.get_index_change("KOSDAQ")
        return f"최근 거래일 기준 코스피 {kospi:+.1f}%, 코스닥 {kosdaq:+.1f}%예요."
    except Exception:
        return "시장 지수 데이터를 가져오지 못했어요."
