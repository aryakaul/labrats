"""Run persona evaluations across papers."""

import asyncio

from labrats.models import Paper, PaperCard, PersonaConfig
from labrats.personas import run_persona, summarize_abstract


async def evaluate_paper(
    paper: Paper,
    personas: list[PersonaConfig],
    persona_prompt: str,
    model: str,
    api_base: str | None = None,
) -> PaperCard:
    """Evaluate a single paper with all personas concurrently."""
    tasks = [
        run_persona(p, paper, persona_prompt, model, api_base)
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
) -> list[PaperCard]:
    """Evaluate papers sequentially. Calls on_progress(done, total) after each."""
    cards = []
    total = len(papers)
    for i, paper in enumerate(papers, start=1):
        card = await evaluate_paper(
            paper, personas, persona_prompt, model, api_base,
        )
        cards.append(card)
        if on_progress:
            on_progress(i, total)
    return cards
