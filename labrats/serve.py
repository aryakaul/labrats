"""Unified labrats web server — digest + config + run."""

import asyncio
import json
import logging
import os
import threading
import webbrowser
from datetime import date, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote

from loguru import logger

from labrats.db import (
    already_scraped,
    enforce_cap,
    last_scraped,
    load_llm_summary,
    load_papers,
    load_results,
    log_scrape,
    open_db,
    papers_needing_eval,
    paper_count,
    upsert_llm_summary,
    upsert_papers,
    upsert_results,
)
from labrats.models import PaperCard
from labrats.models_registry import (
    PROVIDER_DOCS,
    PROVIDER_KEYS,
    discover_local,
    list_cloud_models,
)
from labrats.personas import (
    DEFAULT_PERSONA_PROMPT,
    delete_persona,
    load_personas,
    load_profiles,
    load_settings,
    resolve_model,
    save_persona,
    save_profiles,
    save_settings,
)
from labrats.pipeline import run_pipeline
from labrats.scraper import (
    fetch_from_sources,
    filter_by_topics,
    union_arxiv_cats,
)
from labrats.synthesis import parse_llm_summary, score_cards

PACKAGE_DIR = Path(__file__).resolve().parent
INDEX_HTML = PACKAGE_DIR / "templates" / "serve.html"
STATIC_DIR = PACKAGE_DIR / "static"

_STATIC_MIME = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


# ── background run state ──
# Single writer (the background thread) + single reader pattern (the
# polling /api/run/status endpoint reads dict snapshots). Status
# transitions are checked in _start_run before spawning a thread.

_run_state = {
    "status": "idle",       # idle | running | done | error
    "phase": "",            # scraping | evaluating | done
    "profile": "",          # which profile is being evaluated
    "progress": {"completed": 0, "total": 0},
    "error": None,
}


def _reset_run_state():
    _run_state.update(
        status="running", phase="", profile="",
        progress={"completed": 0, "total": 0}, error=None,
    )


# ── serialization helpers ──


def _list_personas_raw(config_dir):
    """Return all personas as JSON-safe dicts for the API."""
    return [
        {
            "stem": p.stem,
            "name": p.name,
            "role": p.role,
            "scored_fields": p.scored_fields,
            "model": p.model or "",
            "enabled": p.enabled,
        }
        for p in load_personas(config_dir)
    ]


def _card_to_dict(card):
    """Serialize a PaperCard to a JSON-safe dict for the digest API."""
    p = card.paper
    return {
        "paper": {
            "doi": p.doi,
            "title": p.title,
            "authors": p.authors,
            "abstract": p.abstract,
            "category": p.category,
            "date": p.date,
            "url": p.url,
        },
        "results": [
            {
                "persona_name": r.persona_name,
                "scores": r.scores,
                "summary": r.summary,
                "model": r.model,
            }
            for r in card.results
        ],
        "tension": card.tension,
        "avg_score": card.avg_score,
        "disputed": card.disputed,
        "disputed_field": card.disputed_field,
        "llm_summary": parse_llm_summary(card.llm_summary),
    }


def _mask_api_keys(keys: dict) -> dict:
    """Partially mask API key values for safe display."""
    masked = {}
    for k, v in keys.items():
        if v and len(v) > 8:
            masked[k] = v[:4] + "*" * (len(v) - 8) + v[-4:]
        elif v:
            masked[k] = "****"
        else:
            masked[k] = ""
    return masked


# ── background pipeline run ──


