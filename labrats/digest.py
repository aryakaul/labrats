"""Assemble and serialize digest paper cards from the database.

Transport-agnostic card layer shared by the web server and the static
site exporter — neither renderer imports the other.
"""

from pathlib import Path

from labrats.assets import persona_image_url
from labrats.db import (
    last_scraped,
    load_llm_summary,
    load_papers,
    load_results,
    paper_count,
)
from labrats.models import PaperCard
from labrats.personas import load_personas, load_profiles
from labrats.synthesis import parse_llm_summary, score_cards


def card_to_dict(card, config_dir: Path):
    """Serialize a PaperCard to a JSON-safe dict for the digest API."""
    p = card.paper
    return {
        "paper": {
            "doi": p.doi,
            "title": p.title,
            "authors": p.authors,
            "abstract": p.abstract,
            "category": p.category,
            "date": p.date,
            "url": p.url,
        },
        "results": [
            {
                "persona_name": r.persona_name,
                "scores": r.scores,
                "summary": r.summary,
                "model": r.model,
                "image_url": persona_image_url(
                    r.persona_name, config_dir,
                ),
            }
            for r in card.results
        ],
        "tension": card.tension,
        "avg_score": card.avg_score,
        "disputed": card.disputed,
        "disputed_field": card.disputed_field,
        "llm_summary": parse_llm_summary(card.llm_summary),
    }


def build_profile_cards(conn, config_dir: Path, profile: dict):
    """Assemble scored PaperCards for a profile from the DB."""
    pnames = profile.get("personas")
    persona_list = [
        p for p in load_personas(config_dir, pnames)
        if p.enabled
    ]
    persona_names = [p.name for p in persona_list]

    cards = []
    for paper in load_papers(conn, profile["name"]):
        res_map = load_results(conn, paper.doi, profile["name"])
        results = [res_map[n] for n in persona_names if n in res_map]
        if results:
            llm_summary = load_llm_summary(conn, paper.doi)
            cards.append(
                PaperCard(
                    paper=paper,
                    results=results,
                    llm_summary=llm_summary,
                )
            )

    return score_cards(cards)


def build_digest_profiles(conn, config_dir: Path):
    """Profile list with paper counts and last scrape dates."""
    return [
        {
            "name": p["name"],
            "paper_count": paper_count(conn, p["name"]),
            "last_scraped": last_scraped(conn, p["name"]),
        }
        for p in load_profiles(config_dir)
    ]
