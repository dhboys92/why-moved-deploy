"""[Hero #1] why_moved — 종목 급등락 원인 설명 (설계 §2.1, v1.1: 뉴스·업종·장중·시각화)."""

import asyncio
from datetime import datetime, timedelta

from why_moved.common.envelope import dart_viewer_url, envelope, source
from why_moved.context import AppContext
from why_moved.engine import charts
from why_moved.engine.disclosure_templates import match_template
from why_moved.engine.glossary import attach_terms
from why_moved.engine.textviz import sparkline

_SIGNIFICANT_FLOW = 1_000_000_000  # 순매수 10억원 이상이면 수급 요인으로 언급
_SECTOR_MOVE_THRESHOLD = 1.5       # 동종업계 평균 등락률(%) 요인 임계


async def why_moved(ctx: AppContext, query: str, date: str | None = None) -> dict:
    corp = await ctx.resolver.resolve(query)
    price = await ctx.market.get_price_move(corp.stock_code, date)
    day = price["date"]

    factors: list[dict] = []
    sources = [source("네이버 금융 시세 (KRX 데이터)", f"https://finance.naver.com/item/main.naver?code={corp.stock_code}", day)]

    # 1) 공시 요인: D-3 ~ 당일
    bgn = (datetime.strptime(day, "%Y%m%d") - timedelta(days=3)).strftime("%Y%m%d")
    disclosures = await ctx.dart.search_disclosures(corp_code=corp.corp_code, bgn_de=bgn, end_de=day)
    important = sorted(
        (d for d in disclosures if match_template(d.get("report_nm", "")).importance >= 3),
        key=lambda d: match_template(d["report_nm"]).importance,
        reverse=True,
    )
    for d in important[:2]:
        template = match_template(d["report_nm"])
        url = dart_viewer_url(d["rcept_no"])
        factors.append({
            "type": "disclosure",
            "summary": f"[{d['rcept_dt']}] {d['report_nm']} — {template.what_happened} {template.so_what}",
            "source_url": url,
        })
        sources.append(source(d["report_nm"], url, d["rcept_dt"]))

    # 2) 뉴스 요인 (v1.1): 최근 헤드라인 — 제목만 인용, 원문 링크 첨부
    news = await ctx.market.get_stock_news(corp.stock_code, limit=3)
    if news:
        headlines = " / ".join(f"“{n['title']}” ({n['press']})" for n in news[:2])
        factors.append({
            "type": "news",
            "summary": f"관련 뉴스: {headlines}",
            "source_url": news[0]["url"],
        })
        for n in news[:2]:
            if n["url"]:
                sources.append(source(f"뉴스: {n['title'][:30]}", n["url"], n["datetime"][:8]))

    # 3) 수급 요인 (기관·외국인 순매매, 추정대금)
    flow = None
    try:
        flow = await ctx.market.get_investor_flow(corp.stock_code, day)
        dominant = max(("외국인", "기관"), key=lambda k: abs(flow[k]))
        if abs(flow[dominant]) >= _SIGNIFICANT_FLOW:
            direction = "순매수" if flow[dominant] > 0 else "순매도"
            amount = abs(flow[dominant]) / 100_000_000
            factors.append({
                "type": "flow",
                "summary": f"{dominant}이 약 {amount:,.0f}억원(추정) {direction}했어요.",
                "source_url": f"https://finance.naver.com/item/frgn.naver?code={corp.stock_code}",
            })
    except Exception:
        pass  # 수급 조회 실패는 원인 후보 하나가 빠질 뿐, 응답은 계속한다

    # 4) 업종 요인 (v1.1): 동종업계가 같은 방향으로 움직였는지
    industry = await ctx.market.get_industry_compare(corp.stock_code)
    if industry and industry.get("peers"):
        peer_changes = [p["change_pct"] for p in industry["peers"] if p.get("change_pct") is not None]
        if peer_changes:
            avg = sum(peer_changes) / len(peer_changes)
            if abs(avg) >= _SECTOR_MOVE_THRESHOLD and avg * price["change_pct"] > 0:
                names = ", ".join(p["name"] for p in industry["peers"][:3])
                updown = "올랐어요" if avg > 0 else "내렸어요"
                factors.append({
                    "type": "sector",
                    "summary": f"동종업계({names} 등)가 평균 {avg:+.1f}% 함께 {updown}. 업종 전체 흐름일 수 있어요.",
                    "source_url": f"https://m.stock.naver.com/domestic/stock/{corp.stock_code}",
                })

    # 5) 시장 요인: 지수가 같은 방향으로 1% 이상
    market = await ctx.market.get_market_of(corp.stock_code)
    index_change = await ctx.market.get_index_change(market, day)
    if abs(index_change) >= 1.0 and index_change * price["change_pct"] > 0:
        updown = "올랐" if index_change > 0 else "내렸"
        factors.append({
            "type": "market",
            "summary": f"{market} 지수 자체가 {index_change:+.1f}% {updown}어요. 시장 전체 흐름의 영향일 수 있어요.",
            "source_url": "https://finance.naver.com/sise/",
        })

    # 장중 시세 (v1.1): 장이 열려 있으면 지연 현재가 병기
    intraday = await ctx.market.get_intraday_quote(corp.stock_code)
    intraday_note = None
    if intraday and intraday.get("market_status") == "OPEN":
        intraday_note = (
            f"지금 장중에는 {intraday['price']:,}원({intraday['change_pct']:+.1f}%)에 "
            "거래되고 있어요 (지연 시세)."
        )

    # 스파크라인 + 가격×공시 차트 (v1.1) — 마커 번호와 chart_events가 1:1 대응
    series, spark, chart_url, chart_events = [], "", None, []
    try:
        series = await ctx.market.get_price_series(corp.stock_code, days=60)
        closes = [s["close"] for s in series]
        spark = sparkline(closes[-30:])
        chart_url, chart_events = await _price_chart(ctx, corp, day, series, bgn_days=60)
    except Exception:
        pass  # 차트는 부가 요소 — 실패해도 응답은 계속

    explanation = _build_explanation(corp.name, price, factors, spark, intraday_note)
    payload = {
        "stock": {"name": corp.name, "code": corp.stock_code, "market": market},
        "price_move": price,
        "intraday": intraday,
        "explanation": explanation,
        "factors": factors,
        "trend_sparkline_30d": spark or None,
        "chart_url": chart_url,
        "chart_events": chart_events or None,
        "chart_hint": (
            "chart_url은 최근 60일 주가 차트이며, 차트 위 번호 마커는 chart_events의 no와 1:1 대응합니다. "
            "이미지와 함께 번호별 공시 목록을 사용자에게 보여주세요."
            if chart_url else None
        ),
        "investor_flow": (
            {"date": flow["date"], "외국인": flow["외국인"], "기관": flow["기관"],
             "note": "순매매 주식수 × 종가 추정대금이에요. 개인 수급은 제공되지 않아요."}
            if flow else None
        ),
        "terms": attach_terms(explanation + " ".join(f["summary"] for f in factors)),
        "data_note": f"{day} 종가 기준 데이터예요 (뉴스·장중 시세 제외).",
    }
    return envelope(payload, sources)