def _background_run(config_dir, db_path, model, api_base, source):
    """Execute the full scrape-and-evaluate pipeline in a background thread.

    Updates _run_state as it progresses so the UI can poll for status.
    """
    try:
        _run_state["phase"] = "scraping"
        today_dt = date.today()
        today = str(today_dt)
        start = str(today_dt - timedelta(days=1))
        week_start = str(today_dt - timedelta(days=7))
        end = today

        profiles = load_profiles(config_dir)
        conn = open_db(db_path)
        conn.execute("PRAGMA journal_mode=WAL")

        # Only scrape profiles that haven't been fetched today
        needs_scrape = [
            p for p in profiles
            if not already_scraped(conn, p["name"], today)
        ]

        if not needs_scrape:
            logger.info("scraping: all profiles already scraped today")
        else:
            # First-time profiles (no papers in DB) get a full week;
            # returning profiles scrape from their last run to today.
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
                logger.info(
                    f"scraping: {len(returning)} returning profile(s): {names}"
                )

            # Use the earliest last-scrape across returning profiles
            # so no gap is missed; fall back to yesterday if unknown.
            if returning:
                last_dates = [
                    last_scraped(conn, p["name"]) for p in returning
                ]
                returning_start = min(
                    (d for d in last_dates if d), default=start,
                )
            else:
                returning_start = start

            for group, fetch_start in (
                (first_time, week_start),
                (returning, returning_start),
            ):
                if not group:
                    continue
                fetch_topics = {
                    "arxiv_categories": union_arxiv_cats(group),
                }
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

        # Evaluate each profile's papers with its personas
        _run_state["phase"] = "evaluating"
        logger.info("─" * 48)

        for profile in profiles:
            pname = profile["name"]
            _run_state["profile"] = pname
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
            effective_model = profile.get("model") or model
            persona_prompt = (
                profile.get("persona_prompt") or DEFAULT_PERSONA_PROMPT
            )
            purpose = profile.get("purpose", "")
            to_eval = papers_needing_eval(
                conn, db_papers, pname, persona_names,
            )
            if not to_eval:
                logger.info(
                    f"  {pname}: all {len(db_papers)} papers already evaluated"
                )
                continue

            logger.info(
                f"  {pname}: evaluating {len(to_eval)}/{len(db_papers)}"
                f" papers  ×  {len(personas)} personas  [{effective_model}]"
            )
            _run_state["progress"] = {"completed": 0, "total": len(to_eval)}

            def on_progress(done, total, _pname=pname):
                _run_state["progress"] = {"completed": done, "total": total}
                paper_title = to_eval[done - 1].title
                logger.info(f"    [{done}/{total}] {paper_title[:72]}")

            new_cards = asyncio.run(
                run_pipeline(
                    to_eval, personas, persona_prompt,
                    effective_model, api_base, on_progress,
                    purpose=purpose,
                )
            )
            for card in new_cards:
                upsert_results(conn, card.paper.doi, pname, card.results)
                if card.llm_summary:
                    upsert_llm_summary(conn, card.paper.doi, card.llm_summary)

            logger.info(f"  {pname}: done")

        conn.close()
        logger.info("─" * 48)
        logger.info("run complete")
        _run_state["phase"] = "done"
        _run_state["status"] = "done"

    except Exception as e:
        logger.exception(f"run failed: {e}")
        _run_state["status"] = "error"
        _run_state["error"] = str(e)


# ── HTTP handler ──


