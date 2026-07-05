"""종목명/종목코드 → DART corp_code 매핑.

DART corpCode.xml(zip)을 내려받아 상장사만 인메모리 테이블로 유지한다 (주 1회 갱신, 설계 §4).
"""

import io
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass

import httpx

from why_moved.cache.store import TTLCache
from why_moved.common.errors import (
    AmbiguousStockError,
    EtfNotSupportedError,
    StockNotFoundError,
    UpstreamError,
)

CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
CORP_CODE_TTL = 7 * 86400  # 주 1회

# 국민 별칭 → DART 정식 종목명 (정식명이 영문이거나 통칭과 다른 경우)
ALIASES: dict[str, str] = {
    "네이버": "NAVER", "naver": "NAVER",
    "삼전": "삼성전자", "삼성전자우": "삼성전자우",
    "하닉": "SK하이닉스", "sk하이닉스": "SK하이닉스", "에스케이하이닉스": "SK하이닉스",
    "현차": "현대차", "현대자동차": "현대차",
    "엘지전자": "LG전자", "엘지에너지솔루션": "LG에너지솔루션", "엘지화학": "LG화학",
    "카뱅": "카카오뱅크", "카카오뱅크": "카카오뱅크", "카페": "카카오페이",
    "포스코": "POSCO홀딩스", "포스코홀딩스": "POSCO홀딩스",
    "케이비금융": "KB금융", "kb금융": "KB금융",
    "에스케이텔레콤": "SK텔레콤", "skt": "SK텔레콤",
    "케이티": "KT", "kt": "KT",
    "한전": "한국전력", "한국전력공사": "한국전력",
    "삼바": "삼성바이오로직스",
    "셀트": "셀트리온",
    "두산에너빌": "두산에너빌리티",
    "빅히트": "하이브", "hybe": "하이브",
    "제이와이피": "JYP Ent.", "jyp": "JYP Ent.",
    "에스엠": "에스엠", "sm엔터": "에스엠",
    "크래프톤": "크래프톤", "엔씨": "엔씨소프트", "엔씨소프트": "엔씨소프트",
    "에코프로비엠": "에코프로비엠",
    "대한항공": "대한항공", "아시아나": "아시아나항공",
}

_ETF_KEYWORDS = ("KODEX", "TIGER", "ACE ", "SOL ", "PLUS ", "KBSTAR", "HANARO", "ARIRANG", "ETF", "ETN")

# 다중 매칭 후보 정렬용 — 대중 인지도 높은 종목을 먼저 제시
POPULAR = {
    "삼성전자", "삼성물산", "삼성SDI", "삼성전기", "삼성바이오로직스", "삼성생명", "삼성화재", "삼성카드",
    "SK하이닉스", "SK텔레콤", "SK이노베이션", "SK스퀘어",
    "LG전자", "LG화학", "LG에너지솔루션", "LG디스플레이", "LG유플러스",
    "현대차", "현대모비스", "현대건설", "현대글로비스",
    "카카오", "카카오뱅크", "카카오페이", "NAVER", "크래프톤", "엔씨소프트",
    "POSCO홀딩스", "포스코퓨처엠", "KB금융", "신한지주", "하나금융지주", "우리금융지주",
    "셀트리온", "한화에어로스페이스", "한화오션", "두산에너빌리티", "HMM", "기아",
    "에코프로", "에코프로비엠", "하이브", "KT", "KT&G", "한국전력", "대한항공",
}


@dataclass(frozen=True)
class Corp:
    corp_code: str   # DART 고유번호 (8자리)
    stock_code: str  # 거래소 종목코드 (6자리)
    name: str


class CorpCodeResolver:
    def __init__(self, api_key: str, cache: TTLCache, timeout: float = 30.0):
        self._api_key = api_key
        self._cache = cache
        self._timeout = timeout
        self._by_name: dict[str, Corp] = {}
        self._by_stock_code: dict[str, Corp] = {}

    async def _load(self) -> None:
        if self._by_name:
            return
        rows = self._cache.get("corp_codes:listed")
        if rows is None:
            rows = await self._fetch_rows()
            self._cache.set("corp_codes:listed", rows, CORP_CODE_TTL)
        corps = [Corp(*row) for row in rows]
        self._by_name = {c.name: c for c in corps}
        self._by_stock_code = {c.stock_code: c for c in corps}

    async def _fetch_rows(self) -> list[list[str]]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(CORP_CODE_URL, params={"crtfc_key": self._api_key})
                resp.raise_for_status()
                content = resp.content
        except httpx.HTTPError as exc:
            raise UpstreamError("DART(corpCode)", str(exc)) from exc

        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            xml_bytes = zf.read(zf.namelist()[0])
        root = ET.fromstring(xml_bytes)
        rows = []
        for el in root.iter("list"):
            stock_code = (el.findtext("stock_code") or "").strip()
            if len(stock_code) != 6:  # 비상장 제외
                continue
            rows.append([
                (el.findtext("corp_code") or "").strip(),
                stock_code,
                (el.findtext("corp_name") or "").strip(),
            ])
        return rows

    async def name_map(self) -> dict[str, str]:
        """종목코드 → 종목명 매핑 (스크리너 조인용)."""
        await self._load()
        return {code: corp.name for code, corp in self._by_stock_code.items()}

    async def resolve(self, query: str) -> Corp:
        """종목명·별칭·6자리 코드로 상장사를 찾는다.

        - 별칭 사전(ALIASES)으로 통칭 → 정식명 변환 ("네이버"→NAVER)
        - 다중 매칭이면 AmbiguousStockError(candidates)로 후보를 되물을 수 있게 함
        - ETF·ETN 키워드는 미지원임을 전용 메시지로 안내
        """
        await self._load()
        q = query.strip()

        if any(k.lower() in q.lower() for k in _ETF_KEYWORDS):
            raise EtfNotSupportedError(query)

        if q.isdigit() and len(q) == 6:
            corp = self._by_stock_code.get(q)
            if corp:
                return corp
            raise StockNotFoundError(query)

        # 정확 일치 → 별칭 → 대소문자 무시 일치
        corp = self._by_name.get(q)
        if corp:
            return corp
        alias_target = ALIASES.get(q) or ALIASES.get(q.lower())
        if alias_target and alias_target in self._by_name:
            return self._by_name[alias_target]
        lowered = {name.lower(): c for name, c in self._by_name.items()}
        if q.lower() in lowered:
            return lowered[q.lower()]

        # 부분일치: 유일하면 채택, 여러 개면 후보 제시
        candidates = [name for name in self._by_name if q in name]
        if len(candidates) == 1:
            return self._by_name[candidates[0]]
        if 1 < len(candidates) <= 30:
            candidates.sort(key=lambda n: (n not in POPULAR, len(n), n))  # 인지도 → 짧은 이름 순
            raise AmbiguousStockError(query, candidates)
        raise StockNotFoundError(query)
