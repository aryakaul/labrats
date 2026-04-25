import asyncio
import os
import shutil
from datetime import date, timedelta
from pathlib import Path

import typer
from rich import print as rprint

from labrats.config_server import serve_config
from labrats.db import (
    already_scraped,
    enforce_cap,
    load_papers,
    load_results,
    log_scrape,
    open_db,
    papers_needing_eval,
    upsert_papers,
    upsert_results,
)
from labrats.digest import render_digest
from labrats.models import Paper, PaperCard
from labrats.personas import load_personas, load_profiles
from labrats.pipeline import run_pipeline
from labrats.scraper import (
    fetch_papers,
    fetch_papers_arxiv,
    filter_by_topics,
)
from labrats.synthesis import score_cards

app = typer.Typer()

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = PACKAGE_DIR / "templates"
BUNDLED_DEFAULTS = PACKAGE_DIR / "defaults"

_CFG_HELP = "Config directory " "(default: $XDG_CONFIG_HOME/labrats)"
_SRC_HELP = "Data source: biorxiv, arxiv, all"
_API_HELP = "Local API base URL, e.g. " "http://localhost:2276/v1"
_DB_HELP = "SQLite DB path " "(default: $XDG_DATA_HOME/labrats/labrats.db)"


def _config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "~/.config")
    return Path(xdg).expanduser() / "labrats"


def _data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME", "~/.local/share")
    return Path(xdg).expanduser() / "labrats"


def _db_path() -> Path:
    return _data_dir() / "labrats.db"


def _require_config(config_dir: Path) -> None:
    if not config_dir.exists():
        rprint(
            f"[red]Config not found: {config_dir}[/red]\n"
            "Run [bold]labrats init[/bold] to create it."
        )
        raise typer.Exit(1)


def _dedup(papers: list[Paper]) -> list[Paper]:
    seen: set[str] = set()
    result = []
    for p in papers:
        if p.doi not in seen:
            seen.add(p.doi)
            result.append(p)
    return result


def _fetch_from_sources(
    start: str,
    end: str,
    source: str,
    topics: dict,
) -> list[Paper]:
    papers: list[Paper] = []
    errors: list[str] = []

    if source in ("biorxiv", "all"):
        try:
            papers += fetch_papers(start, end)
        except Exception as e:
            errors.append(f"biorxiv: {e}")

    if source in ("arxiv", "all"):
        try:
            cats = topics.get("arxiv_categories", [])
            papers += fetch_papers_arxiv(start, end, cats)
        except Exception as e:
            errors.append(f"arxiv: {e}")

    for err in errors:
        rprint(f"[yellow]Warning — {err}[/yellow]")

    if not papers and errors:
        rprint("[red]All sources failed.[/red]")
        raise typer.Exit(1)

    return _dedup(papers)


def _union_arxiv_cats(profiles: list[dict]) -> list[str]:
    return list({c for p in profiles for c in p.get("arxiv_categories", [])})


