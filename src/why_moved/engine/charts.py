"""차트 생성 엔진 — 순수 함수: 데이터 → PNG bytes (설계 §3 1차 레이어).

패키지에 번들한 나눔고딕(OFL 라이선스)을 등록해 컨테이너에서도 한글이 깨지지 않는다.
서비스 다크 테마(#141517 배경, #FEE500 액센트)를 따른다.
"""

import io
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np

_FONT_PATH = Path(__file__).parent.parent / "assets" / "NanumGothic-Regular.ttf"
if _FONT_PATH.exists():
    fm.fontManager.addfont(str(_FONT_PATH))
    plt.rcParams["font.family"] = fm.FontProperties(fname=str(_FONT_PATH)).get_name()
plt.rcParams["axes.unicode_minus"] = False

BG = "#141517"
PANEL = "#1b1e22"
TEXT = "#f3f3f3"
SUB = "#a4a6aa"
ACCENT = "#FEE500"
UP = "#f87171"    # 국내 관례: 상승=빨강
DOWN = "#60a5fa"  # 하락=파랑

_SEVERITY_COLOR = {"주의": "#fbbf24", "경고": "#fb923c", "위험": "#f87171"}


def _fig(width: float, height: float):
    fig, ax = plt.subplots(figsize=(width, height), dpi=150)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    for spine in ax.spines.values():
        spine.set_color("#3a3e43")
    ax.tick_params(colors=SUB, labelsize=8)
    return fig, ax


def _to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _fmt_date(yyyymmdd: str) -> str:
    return f"{yyyymmdd[4:6]}/{yyyymmdd[6:]}"


