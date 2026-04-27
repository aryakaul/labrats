"""CLI entry point — labrats init, serve, models, preview, test."""

import asyncio
import os
import shutil
from datetime import date, timedelta
from pathlib import Path

import requests
import typer
from rich import print as rprint

from labrats.models import Paper, PersonaConfig
from labrats.models_registry import (
    PROVIDER_DOCS,
    PROVIDER_KEYS,
    discover_local,
    list_cloud_models,
    list_local_models,
)
from labrats.personas import (
    DEFAULT_PERSONA_PROMPT,
    load_personas,
    load_profiles,
    load_settings,
)
from labrats.pipeline import run_pipeline
from labrats.scraper import (
    fetch_from_sources,
    filter_by_topics,
    union_arxiv_cats,
)
from labrats.synthesis import score_cards

app = typer.Typer()

PACKAGE_DIR = Path(__file__).resolve().parent
BUNDLED_DEFAULTS = PACKAGE_DIR / "defaults"

_CFG_HELP = "Config directory (default: $XDG_CONFIG_HOME/labrats)"
_SRC_HELP = "Data source: biorxiv, arxiv, all"
_API_HELP = "Local API base URL, e.g. http://localhost:2276/v1"
_DB_HELP = "SQLite DB path (default: $XDG_DATA_HOME/labrats/labrats.db)"
_MDL_HELP = "Default LLM model (overrides settings.yaml)"
_FALLBACK_MODEL = "openai/gpt-4o-mini"


# ── path helpers ──


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


def _resolve_model(cli_model: str | None, config_dir: Path) -> str:
    """CLI flag > settings.yaml > fallback."""
    if cli_model:
        return cli_model
    settings = load_settings(config_dir)
    cfg_model = settings.get("default_model", "")
    return cfg_model or _FALLBACK_MODEL


# ── preflight model/key checks ──


def _bare_model(model: str) -> str:
    """'openai/gpt-4o' -> 'gpt-4o'"""
    return model.split("/", 1)[-1]


def _collect_models(
    default: str,
    personas: list[PersonaConfig],
    profiles: list[dict] | None = None,
) -> list[str]:
    """Gather every distinct model that will be used in a run."""
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
    """Verify all models are reachable before starting a run."""
    models = _collect_models(model, personas, profiles)
    settings = load_settings(config_dir)
    api_keys = settings.get("api_keys", {})

    if api_base:
        # Local endpoint — verify the model is loaded
        try:
            resp = requests.get(f"{api_base}/models", timeout=5)
            resp.raise_for_status()
            available = {m["id"] for m in resp.json().get("data", [])}
        except Exception as e:
            rprint(f"[red]Cannot reach {api_base}/models[/red]\n  {e}")
            raise typer.Exit(1)
        for m in models:
            bare = _bare_model(m)
            if bare not in available:
                avail_str = "\n  ".join(sorted(available))
                rprint(
                    f"[red]Model not available: {bare}[/red]\n"
                    f"Available models:\n  {avail_str}"
                )
                raise typer.Exit(1)
        return

    # Cloud — verify API keys are set
    cfg_path = config_dir / "settings.yaml"
    missing = []
    for m in models:
        provider = m.split("/", 1)[0]
        env_var = PROVIDER_KEYS.get(provider)
        if not env_var:
            continue
        cfg_key = api_keys.get(provider, "")
        if cfg_key:
            os.environ[env_var] = cfg_key
        elif not os.environ.get(env_var):
            missing.append((m, provider, env_var))

    if missing:
        lines = "\n  ".join(f"{m} -> ${var}" for m, _, var in missing)
        rprint(
            f"[red]Missing API keys:[/red]\n  {lines}\n"
            f"\nSet via {cfg_path} or environment variable."
            f"\nRun [bold]labrats serve[/bold] to manage keys in the browser."
        )
        raise typer.Exit(1)


def _fetch_and_warn(start, end, source, topics):
    """Wrapper around fetch_from_sources that prints warnings via Rich."""
    papers, errors = fetch_from_sources(start, end, source, topics)
    for err in errors:
        rprint(f"[yellow]Warning — {err}[/yellow]")
    if not papers and errors:
        rprint("[red]All sources failed.[/red]")
        raise typer.Exit(1)
    return papers


# ── commands ──


