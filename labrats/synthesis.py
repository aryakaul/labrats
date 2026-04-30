"""Score and rank paper cards by avg score and cross-persona tension."""

import re
import statistics

from labrats.models import PaperCard

TENSION_THRESHOLD = 1.5

_SUMMARY_SECTIONS = ("Background", "Methods", "Results", "Discussion")
_SUMMARY_RE = re.compile(
    r"(Background|Methods|Results|Discussion):\s*([\s\S]*?)"
    r"(?=(?:Background|Methods|Results|Discussion):|$)",
    re.IGNORECASE,
)


def parse_llm_summary(text: str) -> dict[str, str]:
    """Split a 'Background:... Methods:...' summary into a dict.

    Returns {section: text} for every section, with 'Not stated.' for any
    section the LLM omitted. Returns an empty dict on empty input.
    """
    if not text:
        return {}
    found = {
        m.group(1).capitalize(): m.group(2).strip()
        for m in _SUMMARY_RE.finditer(text)
    }
    return {s: found.get(s) or "Not stated." for s in _SUMMARY_SECTIONS}


def compute_tension(card: PaperCard) -> tuple[float, str]:
    """Return (max_field_variance, field_name) across all scored fields.

    Uses max rather than mean so that strong disagreement on a single
    dimension isn't diluted by consensus on other fields.
    """
    if len(card.results) < 2:
        return 0.0, ""
    all_fields = {k for r in card.results for k in r.scores}
    field_variances = {}
    for f in all_fields:
        values = [r.scores[f] for r in card.results if f in r.scores]
        if len(values) >= 2:
            field_variances[f] = statistics.variance(values)
    if not field_variances:
        return 0.0, ""
    max_field = max(field_variances, key=field_variances.__getitem__)
    return field_variances[max_field], max_field


def compute_avg_score(card: PaperCard) -> float:
    """Mean of all persona scores across all scored fields."""
    if not card.results:
        return 0.0
    all_scores = [v for r in card.results for v in r.scores.values()]
    return statistics.mean(all_scores)


def score_cards(cards: list[PaperCard]) -> list[PaperCard]:
    """Compute scores, set disputed flag, and sort by avg_score."""
    for card in cards:
        card.tension, card.disputed_field = compute_tension(card)
        card.avg_score = compute_avg_score(card)
        card.disputed = card.tension > TENSION_THRESHOLD
    cards.sort(key=lambda c: c.avg_score, reverse=True)
    return cards
