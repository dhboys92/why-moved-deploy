"""stock_health — 종목 건강진단 (설계 §2.4, v1.1: 레이더 차트·점수바·업종 비교)."""

import asyncio
from dataclasses import asdict
from datetime import datetime

from why_moved.common.envelope import envelope, source
from why_moved.context import AppContext
from why_moved.engine import charts
from why_moved.engine.financial_extract import extract_financials
from why_moved.engine.health_score import (
    FinancialSnapshot,
    axis_scores,
    narrative,
    overall_grade,
    run_checks,
)
from why_moved.engine.textviz import score_bar

_AXIS_NAMES = {"value": "가치", "growth": "성장", "profitability": "수익성",
               "stability": "건전성", "dividend": "배당"}

_ANNUAL_REPORT = "11011"


async def stock_health(ctx: AppContext, query: str) -> dict:
    corp = await ctx.resolver.resolve(query)

    fiscal_year, fin = await _latest_annual(ctx, corp.corp_code)
    valuation = await ctx.market.get_fundamental(corp.stock_code)

    snapshot = FinancialSnapshot(
        revenue=fin.get("revenue"),
        operating_income=fin.get("operating_income"),
        net_income=fin.get("net_income"),
        equity=fin.get("equity"),
        liabilities=fin.get("liabilities"),
        prev_revenue=fin.get("prev_revenue"),
        prev_operating_income=fin.get("prev_operating_income"),
        per=valuation.get("per"),
        pbr=valuation.get("pbr"),
        div_yield=valuation.get("div"),
    )
    checks = run_checks(snapshot)
    scores = axis_scores(snapshot)
    grade = overall_grade(scores)

    # v1.1: 레이더 차트 + 유니코드 점수바 + 동종업계 비교
    chart_url = None
    try:
        key = ("stock_health", corp.stock_code, fiscal_year, grade)
        chart_id = ctx.charts.exists(*key)
        if chart_id is None:
            png = await asyncio.to_thread(charts.health_radar, corp.name, scores, grade)
            chart_id = ctx.charts.save(png, *key)
        chart_url = ctx.chart_url(chart_id)
    except Exception:
        pass

    scores_visual = "\n".join(
        f"{_AXIS_NAMES[k]:<3} {score_bar(v)}" for k, v in scores.items()
    )

    peers_note = None
    industry = await ctx.market.get_industry_compare(corp.stock_code)
    if industry and industry.get("peers"):
        names = ", ".join(p["name"] for p in industry["peers"][:4])
        peers_note = f"같은 업종 비교 대상: {names}. 점수는 업종 특성에 따라 달리 볼 필요가 있어요."

    payload = {
        "stock": {"name": corp.name, "code": corp.stock_code},
        "scores": scores,
        "scores_visual": scores_visual,
        "grade": grade,
        "narrative": narrative(checks, grade),
        "chart_url": chart_url,
        "chart_hint": "chart_url은 5축 레이더 차트 이미지입니다. 사용자에게 보여주세요." if chart_url else None,
        "industry_note": peers_note,
        "checks": [asdict(c) for c in checks],
        "data_basis": {
            "fiscal_period": f"{fiscal_year}년 사업보고서" if fiscal_year else "재무 데이터 없음",
            "valuation_note": "PER·PBR·배당수익률은 네이버 금융 최근 거래일 기준이에요.",
        },
    }
    sources = [
        source("DART 재무제표", "https://dart.fss.or.kr", fiscal_year or ""),
        source("네이버 금융 밸류에이션", f"https://m.stock.naver.com/domestic/stock/{corp.stock_code}"),
    ]
    return envelope(payload, sources)


async def _latest_annual(ctx: AppContext, corp_code: str) -> tuple[str, dict]:
    """가장 최근에 제출된 사업보고서 재무를 찾는다 (당해→전년 순서로 시도)."""
    current_year = datetime.now().year
    for year in (current_year - 1, current_year - 2):
        try:
            rows = await ctx.dart.get_financials(corp_code, str(year), _ANNUAL_REPORT)
        except Exception:
            continue
        if rows:
            return str(year), extract_financials(rows)
    return "", {}
