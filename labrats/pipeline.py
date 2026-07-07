"""Run persona evaluations across papers."""

import asyncio

from loguru import logger

from labrats.models import Paper, PaperCard, PersonaConfig
from labrats.personas import run_persona, summarize_abstract


async def evaluate_paper(
    paper: Paper,
    personas: list[PersonaConfig],
    persona_prompt: str,
    model: str,
    api_base: str | None = None,
    purpose: str = "",
) -> PaperCard:
    """Evaluate a single paper with all personas concurrently."""
    tasks = [
        run_persona(p, paper, persona_prompt, model, api_base, purpose)
        for p in personas
    ]
    tasks.append(summarize_abstract(paper, model, api_base))
    *results, llm_summary = await asyncio.gather(*tasks)
    return PaperCard(paper=paper, results=results, llm_summary=llm_summary)


async def run_pipeline(
    papers: list[Paper],
    personas: list[PersonaConfig],
    persona_prompt: str,
    model: str,
    api_base: str | None = None,
    on_progress=None,
    purpose: str = "",
    on_card=None,
    concurrency: int = 4,
) -> list[PaperCard]:
    """Evaluate papers with bounded concurrency.

    Up to ``concurrency`` papers are in flight at once. Per-paper
    failures are logged and skipped — surviving papers are returned and
    handed to ``on_card`` as they finish (completion order, not input
    order). ``on_progress(completed, total)`` reports a monotonic count.

    The event loop is single-threaded, so ``done``, ``cards`` and the
    callbacks are only touched between awaits — no locking needed.
    """
    total = len(papers)
    sem = asyncio.Semaphore(max(1, concurrency))
    cards: list[PaperCard] = []
    done = 0

    async def _run_one(paper):
        nonlocal done
        async with sem:
            try:
                card = await evaluate_paper(
                    paper, personas, persona_prompt,
                    model, api_base, purpose,
                )
            except Exception as e:
                logger.exception(
                    f"paper failed, skipping: {paper.title[:72]} — {e}"
                )
                card = None
        done += 1
        if card is not None:
            cards.append(card)
            if on_card:
                on_card(card)
        if on_progress:
            on_progress(done, total)

    await asyncio.gather(*(_run_one(p) for p in papers))
    return cards
