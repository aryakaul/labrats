"""Tests for digest.build_profile_cards — bulk-loading semantics."""

import yaml

from labrats.db import (
    open_db,
    upsert_llm_summary,
    upsert_papers,
    upsert_results,
)
from labrats.digest import build_profile_cards
from labrats.models import Paper, PersonaResult

PROFILE = "P"


def _cfg(tmp_path):
    cfg = tmp_path / "config"
    (cfg / "personas").mkdir(parents=True)
    (cfg / "topics.yaml").write_text(
        yaml.safe_dump({"profiles": [{"name": PROFILE}]})
    )
    for stem, name in (("a", "Alice"), ("b", "Bob")):
        (cfg / "personas" / f"{stem}.yaml").write_text(
            yaml.safe_dump(
                {"name": name, "role": "r", "scored_fields": ["rigor"]}
            )
        )
    return cfg


def _paper(doi, title="T"):
    return Paper(
        doi=doi, title=title, authors=["A"], abstract="x",
        category="c", date="2026-07-01", url=f"https://x/{doi}",
    )


def _res(name):
    return PersonaResult(
        persona_name=name, scores={"rigor": 7.0}, summary="s", model="m",
    )


def test_only_papers_with_results_included(tmp_path):
    cfg = _cfg(tmp_path)
    conn = open_db(tmp_path / "db.sqlite")
    upsert_papers(conn, [_paper("d1"), _paper("d2")], PROFILE, "2026-07-01")
    # only d1 has results
    upsert_results(conn, "d1", PROFILE, [_res("Alice"), _res("Bob")])

    cards = build_profile_cards(conn, cfg, {"name": PROFILE})
    conn.close()
    assert [c.paper.doi for c in cards] == ["d1"]


def test_persona_filtering_and_summary_attached(tmp_path):
    cfg = _cfg(tmp_path)
    conn = open_db(tmp_path / "db.sqlite")
    upsert_papers(conn, [_paper("d1")], PROFILE, "2026-07-01")
    # a stray result for a persona not in config must be dropped
    upsert_results(
        conn, "d1", PROFILE, [_res("Alice"), _res("Ghost")]
    )
    upsert_llm_summary(conn, "d1", "Background\nfoo")

    cards = build_profile_cards(conn, cfg, {"name": PROFILE})
    conn.close()
    assert len(cards) == 1
    names = [r.persona_name for r in cards[0].results]
    assert names == ["Alice"]  # Ghost filtered out
    assert cards[0].llm_summary == "Background\nfoo"


def test_summary_only_for_matching_doi(tmp_path):
    cfg = _cfg(tmp_path)
    conn = open_db(tmp_path / "db.sqlite")
    upsert_papers(conn, [_paper("d1"), _paper("d2")], PROFILE, "2026-07-01")
    upsert_results(conn, "d1", PROFILE, [_res("Alice")])
    upsert_results(conn, "d2", PROFILE, [_res("Bob")])
    upsert_llm_summary(conn, "d2", "only-d2")

    cards = build_profile_cards(conn, cfg, {"name": PROFILE})
    conn.close()
    by_doi = {c.paper.doi: c for c in cards}
    assert by_doi["d1"].llm_summary == ""
    assert by_doi["d2"].llm_summary == "only-d2"
