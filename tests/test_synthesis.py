"""Tests for synthesis.py — tension, avg score, sorting, disputed flag."""

from labrats.models import Paper, PaperCard, PersonaResult
from labrats.synthesis import (
	TENSION_THRESHOLD,
	compute_avg_score,
	compute_tension,
	score_cards,
)


def _paper(doi: str = "10.x/y") -> Paper:
	return Paper(
		doi=doi, title="t", authors=[], abstract="a",
		category="c", date="2026-01-01", url="u",
	)


def _result(name: str, scores: dict[str, float]) -> PersonaResult:
	return PersonaResult(persona_name=name, scores=scores, summary="")


def test_compute_tension_empty_when_single_result():
	card = PaperCard(paper=_paper(), results=[_result("a", {"x": 5})])
	assert compute_tension(card) == (0.0, "")


def test_compute_tension_picks_max_variance_field():
	# rigor: identical (var 0); novelty: wide spread (var 24.5)
	card = PaperCard(
		paper=_paper(),
		results=[
			_result("a", {"rigor": 7, "novelty": 2}),
			_result("b", {"rigor": 7, "novelty": 9}),
		],
	)
	variance, field = compute_tension(card)
	assert field == "novelty"
	assert variance > 0


def test_compute_tension_skips_fields_missing_on_some_personas():
	card = PaperCard(
		paper=_paper(),
		results=[
			_result("a", {"shared": 5, "only_a": 1}),
			_result("b", {"shared": 8}),
		],
	)
	_, field = compute_tension(card)
	assert field == "shared"


def test_compute_avg_score_empty_results():
	assert compute_avg_score(PaperCard(paper=_paper())) == 0.0


def test_compute_avg_score_mean_across_all_fields():
	card = PaperCard(
		paper=_paper(),
		results=[
			_result("a", {"x": 4, "y": 6}),
			_result("b", {"x": 8, "y": 10}),
		],
	)
	assert compute_avg_score(card) == 7.0


def test_score_cards_sorts_descending_by_avg_score():
	low = PaperCard(
		paper=_paper("10.x/low"),
		results=[_result("a", {"s": 3})],
	)
	high = PaperCard(
		paper=_paper("10.x/high"),
		results=[_result("a", {"s": 9})],
	)
	out = score_cards([low, high])
	assert [c.paper.doi for c in out] == ["10.x/high", "10.x/low"]


def test_score_cards_sets_disputed_when_above_threshold():
	# Variance of [1, 10] = 40.5, well above 1.5
	card = PaperCard(
		paper=_paper(),
		results=[
			_result("a", {"novelty": 1}),
			_result("b", {"novelty": 10}),
		],
	)
	score_cards([card])
	assert card.disputed is True
	assert card.disputed_field == "novelty"
	assert card.tension > TENSION_THRESHOLD


def test_score_cards_consensus_not_disputed():
	card = PaperCard(
		paper=_paper(),
		results=[
			_result("a", {"novelty": 6}),
			_result("b", {"novelty": 7}),
		],
	)
	score_cards([card])
	assert card.disputed is False