async def _price_chart(
    ctx: AppContext, corp, day: str, series: list[dict], bgn_days: int
) -> tuple[str | None, list[dict]]:
    """최근 60일 가격 + 번호 공시 마커 차트. (chart_url, chart_events) 반환.

    chart_events의 no가 차트 위 번호 마커와 1:1 대응한다 — 마커가 어떤 공시인지
    원문 링크까지 연결하기 위한 범례 데이터.
    """
    bgn = (datetime.strptime(day, "%Y%m%d") - timedelta(days=bgn_days)).strftime("%Y%m%d")
    window_disclosures = await ctx.dart.search_disclosures(
        corp_code=corp.corp_code, bgn_de=bgn, end_de=day
    )
    important = [
        d for d in window_disclosures
        if match_template(d.get("report_nm", "")).importance >= 3
    ]
    important.sort(key=lambda d: d.get("rcept_dt", ""))  # 시간순 번호
    events = [
        {
            "no": i + 1,
            "date": d["rcept_dt"],
            "type": match_template(d["report_nm"]).type_name,
            "title": d["report_nm"].strip(),
            "url": dart_viewer_url(d["rcept_no"]),
        }
        for i, d in enumerate(important[:9])
    ]

    key = ("why_moved", corp.stock_code, day)
    cached = ctx.charts.exists(*key)
    if cached:
        return ctx.chart_url(cached), events

    markers = [{"rcept_dt": e["date"], "type_name": e["type"], "no": e["no"]} for e in events]
    png = await asyncio.to_thread(charts.price_with_disclosures, corp.name, series, markers)
    return ctx.chart_url(ctx.charts.save(png, *key)), events


def _build_explanation(
    name: str, price: dict, factors: list[dict], spark: str, intraday_note: str | None
) -> str:
    """3줄 요약. 환각 금지 — 요인이 없으면 없다고 말한다 (설계 §2.1)."""
    change = price["change_pct"]
    direction = "올랐어요" if change > 0 else ("내렸어요" if change < 0 else "보합이에요")
    first = f"{name}은(는) {price['date'][:4]}-{price['date'][4:6]}-{price['date'][6:]} 기준 {abs(change):.1f}% {direction}."
    if intraday_note:
        first += f" {intraday_note}"
    trend = f"\n최근 30일 흐름: {spark}" if spark else ""

    if not factors:
        volume_note = ""
        if price.get("volume_ratio") and price["volume_ratio"] >= 3:
            volume_note = f" 다만 거래량이 평소의 {price['volume_ratio']:.0f}배로 뛰어 소문·테마성 움직임일 가능성은 있어요."
        return (
            f"{first} 공시·뉴스·수급·시장 어디에서도 뚜렷한 공개 요인을 찾지 못했어요."
            f"{volume_note} 공개된 정보가 없는 움직임은 더 조심스럽게 보는 것이 좋아요.{trend}"
        )

    reasons = " ".join(f"{i + 1}) {f['summary']}" for i, f in enumerate(factors[:3]))
    return f"{first} 확인된 공개 요인: {reasons}{trend}"
