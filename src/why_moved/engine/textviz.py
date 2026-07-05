"""유니코드 텍스트 시각화 — 어떤 텍스트 클라이언트에서도 렌더되는 폴백 (설계 §3 2차 레이어)."""

_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float], width: int = 20) -> str:
    """숫자 시계열 → 스파크라인. 예: '▁▂▄▅▇█▆▅'"""
    if not values:
        return ""
    if len(values) > width:
        # 균등 샘플링으로 width개로 압축
        step = (len(values) - 1) / (width - 1)
        values = [values[round(i * step)] for i in range(width)]
    lo, hi = min(values), max(values)
    if hi == lo:
        return _SPARK_CHARS[3] * len(values)
    scale = len(_SPARK_CHARS) - 1
    return "".join(_SPARK_CHARS[round((v - lo) / (hi - lo) * scale)] for v in values)


def score_bar(score: int | None, width: int = 10) -> str:
    """0~100 점수 → 채움 바. 예: '███████░░░ 70'"""
    if score is None:
        return "─" * width + "  —"
    filled = round(score / 100 * width)
    return "█" * filled + "░" * (width - filled) + f" {score}"


def flow_bar(value: float, max_abs: float, width: int = 8) -> str:
    """순매수(+/-) → 방향 바. 예: '▓▓▓▓▏ +1.2조'"""
    if max_abs <= 0:
        return ""
    filled = max(1, round(abs(value) / max_abs * width)) if value else 0
    return ("▓" * filled) if value >= 0 else ("▒" * filled)
