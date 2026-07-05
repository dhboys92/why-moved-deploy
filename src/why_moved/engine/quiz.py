"""공시 문해력 퀴즈 엔진 — 실제 공시 1건으로 3지선다 문제를 만든다 (순수 함수).

seed 기반 결정적 랜덤(random.Random)만 사용한다 — 같은 공시는 항상 같은 퀴즈.
선지는 공시 템플릿의 so_what을 재사용하므로 별도 콘텐츠 관리가 필요 없다.
"""

import random
from dataclasses import dataclass

from why_moved.engine.disclosure_templates import TEMPLATES, DisclosureTemplate, match_template


@dataclass(frozen=True)
class Quiz:
    question: str
    choices: list[str]
    correct_index: int          # 0-based
    correct_choice: str         # "A" | "B" | "C"
    explanation: str


_LABELS = ("A", "B", "C")


def _shorten(so_what: str, limit: int = 80) -> str:
    """so_what 첫 문장을 선지 길이로 다듬는다."""
    first = so_what.split(". ")[0].rstrip(".")
    return (first[: limit - 1] + "…") if len(first) > limit else first


def build_quiz(company: str, report_name: str, seed: str) -> Quiz:
    """공시 1건 → 3지선다. 정답=해당 템플릿 so_what, 오답=다른 템플릿 so_what 2개."""
    template = match_template(report_name)
    rng = random.Random(seed)

    distractor_pool = [t for t in TEMPLATES if t.type_name != template.type_name]
    distractors: list[DisclosureTemplate] = rng.sample(distractor_pool, 2)

    choices = [_shorten(template.so_what), *(_shorten(t.so_what) for t in distractors)]
    order = list(range(3))
    rng.shuffle(order)
    shuffled = [choices[i] for i in order]
    correct_index = order.index(0)

    question = (
        f"[실제 오늘 공시 퀴즈] {company}이(가) 방금 '{template.type_name}' 공시를 냈어요. "
        f"이 유형의 공시는 일반적으로 어떤 의미로 해석될까요?"
    )
    explanation = (
        f"정답은 {_LABELS[correct_index]}! "
        f"'{template.type_name}'은(는) 이런 공시예요 — {template.what_happened} {template.so_what} "
        f"{template.why_care}"
    )
    return Quiz(
        question=question,
        choices=shuffled,
        correct_index=correct_index,
        correct_choice=_LABELS[correct_index],
        explanation=explanation,
    )
