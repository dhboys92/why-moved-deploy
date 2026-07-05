"""[신규 콘텐츠] disclosure_quiz — 오늘 공시로 만드는 공시 문해력 퀴즈.

실제 최근 거래일 공시 1건으로 3지선다를 낸다. LLM이 대화형으로 진행하며
정답은 사용자가 고른 뒤에만 공개하도록 quiz_hint로 안내한다.
"""

from datetime import datetime

from why_moved.common.envelope import dart_viewer_url, envelope, source
from why_moved.context import AppContext
from why_moved.engine.disclosure_templates import match_template
from why_moved.engine.glossary import attach_terms
from why_moved.engine.quiz import build_quiz


async def disclosure_quiz(ctx: AppContext, topic: str = "") -> dict:
    day = datetime.now().strftime("%Y%m%d")
    disclosures = await ctx.dart.search_disclosures(bgn_de=day, end_de=day, page_count=100)

    # 휴장일·이른 아침엔 최근 거래일 공시로 폴백 (daily_digest와 동일 전략)
    if not _quizzable(disclosures, topic):
        try:
            trading_day = await ctx.market.latest_trading_day()
        except Exception:
            trading_day = None
        if trading_day and trading_day != day:
            day = trading_day
            disclosures = await ctx.dart.search_disclosures(bgn_de=day, end_de=day, page_count=100)

    pool = _quizzable(disclosures, topic)
    if not pool:
        return envelope({
            "error_note": (
                f"'{topic}' 유형의 최근 공시를 찾지 못했어요. topic 없이 다시 시도해 보세요."
                if topic else "퀴즈로 낼 만한 최근 공시가 아직 없어요. 잠시 후 다시 시도해 주세요."
            ),
        }, [source("DART 전자공시", "https://dart.fss.or.kr", day)])

    # seed = 날짜+접수번호 — 같은 날 같은 공시면 같은 퀴즈 (결정적)
    target = pool[0]
    quiz = build_quiz(target.get("corp_name", "어느 회사"), target["report_nm"], f"{day}:{target['rcept_no']}")
    template = match_template(target["report_nm"])
    url = dart_viewer_url(target["rcept_no"])

    payload = {
        "question": quiz.question,
        "choices": {"A": quiz.choices[0], "B": quiz.choices[1], "C": quiz.choices[2]},
        "correct_choice": quiz.correct_choice,
        "explanation": quiz.explanation,
        "disclosure": {
            "company": target.get("corp_name", ""),
            "title": target["report_nm"].strip(),
            "date": target["rcept_dt"],
            "type": template.type_name,
            "url": url,
        },
        "terms": attach_terms(template.so_what + template.what_happened),
        "quiz_hint": (
            "대화형 퀴즈로 진행하세요: ① 문제와 A/B/C 선지만 먼저 보여주고 정답은 절대 미리 공개하지 마세요. "
            "② 사용자가 고르면 채점하고 explanation으로 해설하세요. "
            "③ 이 문제가 '오늘 실제로 나온 공시'라는 점과 원문 링크를 알려주면 흥미가 높아져요. "
            "④ 끝나면 '한 문제 더?'를 제안하세요 (topic으로 다른 유형 지정 가능)."
        ),
        "suggested_questions": [
            f"{target.get('corp_name', '이 회사')}의 이 공시를 자세히 통역해줄래?",
            "한 문제 더 내줘!",
        ],
    }
    return envelope(payload, [source(target["report_nm"], url, target["rcept_dt"])])


def _quizzable(disclosures: list[dict], topic: str) -> list[dict]:
    """템플릿이 매칭되는(중요도≥3) 공시만 퀴즈 풀로. topic이 있으면 유형 필터."""
    pool = []
    for d in disclosures:
        template = match_template(d.get("report_nm", ""))
        if template.importance < 3:
            continue
        if topic and topic not in template.type_name and topic not in d.get("report_nm", ""):
            continue
        pool.append(d)
    return pool
