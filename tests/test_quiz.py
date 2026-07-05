"""공시 퀴즈 엔진 + 신규 콘텐츠 tool 테스트."""

from why_moved.common.envelope import contains_forbidden_phrase
from why_moved.engine.quiz import build_quiz

DISCLOSURE = {
    "rcept_no": "20260703000001",
    "corp_name": "테스트전자",
    "report_nm": "주요사항보고서(유상증자결정)",
    "rcept_dt": "20260703",
}


class TestQuizEngine:
    def test_deterministic_same_seed(self):
        q1 = build_quiz("테스트전자", "유상증자결정", "seed1")
        q2 = build_quiz("테스트전자", "유상증자결정", "seed1")
        assert q1 == q2

    def test_different_seed_can_shuffle(self):
        quizzes = {build_quiz("A", "유상증자결정", f"s{i}").correct_choice for i in range(20)}
        assert len(quizzes) > 1  # 정답 위치가 seed에 따라 달라진다

    def test_three_unique_choices_contain_answer(self):
        q = build_quiz("테스트전자", "전환사채권발행결정", "x")
        assert len(q.choices) == 3
        assert len(set(q.choices)) == 3
        assert q.choices[q.correct_index]  # 정답 인덱스 유효
        assert q.correct_choice in ("A", "B", "C")

    def test_no_recommendation_phrases(self):
        q = build_quiz("테스트전자", "유상증자결정", "y")
        combined = q.question + " ".join(q.choices) + q.explanation
        assert contains_forbidden_phrase(combined) is None


class TestDisclosureQuizTool:
    async def test_quiz_from_today(self, mock_ctx):
        from why_moved.tools.disclosure_quiz_tool import disclosure_quiz

        mock_ctx.dart.search_disclosures.return_value = [DISCLOSURE]
        result = await disclosure_quiz(mock_ctx)
        assert "유상증자" in result["question"]
        assert set(result["choices"]) == {"A", "B", "C"}
        assert result["correct_choice"] in result["choices"]
        assert "정답을 절대 미리 공개하지" in result["quiz_hint"] or "미리 공개" in result["quiz_hint"]
        assert result["disclosure"]["url"].startswith("https://dart.fss.or.kr")

    async def test_topic_filter_no_match(self, mock_ctx):
        from why_moved.tools.disclosure_quiz_tool import disclosure_quiz

        mock_ctx.dart.search_disclosures.return_value = [DISCLOSURE]
        result = await disclosure_quiz(mock_ctx, topic="합병")
        assert "찾지 못했어요" in result["error_note"]

    async def test_deterministic_same_day(self, mock_ctx):
        from why_moved.tools.disclosure_quiz_tool import disclosure_quiz

        mock_ctx.dart.search_disclosures.return_value = [DISCLOSURE]
        r1 = await disclosure_quiz(mock_ctx)
        r2 = await disclosure_quiz(mock_ctx)
        assert r1["choices"] == r2["choices"]
        assert r1["correct_choice"] == r2["correct_choice"]


class TestTodayMoversTool:
    async def test_movers_with_reasons(self, mock_ctx):
        from why_moved.tools.today_movers_tool import today_movers

        mock_ctx.market.get_top_movers.return_value = [
            {"name": "테스트전자", "code": "005930", "market": "KOSPI", "change_pct": 15.2, "close": 10000},
        ]
        mock_ctx.dart.search_disclosures.return_value = [DISCLOSURE]
        mock_ctx.market.get_stock_news.return_value = [
            {"title": "대박 수주", "press": "테스트일보", "datetime": "202607030900", "url": "https://n.news.naver.com/1"},
        ]
        result = await today_movers(mock_ctx)
        mover = result["movers"][0]
        assert mover["change_pct"] == 15.2
        types = {r["type"] for r in mover["reasons"]}
        assert types == {"disclosure", "news"}
        assert result["chart_url"]
        assert "급등" in result["summary"]

    async def test_unresolvable_stock_graceful(self, mock_ctx):
        from why_moved.common.errors import StockNotFoundError
        from why_moved.tools.today_movers_tool import today_movers

        mock_ctx.market.get_top_movers.return_value = [
            {"name": "신규상장주", "code": "999999", "market": "KOSDAQ", "change_pct": 30.0, "close": 5000},
        ]
        mock_ctx.resolver.resolve.side_effect = StockNotFoundError("999999")
        mock_ctx.market.get_stock_news.return_value = []
        result = await today_movers(mock_ctx)
        assert result["movers"][0]["reasons"] == []  # 크래시 없이 등락률만
