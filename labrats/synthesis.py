"""Score and rank paper cards by tension and interestingness."""

import statistics

from labrats.models import PaperCard


def compute_tension(card: PaperCard) -> float:
    if len(card.results) < 2:
        return 0.0
    variances = []
    all_fields = {k for r in card.results for k in r.scores}
    for field in all_fields:
        values = [r.scores[field] for r in card.results if field in r.scores]
        if len(values) >= 2:
            variances.append(statistics.variance(values))
    if not variances:
        return 0.0
    return statistics.mean(variances)


def compute_interestingness(card: PaperCard) -> float:
    if not card.results:
        return 0.0
    all_scores = [v for r in card.results for v in r.scores.values()]
    mean_score = statistics.mean(all_scores)
    return (0.6 * mean_score) + (0.4 * card.tension)


def score_cards(cards: list[PaperCard]) -> list[PaperCard]:
    for card in cards:
        card.tension = compute_tension(card)
        card.interestingness = compute_interestingness(card)
    cards.sort(key=lambda c: c.interestingness, reverse=True)
    return cards