@app.command()
def init(
    config_dir: Path = typer.Option(None, help=_CFG_HELP),
    db_path: Path = typer.Option(None, help=_DB_HELP),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files and clear the database"),
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
        rprint(f"  [yellow]skipped[/yellow]  {f}  (--force to overwrite)")
    rprint(f"\nConfig dir: {target}")

    if force:
        db = db_path or _db_path()
        if db.exists():
            db.unlink()
            rprint(f"  [green]cleared[/green]   database  {db}")
        else:
            rprint(f"  [dim]no database at {db}[/dim]")


@app.command()
def models(
    config_dir: Path = typer.Option(None, help=_CFG_HELP),
    api_base: str = typer.Option(None, help=_API_HELP),
):
    """List available LLM models (cloud + local)."""
    cfg = config_dir or _config_dir()
    settings = load_settings(cfg)
    api_keys = settings.get("api_keys", {})
    extra = settings.get("local_endpoints", [])

    # Explicit endpoint — just show that one
    if api_base:
        try:
            local = list_local_models(api_base)
        except Exception as e:
            rprint(f"[red]Cannot reach {api_base}:[/red] {e}")
            raise typer.Exit(1)
        rprint(f"[bold]Local ({api_base}):[/bold]")
        for m in local:
            rprint(f"  {m}")
        if not local:
            rprint("  (none loaded)")
        return

    # Auto-discover local endpoints
    local_results = discover_local(extra)
    if local_results:
        rprint("[bold]Local models:[/bold]")
        for label, ms in local_results.items():
            rprint(f"\n  [green]{label}[/green]")
            for m in ms:
                rprint(f"    {m}")

    # Cloud models grouped by provider
    cloud = list_cloud_models()
    active, inactive = [], []
    for provider in sorted(cloud.keys()):
        cfg_key = api_keys.get(provider, "")
        env_var = PROVIDER_KEYS.get(provider, "")
        has_key = bool(cfg_key) or bool(os.environ.get(env_var))
        (active if has_key else inactive).append(provider)

    if active:
        rprint("\n[bold]Cloud providers:[/bold]")
        for p in active:
            ms = cloud.get(p, [])
            doc = PROVIDER_DOCS.get(p, "")
            rprint(f"\n  [green]{p}[/green] ({len(ms)} models)")
            if doc:
                rprint(f"  {doc}")
            for m in ms:
                rprint(f"    {m}")
    elif not local_results:
        rprint("[yellow]No active providers.[/yellow]")
        rprint(
            "Configure API keys via "
            "[bold]labrats serve[/bold] or settings.yaml."
        )

    if inactive:
        rprint("\n[dim]Inactive (no key):[/dim]")
        for p in inactive:
            doc = PROVIDER_DOCS.get(p, "")
            hint = f"  {doc}" if doc else ""
            rprint(f"  [dim]{p}{hint}[/dim]")


@app.command()
def serve(
    config_dir: Path = typer.Option(None, help=_CFG_HELP),
    port: int = typer.Option(8485, help="Port for the web server"),
    db_path: Path = typer.Option(None, help=_DB_HELP),
    model: str = typer.Option(None, help=_MDL_HELP),
    api_base: str = typer.Option(None, help=_API_HELP),
    source: str = typer.Option("all", help=_SRC_HELP),
):
    """Launch the unified web UI (digest + config + run)."""
    from labrats.serve import serve as _serve

    cfg = config_dir or _config_dir()
    _require_config(cfg)
    mdl = _resolve_model(model, cfg)
    db = db_path or _db_path()
    _serve(cfg, port, db, mdl, api_base, source)


@app.command()
def preview(
    model: str = typer.Option(None, help=_MDL_HELP),
    source: str = typer.Option("all", help=_SRC_HELP),
    config_dir: Path = typer.Option(None, help=_CFG_HELP),
):
    """Show what would run without calling any LLM."""
    cfg = config_dir or _config_dir()
    _require_config(cfg)
    model = _resolve_model(model, cfg)
    profiles = load_profiles(cfg)
    fetch_topics = {"arxiv_categories": union_arxiv_cats(profiles)}
    today = date.today()
    start = str(today - timedelta(days=1))
    end = str(today)

    papers = _fetch_and_warn(start, end, source, fetch_topics)
    rprint(f"Papers fetched: {len(papers)}\n")

    for profile in profiles:
        pname = profile["name"]
        filtered = filter_by_topics(papers, profile)
        pnames = profile.get("personas")
        personas = [p for p in load_personas(cfg, pnames) if p.enabled]
        rprint(f"[bold]{pname}[/bold]")
        rprint(f"  {len(filtered)} papers  · {len(personas)} personas")
        for p in personas:
            m = p.model or model
            rprint(f"    - {p.name} ({m})")
        for paper in filtered[:3]:
            rprint(f"  · {paper.title[:60]}")
        rprint("")


@app.command()
def test(
    model: str = typer.Option(None, help=_MDL_HELP),
    source: str = typer.Option("all", help=_SRC_HELP),
    config_dir: Path = typer.Option(None, help=_CFG_HELP),
    api_base: str = typer.Option(None, help=_API_HELP),
):
    """Run the full pipeline on a single paper (smoke test)."""
    cfg = config_dir or _config_dir()
    _require_config(cfg)
    model = _resolve_model(model, cfg)
    profiles = load_profiles(cfg)

    # Preflight: verify model access before scraping
    all_personas = []
    for p in profiles:
        pnames = p.get("personas")
        all_personas += [x for x in load_personas(cfg, pnames) if x.enabled]
    _preflight_check(model, all_personas, api_base, cfg, profiles)

    fetch_topics = {"arxiv_categories": union_arxiv_cats(profiles)}
    today = date.today()
    start = str(today - timedelta(days=1))
    end = str(today)

    papers = _fetch_and_warn(start, end, source, fetch_topics)

    # Find first profile with matching papers
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
    persona_prompt = (
        matched_profile.get("persona_prompt") or DEFAULT_PERSONA_PROMPT
    )
    paper = matched_papers[0]

    rprint(
        f"[bold]Profile:[/bold] {pname}\n"
        f"[bold]Paper:[/bold] {paper.title}\n"
    )
    cards = asyncio.run(
        run_pipeline(
            [paper], personas, persona_prompt, effective_model, api_base,
        )
    )
    cards = score_cards(cards)
    card = cards[0]

    for r in card.results:
        rprint(f"[bold]{r.persona_name}[/bold]")
        rprint(f"  Scores: {r.scores}")
        rprint(f"  {r.summary}\n")
    rprint(
        f"Tension: {card.tension:.2f}  "
        f"Score: {card.avg_score:.2f}"
    )