@app.command()
def init(
    config_dir: Path = typer.Option(None, help=_CFG_HELP),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite existing files",
    ),
):
    """Seed config dir with bundled defaults."""
    target = config_dir or _config_dir()
    target.mkdir(parents=True, exist_ok=True)
    (target / "personas").mkdir(exist_ok=True)

    copied, skipped = [], []
    for src in sorted(BUNDLED_DEFAULTS.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(BUNDLED_DEFAULTS)
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and not force:
            skipped.append(str(rel))
        else:
            shutil.copy2(src, dst)
            copied.append(str(rel))

    for f in copied:
        rprint(f"  [green]created[/green]  {f}")
    for f in skipped:
        rprint(f"  [yellow]skipped[/yellow]  {f}" "  (--force to overwrite)")
    rprint(f"\nConfig dir: {target}")


@app.command()
def config(
    config_dir: Path = typer.Option(None, help=_CFG_HELP),
    port: int = typer.Option(
        8484,
        help="Port for config server",
    ),
):
    """Open the config UI in your browser."""
    cfg = config_dir or _config_dir()
    _require_config(cfg)
    serve_config(cfg, port)


@app.command()
def run(
    start: str = typer.Option(
        None,
        help="Start date (YYYY-MM-DD), default yesterday",
    ),
    end: str = typer.Option(
        None,
        help="End date (YYYY-MM-DD), default today",
    ),
    model: str = typer.Option(
        "openai/gpt-4o-mini",
        help="Default LLM model",
    ),
    source: str = typer.Option("all", help=_SRC_HELP),
    config_dir: Path = typer.Option(None, help=_CFG_HELP),
    output_dir: Path = typer.Option(
        None,
        help="Output directory (default: ./output)",
    ),
    api_base: str = typer.Option(None, help=_API_HELP),
    db_path: Path = typer.Option(None, help=_DB_HELP),
):
    """Fetch, filter, evaluate, and render a digest."""
    cfg = config_dir or _config_dir()
    _require_config(cfg)
    out = output_dir or Path.cwd() / "output"
    db = db_path or _db_path()

    today = str(date.today())
    today_dt = date.today()
    start = start or str(today_dt - timedelta(days=1))
    end = end or today

    profiles = load_profiles(cfg)
    conn = open_db(db)

    # scrape only profiles not yet fetched today
    needs_scrape = [
        p for p in profiles if not already_scraped(conn, p["name"], today)
    ]

    if needs_scrape:
        fetch_topics = {"arxiv_categories": _union_arxiv_cats(needs_scrape)}
        rprint(f"Fetching \\[{source}]: {start} to {end}")
        all_papers = _fetch_from_sources(start, end, source, fetch_topics)
        rprint(f"Found {len(all_papers)} papers")

        for profile in needs_scrape:
            pname = profile["name"]
            cap = profile.get("max_papers", 500)
            filtered = filter_by_topics(all_papers, profile)
            upsert_papers(conn, filtered, pname, today)
            enforce_cap(conn, pname, cap)
            log_scrape(conn, pname, today)
            rprint(f"{pname}: " f"saved {len(filtered)} papers")
    else:
        rprint("[dim]All profiles already scraped " "today — using DB[/dim]")

    sections = []
    for profile in profiles:
        pname = profile["name"]
        pnames = profile.get("personas")
        personas = [p for p in load_personas(cfg, pnames) if p.enabled]
        if not personas:
            rprint(f"[yellow]{pname}: no personas, " f"skipping[/yellow]")
            continue

        db_papers = load_papers(conn, pname)
        if not db_papers:
            rprint(f"[yellow]{pname}: no papers in " f"DB[/yellow]")
            continue

        persona_names = [p.name for p in personas]
        to_eval = papers_needing_eval(conn, db_papers, pname, persona_names)
        if to_eval:
            rprint(f"{pname}: evaluating " f"{len(to_eval)} papers")
            new_cards = asyncio.run(
                run_pipeline(to_eval, personas, model, api_base)
            )
            for card in new_cards:
                upsert_results(conn, card.paper.doi, pname, card.results)

        cards: list[PaperCard] = []
        for paper in db_papers:
            res_map = load_results(conn, paper.doi, pname)
            results = [res_map[n] for n in persona_names if n in res_map]
            if results:
                cards.append(PaperCard(paper=paper, results=results))

        cards = score_cards(cards)
        sections.append((pname, cards))

    conn.close()

    if not sections:
        rprint("[yellow]No results. Done.[/yellow]")
        raise typer.Exit()

    result = render_digest(sections, TEMPLATE_DIR, out)
    rprint(f"[green]Digest: {result}[/green]")


@app.command()
def preview(
    model: str = typer.Option(
        "openai/gpt-4o-mini",
        help="Default LLM model",
    ),
    source: str = typer.Option("all", help=_SRC_HELP),
    config_dir: Path = typer.Option(None, help=_CFG_HELP),
):
    """Show what would run without calling any LLM."""
    cfg = config_dir or _config_dir()
    _require_config(cfg)
    profiles = load_profiles(cfg)
    fetch_topics = {"arxiv_categories": _union_arxiv_cats(profiles)}
    today = date.today()
    start = str(today - timedelta(days=1))
    end = str(today)

    papers = _fetch_from_sources(start, end, source, fetch_topics)
    rprint(f"Papers fetched: {len(papers)}\n")

    for profile in profiles:
        pname = profile["name"]
        filtered = filter_by_topics(papers, profile)
        pnames = profile.get("personas")
        personas = [p for p in load_personas(cfg, pnames) if p.enabled]
        rprint(f"[bold]{pname}[/bold]")
        rprint(f"  {len(filtered)} papers  " f"· {len(personas)} personas")
        for p in personas:
            m = p.model or model
            rprint(f"    - {p.name} ({m})")
        for paper in filtered[:3]:
            rprint(f"  · {paper.title[:60]}")
        rprint("")


@app.command()
def test(
    model: str = typer.Option(
        "openai/gpt-4o-mini",
        help="Default LLM model",
    ),
    source: str = typer.Option("all", help=_SRC_HELP),
    config_dir: Path = typer.Option(None, help=_CFG_HELP),
    api_base: str = typer.Option(None, help=_API_HELP),
):
    """Run the full pipeline on a single paper."""
    cfg = config_dir or _config_dir()
    _require_config(cfg)
    profiles = load_profiles(cfg)
    fetch_topics = {"arxiv_categories": _union_arxiv_cats(profiles)}
    today = date.today()
    start = str(today - timedelta(days=1))
    end = str(today)

    papers = _fetch_from_sources(start, end, source, fetch_topics)

    matched_profile = None
    matched_papers: list[Paper] = []
    for profile in profiles:
        filtered = filter_by_topics(papers, profile)
        if filtered:
            matched_profile = profile
            matched_papers = filtered
            break

    if not matched_profile:
        rprint("[yellow]No papers to test with.[/yellow]")
        raise typer.Exit()

    pname = matched_profile["name"]
    pnames = matched_profile.get("personas")
    personas = [p for p in load_personas(cfg, pnames) if p.enabled]
    paper = matched_papers[0]

    rprint(
        f"[bold]Profile:[/bold] {pname}\n"
        f"[bold]Paper:[/bold] {paper.title}\n"
    )
    cards = asyncio.run(run_pipeline([paper], personas, model, api_base))
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