class ServeHandler(BaseHTTPRequestHandler):
    """Handles all API routes and serves the single-page web UI."""

    # Set by serve() before the server starts
    config_dir: Path = Path()
    db_path: Path = Path()
    model: str = ""
    api_base: str | None = None
    source: str = "all"

    def log_message(self, fmt, *args):
        logger.debug(fmt % args if args else fmt)

    # ── response helpers ──

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        return json.loads(raw) if raw else {}

    def _send_html(self, body: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, rel_path: str):
        """Serve a file from labrats/static/ with no-cache headers."""
        # strip query string, then resolve and confine to STATIC_DIR
        rel = rel_path.split("?", 1)[0].split("#", 1)[0]
        try:
            fpath = (STATIC_DIR / rel).resolve()
            fpath.relative_to(STATIC_DIR.resolve())
        except (ValueError, OSError):
            self.send_error(404)
            return
        if not fpath.is_file():
            self.send_error(404)
            return
        body = fpath.read_bytes()
        mime = _STATIC_MIME.get(
            fpath.suffix.lower(), "application/octet-stream",
        )
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # ── GET ──

    def do_GET(self):
        path = self.path

        if path == "/api/personas":
            self._send_json(_list_personas_raw(self.config_dir))

        elif path == "/api/profiles":
            self._send_json(load_profiles(self.config_dir))

        elif path == "/api/settings":
            data = load_settings(self.config_dir)
            self._send_json({
                "default_model": data.get("default_model", ""),
                "api_keys": _mask_api_keys(data.get("api_keys", {})),
            })

        elif path == "/api/models":
            settings = load_settings(self.config_dir)
            api_keys = settings.get("api_keys", {})
            extra = settings.get("local_endpoints", [])
            cloud = list_cloud_models()
            result = {"cloud": {}, "local": {}}
            for provider, models in cloud.items():
                has_key = bool(api_keys.get(provider, ""))
                result["cloud"][provider] = {
                    "models": models,
                    "active": has_key,
                    "docs": PROVIDER_DOCS.get(provider, ""),
                }
            result["local"] = discover_local(extra)
            self._send_json(result)

        elif path == "/api/digest":
            self._get_digest_profiles()

        elif path.startswith("/api/digest/"):
            profile = unquote(path[len("/api/digest/"):])
            self._get_digest_cards(profile)

        elif path == "/api/run/status":
            self._send_json(dict(_run_state))

        elif path.startswith("/static/"):
            self._send_static(path[len("/static/"):])

        elif path in ("/", ""):
            self._send_html(INDEX_HTML.read_bytes())

        else:
            self.send_error(404)

    def _get_digest_profiles(self):
        """Return profile list with paper counts and last scrape dates."""
        conn = open_db(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        profiles = load_profiles(self.config_dir)
        result = [
            {
                "name": p["name"],
                "paper_count": paper_count(conn, p["name"]),
                "last_scraped": last_scraped(conn, p["name"]),
            }
            for p in profiles
        ]
        conn.close()
        self._send_json(result)

    def _get_digest_cards(self, profile_name):
        """Return scored paper cards for a profile."""
        conn = open_db(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        profiles = load_profiles(self.config_dir)
        profile = next(
            (p for p in profiles if p["name"] == profile_name),
            None,
        )
        if not profile:
            conn.close()
            self._send_json({"error": "unknown profile"}, 404)
            return

        pnames = profile.get("personas")
        persona_list = [
            p for p in load_personas(self.config_dir, pnames)
            if p.enabled
        ]
        persona_names = [p.name for p in persona_list]

        db_papers = load_papers(conn, profile_name)
        cards = []
        for paper in db_papers:
            res_map = load_results(conn, paper.doi, profile_name)
            results = [res_map[n] for n in persona_names if n in res_map]
            if results:
                llm_summary = load_llm_summary(conn, paper.doi)
                cards.append(
                    PaperCard(
                        paper=paper,
                        results=results,
                        llm_summary=llm_summary,
                    )
                )

        cards = score_cards(cards)
        conn.close()
        self._send_json([_card_to_dict(c) for c in cards])

    # ── POST ──

    def do_POST(self):
        if self.path == "/api/personas":
            data = self._read_body()
            save_persona(self.config_dir, data)
            self._send_json(_list_personas_raw(self.config_dir))

        elif self.path == "/api/profiles":
            data = self._read_body()
            profiles = load_profiles(self.config_dir)
            profiles.append(data)
            save_profiles(self.config_dir, profiles)
            self._send_json(profiles)

        elif self.path == "/api/run":
            self._start_run()

        else:
            self.send_error(404)

    def _start_run(self):
        """Kick off the background pipeline and return immediately."""
        if _run_state["status"] == "running":
            self._send_json({"error": "already running"}, 409)
            return
        _reset_run_state()

        model = resolve_model(self.model, self.config_dir)

        # Inject saved API keys into environment for litellm
        api_keys = load_settings(self.config_dir).get("api_keys", {})
        for provider, env_var in PROVIDER_KEYS.items():
            val = api_keys.get(provider, "")
            if val and not os.environ.get(env_var):
                os.environ[env_var] = val

        t = threading.Thread(
            target=_background_run,
            args=(
                self.config_dir, self.db_path,
                model, self.api_base, self.source,
            ),
            daemon=True,
        )
        t.start()
        self._send_json({"ok": True})

    # ── PUT ──

    def do_PUT(self):
        if self.path == "/api/settings":
            data = self._read_body()
            new_keys = data.get("api_keys", {})
            current = load_settings(self.config_dir)
            cur_keys = current.get("api_keys", {})
            # Only update keys that aren't masked placeholder values
            for k, v in new_keys.items():
                if v and "*" not in v:
                    cur_keys[k] = v
                elif not v:
                    cur_keys[k] = ""
            current["api_keys"] = cur_keys
            if "default_model" in data:
                current["default_model"] = data["default_model"]
            save_settings(self.config_dir, current)
            self._send_json({"ok": True})
            return

        parts = self.path.split("/")
        if len(parts) == 4 and parts[1:3] == ["api", "personas"]:
            stem = parts[3]
            data = self._read_body()
            save_persona(self.config_dir, data, stem)
            self._send_json(_list_personas_raw(self.config_dir))

        elif len(parts) == 4 and parts[1:3] == ["api", "profiles"]:
            idx = int(parts[3])
            data = self._read_body()
            profiles = load_profiles(self.config_dir)
            if 0 <= idx < len(profiles):
                profiles[idx] = data
                save_profiles(self.config_dir, profiles)
            self._send_json(profiles)

        else:
            self.send_error(404)

    # ── DELETE ──

    def do_DELETE(self):
        parts = self.path.split("/")
        if len(parts) == 4 and parts[1:3] == ["api", "personas"]:
            stem = parts[3]
            delete_persona(self.config_dir, stem)
            self._send_json(_list_personas_raw(self.config_dir))

        elif len(parts) == 4 and parts[1:3] == ["api", "profiles"]:
            idx = int(parts[3])
            profiles = load_profiles(self.config_dir)
            if 0 <= idx < len(profiles):
                profiles.pop(idx)
                save_profiles(self.config_dir, profiles)
            self._send_json(profiles)

        else:
            self.send_error(404)


def serve(
    config_dir: Path,
    port: int = 8485,
    db_path: Path = Path(),
    model: str = "",
    api_base: str | None = None,
    source: str = "all",
):
    """Start the HTTP server and open the browser."""
    # suppress noisy third-party stdlib loggers
    for noisy in ("httpx", "httpcore", "litellm", "urllib3", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    ServeHandler.config_dir = config_dir
    ServeHandler.db_path = db_path
    ServeHandler.model = model
    ServeHandler.api_base = api_base
    ServeHandler.source = source
    server = HTTPServer(("127.0.0.1", port), ServeHandler)
    url = f"http://127.0.0.1:{port}"
    logger.info(f"labrats → {url}")
    logger.info(f"source: {source}  |  model: {model or 'from settings'}")
    logger.info(f"config: {config_dir}")
    logger.info(f"db:     {db_path}")
    logger.info("press Ctrl+C to stop")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("stopped")
    finally:
        server.server_close()
