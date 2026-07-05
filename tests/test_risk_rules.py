"""위험신호 룰셋 유닛테스트 — 룰별 양성/음성 케이스."""

from why_moved.engine.risk_rules import RiskContext, evaluate_rules, overall_level

TODAY = "20260705"


def _ids(signals):
    return {s.rule_id for s in signals}


def test_clean_stock_has_no_signals():
    ctx = RiskContext(
        today=TODAY,
        financials_by_year={"2025": {"equity": 1000, "capital": 100, "operating_income": 50}},
        managed=False,
        unfaithful=False,
        insider_net_sell_1m=0,
        volume_ratio=1.0,
    )
    signals, unavailable = evaluate_rules(ctx)
    assert signals == []
    assert overall_level(signals) == "안전"
    assert "R13" in unavailable  # 공매도는 v1 미지원 — 정직하게 보고


def test_r01_bad_audit_opinion():
    ctx = RiskContext(
        today=TODAY,
        disclosures_2y=[{"report_nm": "감사보고서제출 (의견거절)", "rcept_no": "1", "rcept_dt": "20260101"}],
    )
    signals, _ = evaluate_rules(ctx)
    assert "R01" in _ids(signals)
    assert overall_level(signals) == "위험"


def test_r02_capital_impairment():
    ctx = RiskContext(
        today=TODAY,
        financials_by_year={"2025": {"equity": 50, "capital": 100, "operating_income": 10}},
    )
    signals, _ = evaluate_rules(ctx)
    assert "R02" in _ids(signals)


def test_r02_healthy_equity_no_signal():
    ctx = RiskContext(
        today=TODAY,
        financials_by_year={"2025": {"equity": 500, "capital": 100, "operating_income": 10}},
    )
    signals, _ = evaluate_rules(ctx)
    assert "R02" not in _ids(signals)


def test_r03_managed_stock():
    signals, _ = evaluate_rules(RiskContext(today=TODAY, managed=True))
    assert "R03" in _ids(signals)


def test_r03_kind_unknown_falls_back_to_disclosure_titles():
    # KIND 확인 불가여도 공시 제목 점검이 수행되므로 '확인 불가'가 아니다
    signals, unavailable = evaluate_rules(RiskContext(today=TODAY, managed=None))
    assert "R03" not in _ids(signals)
    assert "R03" not in unavailable


def test_r03_detected_from_disclosure_title():
    disclosures = [{"report_nm": "관리종목지정", "rcept_no": "9", "rcept_dt": "20260601"}]
    signals, _ = evaluate_rules(RiskContext(today=TODAY, managed=None, disclosures_2y=disclosures))
    assert "R03" in _ids(signals)


def test_r05_detected_from_disclosure_title():
    disclosures = [{"report_nm": "불성실공시법인지정", "rcept_no": "8", "rcept_dt": "20260601"}]
    signals, unavailable = evaluate_rules(RiskContext(today=TODAY, unfaithful=None, disclosures_2y=disclosures))
    assert "R05" in _ids(signals)
    assert "R05" not in unavailable


def test_r06_cb_three_times():
    disclosures = [
        {"report_nm": "전환사채권발행결정", "rcept_no": str(i), "rcept_dt": "20260101"}
        for i in range(3)
    ]
    signals, _ = evaluate_rules(RiskContext(today=TODAY, disclosures_2y=disclosures))
    assert "R06" in _ids(signals)


def test_r06_two_cbs_not_enough():
    disclosures = [
        {"report_nm": "전환사채권발행결정", "rcept_no": str(i), "rcept_dt": "20260101"}
        for i in range(2)
    ]
    signals, _ = evaluate_rules(RiskContext(today=TODAY, disclosures_2y=disclosures))
    assert "R06" not in _ids(signals)


def test_r07_repeated_rights_offering():
    disclosures = [
        {"report_nm": "유상증자결정", "rcept_no": str(i), "rcept_dt": "20250901"}
        for i in range(2)
    ]
    signals, _ = evaluate_rules(RiskContext(today=TODAY, disclosures_2y=disclosures))
    assert "R07" in _ids(signals)


def test_old_disclosures_outside_window_ignored():
    disclosures = [
        {"report_nm": "유상증자결정", "rcept_no": "1", "rcept_dt": "20220101"},
        {"report_nm": "유상증자결정", "rcept_no": "2", "rcept_dt": "20220301"},
    ]
    signals, _ = evaluate_rules(RiskContext(today=TODAY, disclosures_2y=disclosures))
    assert "R07" not in _ids(signals)


def test_r09_insider_net_sell():
    signals, _ = evaluate_rules(RiskContext(today=TODAY, insider_net_sell_1m=10_000))
    assert "R09" in _ids(signals)


def test_r10_three_year_operating_loss():
    fin = {
        "2023": {"equity": 100, "capital": 10, "operating_income": -5},
        "2024": {"equity": 100, "capital": 10, "operating_income": -3},
        "2025": {"equity": 100, "capital": 10, "operating_income": -1},
    }
    signals, _ = evaluate_rules(RiskContext(today=TODAY, financials_by_year=fin))
    assert "R10" in _ids(signals)


def test_r10_profitable_year_breaks_streak():
    fin = {
        "2023": {"equity": 100, "capital": 10, "operating_income": -5},
        "2024": {"equity": 100, "capital": 10, "operating_income": 3},
        "2025": {"equity": 100, "capital": 10, "operating_income": -1},
    }
    signals, _ = evaluate_rules(RiskContext(today=TODAY, financials_by_year=fin))
    assert "R10" not in _ids(signals)


def test_r11_embezzlement():
    disclosures = [{"report_nm": "횡령ㆍ배임혐의발생", "rcept_no": "1", "rcept_dt": "20260601"}]
    signals, _ = evaluate_rules(RiskContext(today=TODAY, disclosures_2y=disclosures))
    assert "R11" in _ids(signals)
    assert overall_level(signals) == "위험"


def test_r14_volume_spike_without_disclosure():
    ctx = RiskContext(today=TODAY, volume_ratio=7.0, has_recent_disclosure=False)
    signals, _ = evaluate_rules(ctx)
    assert "R14" in _ids(signals)


def test_r14_volume_spike_with_disclosure_no_signal():
    ctx = RiskContext(today=TODAY, volume_ratio=7.0, has_recent_disclosure=True)
    signals, _ = evaluate_rules(ctx)
    assert "R14" not in _ids(signals)


def test_r15_delisting():
    disclosures = [{"report_nm": "상장적격성 실질심사 사유 발생", "rcept_no": "1", "rcept_dt": "20260601"}]
    signals, _ = evaluate_rules(RiskContext(today=TODAY, disclosures_2y=disclosures))
    assert "R15" in _ids(signals)


def test_overall_level_ordering():
    ctx = RiskContext(
        today=TODAY,
        disclosures_2y=[{"report_nm": "소송등의제기", "rcept_no": "1", "rcept_dt": "20260620"}],
    )
    signals, _ = evaluate_rules(ctx)
    assert overall_level(signals) == "주의"
