"""KST 기준 시각 유틸.

배포 컨테이너는 TZ 미설정(기본 UTC)이라 naive datetime.now()는 KST 00~09시에
전날 날짜를 반환한다. '오늘' 계산은 반드시 이 모듈을 거친다.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    """Asia/Seoul 기준 현재 시각 (tz-aware)."""
    return datetime.now(KST)
