"""explain_disclosure — 공시 쉬운말 통역 (설계 §2.3)."""

from datetime import timedelta

from why_moved.common.clock import now_kst
from why_moved.common.envelope import dart_viewer_url, envelope, source
from why_moved.common.errors import DisclosureNotFoundError
from why_moved.context import AppContext
from why_moved.engine.disclosure_templates import match_template
from why_moved.engine.glossary import attach_terms


async def explain_disclosure(
    ctx: AppContext,
    company: str,
    keyword: str = "",
    rcept_no: str = "",
) -> dict:
    corp = await ctx.resolver.resolve(company)
    today = now_kst().strftime("%Y%m%d")
    bgn = (now_kst() - timedelta(days=90)).strftime("%Y%m%d")
    disclosures = await ctx.dart.search_disclosures(
        corp_code=corp.corp_code, bgn_de=bgn, end_de=today
    )
    if not disclosures:
        raise DisclosureNotFoundError(f"{corp.name}의 최근 90일 공시 없음")

    target = _pick(disclosures, keyword, rcept_no)
    if target is None:
        raise DisclosureNotFoundError(
            f"{corp.name}의 최근 공시 중 조건에 맞는 것을 찾지 못했어요 (keyword='{keyword}')"
        )

    template = match_template(target["report_nm"])
    url = dart_viewer_url(target["rcept_no"])
    combined_text = template.what_happened + template.so_what + template.why_care + target["report_nm"]

    payload = {
        "disclosure": {
            "title": target["report_nm"],
            "company": corp.name,
            "date": target["rcept_dt"],
            "type": template.type_name,
            "url": url,
        },
        "what_happened": template.what_happened,
        "so_what": template.so_what,
        "why_care": template.why_care,
        "terms": attach_terms(combined_text),
        "recent_disclosures": [
            {"title": d["report_nm"], "date": d["rcept_dt"], "url": dart_viewer_url(d["rcept_no"])}
            for d in disclosures[:5]
        ],
    }
    return envelope(payload, [source(target["report_nm"], url, target["rcept_dt"])])


def _pick(disclosures: list[dict], keyword: str, rcept_no: str) -> dict | None:
    if rcept_no:
        return next((d for d in disclosures if d.get("rcept_no") == rcept_no), None)
    if keyword:
        return next((d for d in disclosures if keyword in d.get("report_nm", "")), None)
    return disclosures[0]  # 최신 공시
