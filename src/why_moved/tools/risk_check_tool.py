"""[Hero #2, 공익] risk_check — 위험신호 진단 (설계 §2.2, v1.1: 요약 카드 이미지)."""

import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta

from why_moved.common.envelope import dart_viewer_url, envelope, source
from why_moved.context import AppContext
from why_moved.engine import charts
from why_moved.engine.financial_extract import extract_financials
from why_moved.engine.risk_rules import (
    TOTAL_RULES,
    RiskContext,
    evaluate_rules,
    overall_level,
)

_ANNUAL_REPORT = "11011"


async def risk_check(ctx: AppContext, query: str) -> dict:
    corp = await ctx.resolver.resolve(query)
    today = datetime.now().strftime("%Y%m%d")
    bgn_2y = (datetime.now() - timedelta(days=730)).strftime("%Y%m%d")

    disclosures = await ctx.dart.search_disclosures(
        corp_code=corp.corp_code, bgn_de=bgn_2y, end_de=today, page_count=100
    )

    financials_by_year = await _collect_financials(ctx, corp.corp_code)
    managed = await ctx.kind.is_managed(corp.stock_code)
    unfaithful = await ctx.kind.is_unfaithful(corp.stock_code)
    insider_net_sell = await _insider_net_sell_1m(ctx, corp.corp_code, today)

    volume_ratio = None
    has_recent = False
    try:
        price = await ctx.market.get_price_move(corp.stock_code)
        volume_ratio = price.get("volume_ratio")
        recent_cut = (datetime.now() - timedelta(days=3)).strftime("%Y%m%d")
        has_recent = any(d.get("rcept_dt", "") >= recent_cut for d in disclosures)
    except Exception:
        pass  # 시세 실패 시 R14만 확인 불가로 처리

    rule_ctx = RiskContext(
        today=today,
        disclosures_2y=disclosures,
        financials_by_year=financials_by_year,
        managed=managed,
        unfaithful=unfaithful,
        insider_net_sell_1m=insider_net_sell,
        short_balance_ratio_change=None,  # 공매도 잔고는 v1.1에서 지원
        volume_ratio=volume_ratio,
        has_recent_disclosure=has_recent,
    )
    signals, unavailable = evaluate_rules(rule_ctx)
    level = overall_level(signals)

    sources = [source("DART 전자공시", "https://dart.fss.or.kr", today)]
    for s in signals:
        if s.rcept_no:
            sources.append(source(s.title, dart_viewer_url(s.rcept_no)))

    # v1.1: 위험신호 요약 카드 이미지
    chart_url = None
    try:
        key = ("risk_check", corp.stock_code, today, level, str(len(signals)))
        chart_id = ctx.charts.exists(*key)
        if chart_id is None:
            png = await asyncio.to_thread(
                charts.risk_card, corp.name, level,
                [asdict(s) for s in signals], TOTAL_RULES,
            )
            chart_id = ctx.charts.save(png, *key)
        chart_url = ctx.chart_url(chart_id)
    except Exception:
        pass

    payload = {
        "stock": {"name": corp.name, "code": corp.stock_code},
        "risk_level": level,
        "summary": _summary(corp.name, level, signals),
        "signals": [
            {**asdict(s), "source_url": dart_viewer_url(s.rcept_no) if s.rcept_no else ""}
            for s in signals
        ],
        "checked_rules": TOTAL_RULES,
        "unavailable_rules": unavailable,
        "chart_url": chart_url,
        "chart_hint": "chart_url은 위험 진단 요약 카드 이미지입니다. 사용자에게 보여주세요." if chart_url else None,
    }
    return envelope(payload, sources)


async def _collect_financials(ctx: AppContext, corp_code: str) -> dict[str, dict]:
    """최근 3개 사업연도의 자본총계·자본금·영업이익."""
    result: dict[str, dict] = {}
    current_year = datetime.now().year
    for year in range(current_year - 3, current_year):
        try:
            rows = await ctx.dart.get_financials(corp_code, str(year), _ANNUAL_REPORT)
        except Exception:
            continue
        if not rows:
            continue
        f = extract_financials(rows)
        result[str(year)] = {
            "equity": f["equity"],
            "capital": f["capital"],
            "operating_income": f["operating_income"],
        }
    return result


async def _insider_net_sell_1m(ctx: AppContext, corp_code: str, today: str) -> int | None:
    """최근 1개월 임원·주요주주 순매도 주식수 (양수=순매도)."""
    try:
        rows = await ctx.dart.get_executive_holdings(corp_code)
    except Exception:
        return None
    cut = (datetime.strptime(today, "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")
    net = 0
    seen = False
    for row in rows:
        if (row.get("rcept_dt", "") or "") < cut:
            continue
        try:
            change = float(str(row.get("sp_stock_lmp_irds_cnt", "0")).replace(",", ""))
        except ValueError:
            continue
        net -= int(change)  # 증감이 음수(매도)면 순매도에 가산
        seen = True
    return net if seen else 0


def _summary(name: str, level: str, signals) -> str:
    if not signals:
        return f"{name}에서 점검한 위험신호가 발견되지 않았어요. 다만 위험신호가 없다는 것이 수익을 보장하지는 않아요."
    titles = ", ".join(s.title for s in signals[:3])
    return f"🚨 {name}에서 위험신호 {len(signals)}개를 발견했어요 (종합: {level}). 주요 신호: {titles}. 원문 링크에서 꼭 직접 확인해 보세요."
