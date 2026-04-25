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
import requests

from labrats.models import Paper, PaperCard, PersonaConfig
from labrats.models_registry import (
    PROVIDER_DOCS,
    discover_local,
    list_cloud_models,
    list_local_models,
)
from labrats.personas import load_personas, load_profiles, load_settings
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


_PROVIDER_KEYS = {
	"openai": "OPENAI_API_KEY",
	"anthropic": "ANTHROPIC_API_KEY",
	"gemini": "GOOGLE_API_KEY",
	"google": "GOOGLE_API_KEY",
	"groq": "GROQ_API_KEY",
	"together_ai": "TOGETHERAI_API_KEY",
	"mistral": "MISTRAL_API_KEY",
	"cohere": "COHERE_API_KEY",
}


def _bare_model(model: str) -> str:
	"""Strip provider prefix: 'openai/gpt-4o' -> 'gpt-4o'."""
	return model.split("/", 1)[-1]


def _collect_models(
	default: str,
	personas: list[PersonaConfig],
	profiles: list[dict] | None = None,
) -> list[str]:
	models = {default}
	if profiles:
		for p in profiles:
			if p.get("model"):
				models.add(p["model"])
	for p in personas:
		if p.model:
			models.add(p.model)
	return list(models)


def _preflight_check(
	model: str,
	personas: list[PersonaConfig],
	api_base: str | None,
	config_dir: Path,
	profiles: list[dict] | None = None,
) -> None:
	models = _collect_models(model, personas, profiles)
	settings = load_settings(config_dir)
	api_keys = settings.get("api_keys", {})

	if api_base:
		# local endpoint — check model availability
		try:
			resp = requests.get(
				f"{api_base}/models", timeout=5,
			)
			resp.raise_for_status()
			data = resp.json()
			available = {
				m["id"] for m in data.get("data", [])
			}
		except Exception as e:
			rprint(
				f"[red]Cannot reach local endpoint: "
				f"{api_base}/models[/red]\n  {e}"
			)
			raise typer.Exit(1)

		for m in models:
			bare = _bare_model(m)
			if bare not in available:
				avail_str = "\n  ".join(sorted(available))
				rprint(
					f"[red]Model not available: "
					f"{bare}[/red]\n"
					f"Available models:\n  {avail_str}"
				)
				raise typer.Exit(1)
		return

	# cloud — check API keys
	cfg_path = config_dir / "settings.yaml"
	missing = []
	for m in models:
		provider = m.split("/", 1)[0]
		env_var = _PROVIDER_KEYS.get(provider)
		if not env_var:
			continue
		cfg_key = api_keys.get(provider, "")
		if cfg_key:
			os.environ[env_var] = cfg_key
		elif not os.environ.get(env_var):
			missing.append((m, provider, env_var))

	if missing:
		lines = "\n  ".join(
			f"{m} -> ${var}"
			for m, _, var in missing
		)
		rprint(
			f"[red]Missing API keys:[/red]\n  {lines}\n"
			f"\nSet via {cfg_path} or environment variable."
			f"\nRun [bold]labrats config[/bold] to manage "
			f"keys in the browser."
		)
		raise typer.Exit(1)


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
            if rel.name == "settings.yaml":
                dst.chmod(0o600)
            copied.append(str(rel))

    for f in copied:
        rprint(f"  [green]created[/green]  {f}")
    for f in skipped:
        rprint(f"  [yellow]skipped[/yellow]  {f}" "  (--force to overwrite)")
    rprint(f"\nConfig dir: {target}")


@app.command()
def models(
    config_dir: Path = typer.Option(None, help=_CFG_HELP),
    api_base: str = typer.Option(None, help=_API_HELP),
):
    """List available LLM models."""
    cfg = config_dir or _config_dir()
    settings = load_settings(cfg)
    api_keys = settings.get("api_keys", {})
    extra = settings.get("local_endpoints", [])

    # ── local models ──
    if api_base:
        # explicit endpoint — just show that one
        try:
            local = list_local_models(api_base)
        except Exception as e:
            rprint(
                f"[red]Cannot reach {api_base}:"
                f"[/red] {e}"
            )
            raise typer.Exit(1)
        rprint(f"[bold]Local ({api_base}):[/bold]")
        for m in local:
            rprint(f"  {m}")
        if not local:
            rprint("  (none loaded)")
        return

    # auto-discover local endpoints
    local_results = discover_local(extra)
    if local_results:
        rprint("[bold]Local models:[/bold]")
        for label, ms in local_results.items():
            rprint(f"\n  [green]{label}[/green]")
            for m in ms:
                rprint(f"    {m}")

    # ── cloud models ──
    cloud = list_cloud_models()
    active = []
    inactive = []
    for provider in sorted(cloud.keys()):
        cfg_key = api_keys.get(provider, "")
        env_var = _PROVIDER_KEYS.get(provider, "")
        has_key = bool(cfg_key) or bool(
            os.environ.get(env_var)
        )
        if has_key:
            active.append(provider)
        else:
            inactive.append(provider)

    if active:
        rprint("\n[bold]Cloud providers:[/bold]")
        for p in active:
            ms = cloud.get(p, [])
            doc = PROVIDER_DOCS.get(p, "")
            rprint(
                f"\n  [green]{p}[/green] "
                f"({len(ms)} models)"
            )
            if doc:
                rprint(f"  {doc}")
            for m in ms:
                rprint(f"    {m}")
    elif not local_results:
        rprint("[yellow]No active providers.[/yellow]")
        rprint(
            "Configure API keys via "
            "[bold]labrats config[/bold] or "
            "settings.yaml."
        )

    if inactive:
        rprint("\n[dim]Inactive (no key):[/dim]")
        for p in inactive:
            doc = PROVIDER_DOCS.get(p, "")
            hint = f"  {doc}" if doc else ""
            rprint(f"  [dim]{p}{hint}[/dim]")


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

    # preflight: verify model access before any work
    all_personas = []
    for p in profiles:
        pnames = p.get("personas")
        all_personas += [
            x for x in load_personas(cfg, pnames) if x.enabled
        ]
    _preflight_check(model, all_personas, api_base, cfg, profiles)

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
        effective_model = profile.get("model") or model
        to_eval = papers_needing_eval(conn, db_papers, pname, persona_names)
        if to_eval:
            rprint(f"{pname}: evaluating " f"{len(to_eval)} papers")
            new_cards = asyncio.run(
                run_pipeline(
                    to_eval, personas, effective_model, api_base,
                )
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

    # preflight: verify model access before scraping
    all_personas = []
    for p in profiles:
        pnames = p.get("personas")
        all_personas += [
            x for x in load_personas(cfg, pnames) if x.enabled
        ]
    _preflight_check(model, all_personas, api_base, cfg, profiles)

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
    effective_model = matched_profile.get("model") or model
    paper = matched_papers[0]

    rprint(
        f"[bold]Profile:[/bold] {pname}\n"
        f"[bold]Paper:[/bold] {paper.title}\n"
    )
    cards = asyncio.run(
        run_pipeline([paper], personas, effective_model, api_base)
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
