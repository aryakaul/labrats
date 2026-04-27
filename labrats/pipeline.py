"""Run persona evaluations across papers (CLI + headless modes)."""

import asyncio

from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    MofNCompleteColumn,
)

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
        run_persona(persona, paper, persona_prompt, model, api_base)
        for persona in personas
    ]
    tasks.append(summarize_abstract(paper, model, api_base))
    gathered = await asyncio.gather(*tasks)
    results = list(gathered[:-1])
    llm_summary = gathered[-1]
    return PaperCard(paper=paper, results=results, llm_summary=llm_summary)


async def run_pipeline(
    papers: list[Paper],
    personas: list[PersonaConfig],
    persona_prompt: str,
    model: str,
    api_base: str | None = None,
) -> list[PaperCard]:
    """Evaluate papers with a Rich progress bar (for CLI use)."""
    cards = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
    ) as progress:
        task = progress.add_task("Evaluating papers", total=len(papers))
        for paper in papers:
            card = await evaluate_paper(
                paper, personas, persona_prompt, model, api_base,
            )
            cards.append(card)
            progress.advance(task)
    return cards


async def run_pipeline_headless(
    papers: list[Paper],
    personas: list[PersonaConfig],
    persona_prompt: str,
    model: str,
    api_base: str | None = None,
    on_progress=None,
) -> list[PaperCard]:
    """Evaluate papers without Rich (for background/web use)."""
    cards = []
    total = len(papers)
    for i, paper in enumerate(papers):
        card = await evaluate_paper(
            paper, personas, persona_prompt, model, api_base,
        )
        cards.append(card)
        if on_progress:
            on_progress(i + 1, total)
    return cards
