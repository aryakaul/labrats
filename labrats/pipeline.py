import asyncio

from rich.progress import (
	Progress, SpinnerColumn, TextColumn,
	BarColumn, MofNCompleteColumn,
)

from labrats.models import (
	Paper, PaperCard, PersonaConfig, PersonaResult,
)
from labrats.personas import run_persona


async def evaluate_paper(
	paper: Paper,
	personas: list[PersonaConfig],
	model: str,
) -> PaperCard:
	tasks = [
		run_persona(persona, paper, model)
		for persona in personas
	]
	results: list[PersonaResult] = await asyncio.gather(
		*tasks
	)
	return PaperCard(paper=paper, results=results)


async def run_pipeline(
	papers: list[Paper],
	personas: list[PersonaConfig],
	model: str,
) -> list[PaperCard]:
	cards = []
	with Progress(
		SpinnerColumn(),
		TextColumn("[bold]{task.description}"),
		BarColumn(),
		MofNCompleteColumn(),
	) as progress:
		task = progress.add_task(
			"Evaluating papers", total=len(papers)
		)
		for paper in papers:
			card = await evaluate_paper(
				paper, personas, model
			)
			cards.append(card)
			progress.advance(task)
	return cards
