"""도메인 에러. tool 레이어에서 사용자 친화 메시지로 변환된다."""


class WhyMovedError(Exception):
    """서비스 공통 베이스 에러."""


class StockNotFoundError(WhyMovedError):
    def __init__(self, query: str):
        super().__init__(
            f"'{query}' 종목을 찾지 못했어요. 정확한 종목명 또는 6자리 종목코드로 다시 시도해 주세요."
        )


class AmbiguousStockError(WhyMovedError):
    """여러 종목이 매칭 — 후보를 사용자에게 되물을 수 있게 candidates를 담는다."""

    def __init__(self, query: str, candidates: list[str]):
        self.candidates = candidates[:5]
        names = ", ".join(self.candidates)
        super().__init__(
            f"'{query}'에 해당하는 종목이 여러 개예요: {names}. 어느 종목인지 알려주시면 바로 분석해 드릴게요."
        )


class EtfNotSupportedError(WhyMovedError):
    def __init__(self, query: str):
        super().__init__(
            f"'{query}'은(는) ETF·ETN으로 보여요. ETF는 기업 공시 기반 분석 대상이 아니라 아직 지원하지 않아요. "
            "개별 종목명(예: 삼성전자)으로 물어봐 주세요."
        )


class DisclosureNotFoundError(WhyMovedError):
    def __init__(self, hint: str):
        super().__init__(f"해당 공시를 찾지 못했어요. ({hint})")


class UpstreamError(WhyMovedError):
    def __init__(self, upstream: str, detail: str = ""):
        super().__init__(
            f"{upstream} 데이터를 일시적으로 가져오지 못했어요. 잠시 후 다시 시도해 주세요."
            + (f" ({detail})" if detail else "")
        )
