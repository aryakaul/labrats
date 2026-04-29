"""Tests for scraper.py — filtering, dedup, parsers."""

import xml.etree.ElementTree as ET

from labrats.models import Paper
from labrats.scraper import (
	_parse_arxiv_entry,
	_parse_paper,
	dedup_papers,
	filter_by_topics,
	union_arxiv_cats,
)


def _paper(
	doi: str = "10.x/y",
	title: str = "",
	abstract: str = "",
	category: str = "",
) -> Paper:
	return Paper(
		doi=doi, title=title, authors=[], abstract=abstract,
		category=category, date="2026-01-01", url="u",
	)


# ── filter_by_topics ──


def test_filter_empty_topics_passes_everything():
	papers = [_paper(title="anything")]
	assert filter_by_topics(papers, {}) == papers


def test_filter_keyword_and_category_are_anded():
	bio = _paper(title="CRISPR study", category="genomics")
	# Right keyword, wrong category — must be excluded under AND
	wrong_cat = _paper(title="CRISPR study", category="ecology")
	# Right category, wrong keyword
	wrong_kw = _paper(title="unrelated", category="genomics")
	topics = {"keywords": ["crispr"], "categories": ["genomics"]}
	out = filter_by_topics([bio, wrong_cat, wrong_kw], topics)
	assert out == [bio]


def test_filter_keyword_only_matches_in_title_or_abstract():
	in_title = _paper(title="ribosome dynamics")
	in_abstract = _paper(abstract="we study the ribosome")
	miss = _paper(title="protein folding")
	out = filter_by_topics(
		[in_title, in_abstract, miss], {"keywords": ["ribosome"]},
	)
	assert out == [in_title, in_abstract]


def test_filter_unions_categories_and_arxiv_categories():
	a = _paper(category="genomics")
	b = _paper(category="q-bio.GN")
	topics = {
		"categories": ["genomics"],
		"arxiv_categories": ["q-bio.GN"],
	}
	assert filter_by_topics([a, b], topics) == [a, b]


# ── dedup_papers ──


def test_dedup_preserves_first_occurrence():
	a = _paper("10.x/1", title="first")
	dup = _paper("10.x/1", title="second")
	c = _paper("10.x/2", title="other")
	out = dedup_papers([a, dup, c])
	assert [p.title for p in out] == ["first", "other"]


# ── union_arxiv_cats ──


def test_union_arxiv_cats_dedupes_across_profiles():
	profiles = [
		{"arxiv_categories": ["q-bio.GN", "q-bio.MN"]},
		{"arxiv_categories": ["q-bio.GN", "cs.LG"]},
		{},
	]
	assert sorted(union_arxiv_cats(profiles)) == [
		"cs.LG", "q-bio.GN", "q-bio.MN",
	]


# ── _parse_paper (biorxiv) ──


def test_parse_paper_splits_authors_and_builds_url():
	entry = {
		"doi": "10.1101/2026.01.01.000001",
		"title": "A Study",
		"authors": "Doe, J.; Roe, R. ; ",
		"abstract": "...",
		"category": "genomics",
		"date": "2026-01-01",
	}
	p = _parse_paper(entry)
	assert p.doi == "10.1101/2026.01.01.000001"
	assert p.authors == ["Doe, J.", "Roe, R."]
	assert p.url == (
		"https://www.biorxiv.org/content/"
		"10.1101/2026.01.01.000001"
	)


# ── _parse_arxiv_entry ──


_ATOM_NS = "http://www.w3.org/2005/Atom"
_ARXIV_NS = "http://arxiv.org/schemas/atom"


def _arxiv_xml(
	arxiv_id: str = "2301.00001v2",
	include_doi: bool = False,
	primary_cat: str = "q-bio.GN",
) -> str:
	doi_el = (
		f'<arxiv:doi xmlns:arxiv="{_ARXIV_NS}">10.x/yz</arxiv:doi>'
		if include_doi else ""
	)
	return f"""
	<entry xmlns="{_ATOM_NS}" xmlns:arxiv="{_ARXIV_NS}">
		<id>http://arxiv.org/abs/{arxiv_id}</id>
		<title>Sample Title</title>
		<summary>Sample abstract.</summary>
		<published>2026-01-15T00:00:00Z</published>
		<author><name>Alice</name></author>
		<author><name>Bob</name></author>
		<arxiv:primary_category term="{primary_cat}"/>
		<category term="cs.LG"/>
		{doi_el}
	</entry>
	"""


def test_parse_arxiv_canonicalises_versioned_id_and_falls_back_doi():
	entry = ET.fromstring(_arxiv_xml())
	p = _parse_arxiv_entry(entry)
	assert p.doi == "arxiv:2301.00001"
	assert p.url == "https://arxiv.org/abs/2301.00001"
	assert p.authors == ["Alice", "Bob"]
	assert p.date == "2026-01-15"


def test_parse_arxiv_uses_real_doi_when_present():
	entry = ET.fromstring(_arxiv_xml(include_doi=True))
	p = _parse_arxiv_entry(entry)
	assert p.doi == "10.x/yz"


def test_parse_arxiv_prefers_primary_category():
	entry = ET.fromstring(_arxiv_xml(primary_cat="q-bio.MN"))
	p = _parse_arxiv_entry(entry)
	assert p.category == "q-bio.MN"
