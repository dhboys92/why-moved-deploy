"""위험신호 룰셋 v1 — 15개 룰 (설계 §2.2).

룰은 순수 함수로 구현해 테스트 가능하게 하고, 데이터 수집은 tool 레이어가
RiskContext로 조립해 전달한다. 데이터 확인 불가(None)는 신호를 만들지 않되
unavailable_rules에 기록한다 (크래시 금지, 정직한 응답).
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

SEVERITY_ORDER = {"주의": 1, "경고": 2, "위험": 3}


@dataclass
class RiskContext:
    """룰 평가에 필요한 사전 수집 데이터."""

    today: str                                   # YYYYMMDD
    disclosures_2y: list[dict] = field(default_factory=list)   # {report_nm, rcept_no, rcept_dt}
    financials_by_year: dict[str, dict] = field(default_factory=dict)
    # {year: {"equity": 자본총계, "capital": 자본금, "operating_income": 영업이익}}
    managed: bool | None = None                  # 관리종목 (KIND)
    unfaithful: bool | None = None               # 불성실공시법인 (KIND)
    insider_net_sell_1m: int | None = None       # 최근 1개월 내부자 순매도 주식수 (양수=순매도)
    short_balance_ratio_change: float | None = None  # 공매도 잔고 비중 변화(%p)
    volume_ratio: float | None = None            # 당일 거래량 / 20일 평균
    has_recent_disclosure: bool = False          # 최근 3일 내 공시 존재 여부


@dataclass(frozen=True)
class RiskSignal:
    rule_id: str
    severity: str        # 주의 | 경고 | 위험
    title: str
    easy_explanation: str
    evidence: str = ""   # 근거 요약 (건수·수치)
    rcept_no: str = ""   # 근거 공시 (있으면)


def _within(disclosure: dict, today: str, days: int) -> bool:
    try:
        dt = datetime.strptime(disclosure.get("rcept_dt", ""), "%Y%m%d")
    except ValueError:
        return False
    return dt >= datetime.strptime(today, "%Y%m%d") - timedelta(days=days)


def _find(disclosures: list[dict], keywords: tuple[str, ...], today: str, days: int) -> list[dict]:
    return [
        d for d in disclosures
        if any(k in d.get("report_nm", "") for k in keywords) and _within(d, today, days)
    ]


def evaluate_rules(ctx: RiskContext) -> tuple[list[RiskSignal], list[str]]:
    """(발견 신호, 확인 불가 룰 ID) 를 반환한다."""
    signals: list[RiskSignal] = []
    unavailable: list[str] = []

    # R01 감사의견 비적정 — 공시 제목 기반 탐지
    bad_audit = _find(ctx.disclosures_2y, ("의견거절", "한정의견", "부적정"), ctx.today, 730)
    if bad_audit:
        d = bad_audit[0]
        signals.append(RiskSignal(
            "R01", "위험", "감사의견 비적정 이력",
            "회계법인이 재무제표를 정상으로 인정하지 않았어요. 상장폐지로 이어질 수 있는 심각한 신호예요.",
            f"관련 공시 {len(bad_audit)}건", d.get("rcept_no", ""),
        ))

    # R02 자본잠식 — 최신 연도 자본총계 < 자본금
    if ctx.financials_by_year:
        latest = ctx.financials_by_year[max(ctx.financials_by_year)]
        equity, capital = latest.get("equity"), latest.get("capital")
        if equity is not None and capital is not None and capital > 0 and equity < capital:
            rate = round((1 - equity / capital) * 100)
            signals.append(RiskSignal(
                "R02", "위험", "자본잠식",
                "쌓인 손실로 자기자본이 납입자본금보다 줄어든 상태예요. 심하면 상장폐지 사유가 돼요.",
                f"잠식률 약 {rate}%",
            ))
    else:
        unavailable.append("R02")

    # R03 관리종목 — KIND 목록 + 공시 제목 폴백 (둘 중 하나만 잡혀도 신호)
    managed_disclosures = _find(ctx.disclosures_2y, ("관리종목지정",), ctx.today, 365)
    if ctx.managed or managed_disclosures:
        signals.append(RiskSignal(
            "R03", "위험", "관리종목 지정",
            "거래소가 '투자에 특히 주의하라'고 지정한 종목이에요. 상장폐지 위험이 있어요.",
            rcept_no=managed_disclosures[0].get("rcept_no", "") if managed_disclosures else "",
        ))
    elif ctx.managed is None and not managed_disclosures:
        pass  # KIND 확인 불가여도 공시 제목 점검은 수행됨 — 확인 불가로 표기하지 않음

    # R04 거래정지 이력 (1년) — 공시 제목 기반
    halts = _find(ctx.disclosures_2y, ("매매거래정지",), ctx.today, 365)
    if halts:
        signals.append(RiskSignal(
            "R04", "경고", "거래정지 이력 (1년 내)",
            "최근 1년 안에 매매가 정지된 적이 있어요. 이유를 꼭 확인해 보세요.",
            f"{len(halts)}건", halts[0].get("rcept_no", ""),
        ))

    # R05 불성실공시법인 (1년) — KIND 목록 + 공시 제목 폴백
    unfaith_disclosures = _find(ctx.disclosures_2y, ("불성실공시법인지정",), ctx.today, 365)
    if ctx.unfaithful or unfaith_disclosures:
        signals.append(RiskSignal(
            "R05", "경고", "불성실공시법인 지정",
            "공시 의무를 지키지 않아 거래소의 지정을 받은 회사예요.",
            rcept_no=unfaith_disclosures[0].get("rcept_no", "") if unfaith_disclosures else "",
        ))

    # R06 CB/BW 발행 3회+ (2년)
    cb = _find(ctx.disclosures_2y, ("전환사채", "신주인수권부사채", "교환사채"), ctx.today, 730)
    if len(cb) >= 3:
        signals.append(RiskSignal(
            "R06", "경고", "전환사채(CB) 등 반복 발행",
            "주식으로 바뀔 수 있는 빚을 자주 발행했어요. 기존 주주의 지분 가치가 희석될 수 있어요.",
            f"2년 내 {len(cb)}건", cb[0].get("rcept_no", ""),
        ))

    # R07 유상증자 반복 (2년 내 2회+)
    rights = _find(ctx.disclosures_2y, ("유상증자",), ctx.today, 730)
    if len(rights) >= 2:
        signals.append(RiskSignal(
            "R07", "주의", "유상증자 반복",
            "새 주식을 팔아 자금을 자주 조달했어요. 그만큼 기존 주주 지분이 희석돼 왔어요.",
            f"2년 내 {len(rights)}건", rights[0].get("rcept_no", ""),
        ))

    # R08 최대주주 변경 2회+ (2년)
    owner_changes = _find(ctx.disclosures_2y, ("최대주주변경", "최대주주 변경"), ctx.today, 730)
    if len(owner_changes) >= 2:
        signals.append(RiskSignal(
            "R08", "경고", "최대주주 잦은 변경",
            "회사의 주인이 2년 사이 여러 번 바뀌었어요. 경영이 불안정하다는 신호일 수 있어요.",
            f"2년 내 {len(owner_changes)}건", owner_changes[0].get("rcept_no", ""),
        ))

    # R09 임원 대량 매도 (1개월)
    if ctx.insider_net_sell_1m is None:
        unavailable.append("R09")
    elif ctx.insider_net_sell_1m > 0:
        signals.append(RiskSignal(
            "R09", "주의", "내부자 순매도 (1개월)",
            "회사 사정을 잘 아는 임원·주요주주가 최근 주식을 순매도했어요.",
            f"순매도 {ctx.insider_net_sell_1m:,}주",
        ))

    # R10 3년 연속 영업적자
    years = sorted(ctx.financials_by_year)[-3:]
    if len(years) == 3:
        ois = [ctx.financials_by_year[y].get("operating_income") for y in years]
        if all(oi is not None and oi < 0 for oi in ois):
            signals.append(RiskSignal(
                "R10", "주의", "3년 연속 영업적자",
                "본업에서 3년 연속 손실이 났어요. 이익을 낼 수 있는 구조인지 확인이 필요해요.",
                f"{years[0]}~{years[-1]}",
            ))
    elif not ctx.financials_by_year:
        unavailable.append("R10")

    # R11 횡령·배임
    fraud = _find(ctx.disclosures_2y, ("횡령", "배임"), ctx.today, 730)
    if fraud:
        signals.append(RiskSignal(
            "R11", "위험", "횡령·배임 공시 이력",
            "경영진 등의 횡령·배임 혐의가 공시된 적이 있어요. 최고 수준의 위험 신호예요.",
            f"{len(fraud)}건", fraud[0].get("rcept_no", ""),
        ))

    # R12 소송 (분기 내)
    suits = _find(ctx.disclosures_2y, ("소송등의제기",), ctx.today, 90)
    if suits:
        signals.append(RiskSignal(
            "R12", "주의", "최근 소송 제기",
            "최근 3개월 안에 소송 관련 공시가 있었어요. 소송 금액이 큰지 확인해 보세요.",
            f"{len(suits)}건", suits[0].get("rcept_no", ""),
        ))

    # R13 공매도 잔고 급증
    if ctx.short_balance_ratio_change is None:
        unavailable.append("R13")
    elif ctx.short_balance_ratio_change >= 1.0:  # 잔고 비중 +1%p 이상
        signals.append(RiskSignal(
            "R13", "주의", "공매도 잔고 증가",
            "주가 하락에 베팅하는 공매도 잔고가 최근 늘었어요.",
            f"잔고 비중 +{ctx.short_balance_ratio_change:.1f}%p",
        ))

    # R14 거래량 이상 급증 + 공시 부재
    if ctx.volume_ratio is None:
        unavailable.append("R14")
    elif ctx.volume_ratio >= 5.0 and not ctx.has_recent_disclosure:
        signals.append(RiskSignal(
            "R14", "주의", "이유 없는 거래량 급증",
            f"거래량이 평소의 {ctx.volume_ratio:.0f}배로 뛰었는데 관련 공시가 없어요. 소문에 의한 움직임일 수 있어요.",
        ))

    # R15 상장폐지 사유 발생
    delist = _find(ctx.disclosures_2y, ("상장폐지", "상장적격성"), ctx.today, 730)
    if delist:
        signals.append(RiskSignal(
            "R15", "위험", "상장폐지 관련 공시",
            "상장폐지 사유 또는 심사 관련 공시가 있었어요. 원문을 반드시 확인하세요.",
            f"{len(delist)}건", delist[0].get("rcept_no", ""),
        ))

    return signals, unavailable


def overall_level(signals: list[RiskSignal]) -> str:
    """가장 높은 severity 기준의 종합 등급."""
    if not signals:
        return "안전"
    top = max(SEVERITY_ORDER[s.severity] for s in signals)
    return {1: "주의", 2: "경고", 3: "위험"}[top]


TOTAL_RULES = 15