def price_with_disclosures(
    name: str,
    series: list[dict],          # [{date, close, volume}]
    disclosures: list[dict],     # [{rcept_dt, type_name, no}] — 번호 붙은 중요 공시
) -> bytes:
    """[Hero] 가격 라인 + 번호 공시 마커 — 번호는 응답의 chart_events와 1:1 대응한다."""
    dates = [s["date"] for s in series]
    closes = [s["close"] for s in series]
    x = np.arange(len(dates))

    fig, ax = _fig(8, 4)
    color = UP if closes[-1] >= closes[0] else DOWN
    ax.plot(x, closes, color=color, linewidth=1.8)
    ax.fill_between(x, closes, min(closes), color=color, alpha=0.08)

    # 날짜별로 이벤트 번호를 묶어 마커 하나 + 번호 라벨로 표시
    date_index = {d: i for i, d in enumerate(dates)}
    by_date: dict[int, list[int]] = {}
    for dis in disclosures:
        idx = date_index.get(dis.get("rcept_dt", ""))
        if idx is not None and dis.get("no"):
            by_date.setdefault(idx, []).append(dis["no"])

    for idx, numbers in by_date.items():
        y = closes[idx]
        ax.scatter([idx], [y], color=ACCENT, zorder=5, s=42, marker="v")
        label = "·".join(str(n) for n in sorted(numbers)[:3])
        if len(numbers) > 3:
            label += "+"
        ax.annotate(
            label, (idx, y),
            textcoords="offset points", xytext=(0, 11),
            ha="center", fontsize=9, color="#111111", fontweight="bold",
            bbox={"boxstyle": "circle,pad=0.18", "facecolor": ACCENT, "edgecolor": "none"},
            zorder=6, clip_on=False,
        )

    step = max(1, len(dates) // 6)
    ax.set_xticks(x[::step])
    ax.set_xticklabels([_fmt_date(d) for d in dates[::step]])
    change = (closes[-1] - closes[0]) / closes[0] * 100 if closes[0] else 0
    ax.set_title(
        f"{name} — 최근 {len(dates)}거래일 {change:+.1f}%   ▼번호=공시 (아래 목록 참조)",
        color=TEXT, fontsize=11, pad=10,
    )
    ax.margins(y=0.12)  # 번호 라벨이 위로 튀어나올 공간
    ax.grid(axis="y", color="#2a2d31", linewidth=0.5)
    return _to_png(fig)


def health_radar(name: str, scores: dict[str, int | None], grade: str) -> bytes:
    """5축 건강진단 레이더 차트 + 학점."""
    labels = {"value": "가치", "growth": "성장", "profitability": "수익성",
              "stability": "건전성", "dividend": "배당"}
    axes_names = [labels[k] for k in labels]
    values = [scores.get(k) or 0 for k in labels]

    angles = np.linspace(0, 2 * np.pi, len(values), endpoint=False).tolist()
    values_c = values + values[:1]
    angles_c = angles + angles[:1]

    fig = plt.figure(figsize=(5.4, 5), dpi=150)
    fig.patch.set_facecolor(BG)
    ax = fig.add_subplot(111, polar=True)
    ax.set_facecolor(BG)
    ax.plot(angles_c, values_c, color=ACCENT, linewidth=2)
    ax.fill(angles_c, values_c, color=ACCENT, alpha=0.22)
    ax.set_xticks(angles)
    ax.set_xticklabels(axes_names, color=TEXT, fontsize=11)
    ax.set_ylim(0, 100)
    ax.set_yticks([25, 50, 75])
    ax.set_yticklabels(["25", "50", "75"], color=SUB, fontsize=7)
    ax.grid(color="#3a3e43")
    ax.spines["polar"].set_color("#3a3e43")
    ax.set_title(f"{name} 재무 건강진단", color=TEXT, fontsize=13, pad=24)
    fig.text(0.5, 0.47, grade, ha="center", va="center",
             fontsize=44, color=ACCENT, fontweight="bold", alpha=0.85)
    return _to_png(fig)


def flow_with_price(
    name: str,
    series: list[dict],       # [{date, close}]
    flows: list[dict],        # [{date(YYYY.MM.DD), inst_shares, frgn_shares, close}]
) -> bytes:
    """기관·외국인 일별 순매수(추정대금) 막대 + 주가 라인."""
    flow_map = {f["date"].replace(".", ""): f for f in flows}
    dates = [s["date"] for s in series if s["date"] in flow_map][-20:]
    if not dates:
        dates = [s["date"] for s in series][-20:]
    closes = [next(s["close"] for s in series if s["date"] == d) for d in dates]
    inst = [flow_map.get(d, {}).get("inst_shares", 0) * flow_map.get(d, {}).get("close", 0) / 1e8 for d in dates]
    frgn = [flow_map.get(d, {}).get("frgn_shares", 0) * flow_map.get(d, {}).get("close", 0) / 1e8 for d in dates]
    x = np.arange(len(dates))

    fig, ax = _fig(8, 4)
    ax.bar(x - 0.2, inst, width=0.4, color="#4ade80", label="기관")
    ax.bar(x + 0.2, frgn, width=0.4, color="#60a5fa", label="외국인")
    ax.axhline(0, color="#3a3e43", linewidth=0.8)
    ax.set_ylabel("순매수 (억원, 추정)", color=SUB, fontsize=9)

    ax2 = ax.twinx()
    ax2.plot(x, closes, color=ACCENT, linewidth=1.6, label="주가")
    ax2.tick_params(colors=SUB, labelsize=8)
    ax2.spines["top"].set_visible(False)

    step = max(1, len(dates) // 6)
    ax.set_xticks(x[::step])
    ax.set_xticklabels([_fmt_date(d) for d in dates[::step]])
    ax.set_title(f"{name} — 기관·외국인 순매수와 주가 (최근 {len(dates)}거래일)",
                 color=TEXT, fontsize=11, pad=10)
    ax.legend(loc="upper left", facecolor=PANEL, edgecolor="#3a3e43",
              labelcolor=TEXT, fontsize=8)
    return _to_png(fig)


def risk_card(name: str, level: str, signals: list[dict], checked: int) -> bytes:
    """위험신호 요약 카드 (공유용). 인치 단위로 배치해 신호 수와 무관하게 여백이 일정하다."""
    rows = signals[:8] if signals else [None]
    top, row_h, footer = 0.62, 0.34, 0.42  # inch: 헤더 / 신호 한 줄 / 푸터
    height = top + 0.18 + row_h * len(rows) + footer
    fig = plt.figure(figsize=(6.4, height), dpi=150)
    fig.patch.set_facecolor(BG)

    def y_frac(inches_from_top: float) -> float:
        return 1 - inches_from_top / height

    color = _SEVERITY_COLOR.get(level, "#4ade80")
    fig.text(0.05, y_frac(0.18), f"{name} 위험신호 진단", color=TEXT,
             fontsize=14, fontweight="bold", va="top")
    fig.text(0.95, y_frac(0.16), level, color=color, fontsize=17,
             fontweight="bold", va="top", ha="right")
    fig.add_artist(plt.Line2D([0.05, 0.95], [y_frac(top)] * 2,
                              color="#3a3e43", linewidth=0.8, transform=fig.transFigure))

    y_in = top + 0.18
    for s in rows:
        if s is None:
            fig.text(0.05, y_frac(y_in), f"점검한 룰 {checked}개에서 위험신호가 발견되지 않았어요.",
                     color="#4ade80", fontsize=10.5, va="top")
        else:
            c = _SEVERITY_COLOR.get(s.get("severity", "주의"), SUB)
            fig.text(0.05, y_frac(y_in), f"[{s.get('severity')}] {s.get('title')}",
                     color=c, fontsize=10.5, va="top")
        y_in += row_h

    fig.text(0.05, 0.12 / height, f"룰 {checked}개 점검 · 출처: DART·KIND · 투자 권유가 아닙니다",
             color=SUB, fontsize=7.5, va="bottom")
    return _to_png(fig)
