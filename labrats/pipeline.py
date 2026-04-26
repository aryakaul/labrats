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
from labrats.personas import run_persona


async def evaluate_paper(
    paper: Paper,
    personas: list[PersonaConfig],
    model: str,
    api_base: str | None = None,
) -> PaperCard:
    """Evaluate a single paper with all personas concurrently."""
    tasks = [
        run_persona(persona, paper, model, api_base) for persona in personas
    ]
    results = await asyncio.gather(*tasks)
    return PaperCard(paper=paper, results=results)


async def run_pipeline(
    papers: list[Paper],
    personas: list[PersonaConfig],
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
            card = await evaluate_paper(paper, personas, model, api_base)
            cards.append(card)
            progress.advance(task)
    return cards


async def run_pipeline_headless(
    papers: list[Paper],
    personas: list[PersonaConfig],
    model: str,
    api_base: str | None = None,
    on_progress=None,
) -> list[PaperCard]:
    """Evaluate papers without Rich (for background/web use)."""
    cards = []
    total = len(papers)
    for i, paper in enumerate(papers):
        card = await evaluate_paper(paper, personas, model, api_base)
        cards.append(card)
        if on_progress:
            on_progress(i + 1, total)
    return cards
