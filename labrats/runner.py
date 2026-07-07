"""Shared pipeline orchestration — fetch, evaluate, persist."""

import asyncio
from datetime import date, timedelta
from pathlib import Path

from loguru import logger

from labrats.db import (
    already_scraped,
    delete_persona_result,
    enforce_cap,
    last_scraped,
    load_papers,
    log_scrape,
    open_db,
    paper_count,
    papers_needing_eval,
    upsert_llm_summary,
    upsert_papers,
    upsert_results,
)
from labrats.personas import (
    inject_api_keys,
    load_personas,
    load_profiles,
    profile_run_params,
)
from labrats.pipeline import run_pipeline
from labrats.scraper import (
    fetch_from_sources,
    filter_by_topics,
    union_arxiv_cats,
)


def run_all_profiles(
    config_dir: Path,
    db_path: Path,
    model: str,
    api_base: str | None,
    source: str,
    *,
    on_phase=None,
    on_profile=None,
    on_progress=None,
) -> None:
    """Fetch papers and run persona evaluation for all profiles.

    Optional callbacks (all no-ops if omitted):
      on_phase(phase: str)        — "scraping" | "evaluating" | "done"
      on_profile(name: str)       — called when switching to a profile
      on_progress(done, total)    — per-paper progress forwarded to run_pipeline
    """
    def _phase(p):
        if on_phase:
            on_phase(p)

    def _profile(n):
        if on_profile:
            on_profile(n)

    inject_api_keys(config_dir)

    today_dt = date.today()
    today = str(today_dt)
    start = str(today_dt - timedelta(days=1))
    week_start = str(today_dt - timedelta(days=7))
    end = today

    profiles = load_profiles(config_dir)
    conn = open_db(db_path)

    _phase("scraping")
    needs_scrape = [
        p for p in profiles
        if not already_scraped(conn, p["name"], today)
    ]

    if not needs_scrape:
        logger.info("scraping: all profiles already scraped today")
    else:
        first_time, returning = [], []
        for p in needs_scrape:
            bucket = first_time if paper_count(conn, p["name"]) == 0 else returning
            bucket.append(p)

        if first_time:
            names = ", ".join(p["name"] for p in first_time)
            logger.info(
                f"scraping: {len(first_time)} new profile(s)"
                f" — backfill {week_start} → {end}: {names}"
            )
        if returning:
            names = ", ".join(p["name"] for p in returning)
            logger.info(f"scraping: {len(returning)} returning profile(s): {names}")

        if returning:
            last_dates = [last_scraped(conn, p["name"]) for p in returning]
            returning_start = min((d for d in last_dates if d), default=start)
        else:
            returning_start = start

        for group, fetch_start in (
            (first_time, week_start),
            (returning, returning_start),
        ):
            if not group:
                continue
            fetch_topics = {"arxiv_categories": union_arxiv_cats(group)}
            logger.info(f"fetching from {source}  ({fetch_start} → {end})")
            all_papers, errors = fetch_from_sources(
                fetch_start, end, source, fetch_topics,
            )
            for err in errors:
                logger.warning(f"source error: {err}")
            logger.info(f"fetched {len(all_papers)} papers total")
            for profile in group:
                pname = profile["name"]
                cap = profile.get("max_papers") or 500
                filtered = filter_by_topics(all_papers, profile)
                logger.info(
                    f"  {pname:<30}  "
                    f"{len(filtered)} papers after filter (cap {cap})"
                )
                upsert_papers(conn, filtered, pname, today)
                enforce_cap(conn, pname, cap)
                log_scrape(conn, pname, today)

    _phase("evaluating")
    logger.info("─" * 48)

    for profile in profiles:
        pname = profile["name"]
        _profile(pname)
        pnames = profile.get("personas")
        personas = [
            p for p in load_personas(config_dir, pnames)
            if p.enabled
        ]
        if not personas:
            logger.warning(f"  {pname}: no enabled personas — skipping")
            continue

        db_papers = load_papers(conn, pname)
        if not db_papers:
            logger.info(f"  {pname}: no papers in DB — skipping")
            continue

        persona_names = [p.name for p in personas]
        effective_model, persona_prompt, purpose = profile_run_params(
            profile, model
        )
        to_eval = papers_needing_eval(conn, db_papers, pname, persona_names)
        if not to_eval:
            logger.info(
                f"  {pname}: all {len(db_papers)} papers already evaluated"
            )
            continue

        logger.info(
            f"  {pname}: evaluating {len(to_eval)}/{len(db_papers)}"
            f" papers  ×  {len(personas)} personas  [{effective_model}]"
        )

        def _make_progress_cb(papers_list):
            def cb(done, total):
                logger.info(f"    [{done}/{total}] {papers_list[done - 1].title[:72]}")
                if on_progress:
                    on_progress(done, total)
            return cb

        def _persist(card, _pname=pname):
            upsert_results(conn, card.paper.doi, _pname, card.results)
            if card.llm_summary:
                upsert_llm_summary(conn, card.paper.doi, card.llm_summary)

        asyncio.run(
            run_pipeline(
                to_eval, personas, persona_prompt,
                effective_model, api_base, _make_progress_cb(to_eval),
                purpose=purpose,
                on_card=_persist,
            )
        )

        logger.info(f"  {pname}: done")

    conn.close()
    logger.info("─" * 48)
    logger.info("run complete")
    _phase("done")


def rerun_paper(
    config_dir: Path,
    db_path: Path,
    model: str,
    api_base: str | None,
    doi: str,
    profile_name: str,
    persona_name: str,
) -> None:
    """Re-evaluate a single paper with one persona, replacing its result."""
    inject_api_keys(config_dir)

    conn = open_db(db_path)

    papers = load_papers(conn, profile_name)
    paper = next((p for p in papers if p.doi == doi), None)
    if not paper:
        conn.close()
        raise ValueError(f"paper {doi!r} not found in profile {profile_name!r}")

    profiles = load_profiles(config_dir)
    profile = next((p for p in profiles if p["name"] == profile_name), None)
    if not profile:
        conn.close()
        raise ValueError(f"profile {profile_name!r} not found")

    pnames = profile.get("personas")
    personas = [
        p for p in load_personas(config_dir, pnames)
        if p.enabled and p.name == persona_name
    ]
    if not personas:
        conn.close()
        raise ValueError(f"persona {persona_name!r} not found or not enabled")

    effective_model, persona_prompt, purpose = profile_run_params(
        profile, model
    )

    logger.info(f"rerun: {persona_name} on {paper.title[:60]}  [{effective_model}]")
    delete_persona_result(conn, doi, profile_name, persona_name)

    cards = asyncio.run(
        run_pipeline(
            [paper], personas, persona_prompt, effective_model, api_base,
            purpose=purpose,
        )
    )
    card = cards[0]
    upsert_results(conn, card.paper.doi, profile_name, card.results)
    conn.close()
    logger.info("rerun complete")
