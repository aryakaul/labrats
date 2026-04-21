import asyncio
from datetime import date, timedelta
from pathlib import Path

import typer
from rich import print as rprint

from labrats.digest import render_digest
from labrats.personas import load_personas, load_topics
from labrats.pipeline import run_pipeline
from labrats.scraper import fetch_papers, filter_by_topics
from labrats.synthesis import score_cards

app = typer.Typer()

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
TEMPLATE_DIR = ROOT / "templates"
OUTPUT_DIR = ROOT / "output"


@app.command()
def run(
	start: str = typer.Option(
		None, help="Start date (YYYY-MM-DD), default yesterday"
	),
	end: str = typer.Option(
		None, help="End date (YYYY-MM-DD), default today"
	),
	model: str = typer.Option(
		"openai/gpt-4o-mini", help="Default LLM model"
	),
):
	today = date.today()
	start = start or str(today - timedelta(days=1))
	end = end or str(today)

	topics = load_topics(CONFIG_DIR)
	personas = load_personas(CONFIG_DIR)

	rprint(f"Fetching papers: {start} to {end}")
	papers = fetch_papers(start, end)
	rprint(f"Found {len(papers)} papers")

	filtered = filter_by_topics(papers, topics)
	rprint(f"After topic filter: {len(filtered)} papers")

	if not filtered:
		rprint("[yellow]No papers matched. Done.[/yellow]")
		raise typer.Exit()

	rprint(
		f"Running {len(personas)} personas "
		f"on {len(filtered)} papers"
	)
	cards = asyncio.run(
		run_pipeline(filtered, personas, model)
	)
	cards = score_cards(cards)

	out = render_digest(cards, TEMPLATE_DIR, OUTPUT_DIR)
	rprint(f"[green]Digest written: {out}[/green]")


@app.command()
def preview(
	model: str = typer.Option(
		"openai/gpt-4o-mini", help="Default LLM model"
	),
):
	topics = load_topics(CONFIG_DIR)
	personas = load_personas(CONFIG_DIR)
	today = date.today()
	start = str(today - timedelta(days=1))
	end = str(today)

	papers = fetch_papers(start, end)
	filtered = filter_by_topics(papers, topics)

	rprint(f"Papers fetched: {len(papers)}")
	rprint(f"After filter: {len(filtered)}")
	rprint(f"Personas loaded: {len(personas)}")
	for p in personas:
		m = p.model or model
		rprint(f"  - {p.name} ({m})")
	if filtered:
		rprint("\n[bold]Top 5 matches:[/bold]")
		for paper in filtered[:5]:
			rprint(f"  {paper.title}")


@app.command()
def test(
	model: str = typer.Option(
		"openai/gpt-4o-mini", help="Default LLM model"
	),
):
	topics = load_topics(CONFIG_DIR)
	personas = load_personas(CONFIG_DIR)
	today = date.today()
	start = str(today - timedelta(days=1))
	end = str(today)

	papers = fetch_papers(start, end)
	filtered = filter_by_topics(papers, topics)

	if not filtered:
		rprint("[yellow]No papers to test with.[/yellow]")
		raise typer.Exit()

	paper = filtered[0]
	rprint(f"[bold]Test paper:[/bold] {paper.title}\n")

	cards = asyncio.run(
		run_pipeline([paper], personas, model)
	)
	cards = score_cards(cards)
	card = cards[0]

	for r in card.results:
		rprint(f"[bold]{r.persona_name}[/bold]")
		rprint(f"  Scores: {r.scores}")
		rprint(f"  {r.summary}\n")
	rprint(
		f"Tension: {card.tension:.2f}  "
		f"Interestingness: {card.interestingness:.2f}"
	)
