"""Unified labrats web server — digest + config + run."""

import asyncio
import json
import os
import threading
import webbrowser
from datetime import date, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote

import yaml
from jinja2 import Environment, FileSystemLoader

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
from labrats.models import PaperCard
from labrats.models_registry import (
    PROVIDER_DOCS,
    PROVIDER_KEYS,
    discover_local,
    list_cloud_models,
)
from labrats.personas import (
    delete_persona,
    load_personas,
    load_profiles,
    load_settings,
    save_persona,
    save_profiles,
    save_settings,
)
from labrats.pipeline import run_pipeline_headless
from labrats.scraper import (
    fetch_from_sources,
    filter_by_topics,
    union_arxiv_cats,
)
from labrats.synthesis import score_cards

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


# ── background run state ──
# Shared mutable dict protected by _run_lock. The web UI polls
# GET /api/run/status to read this state and update the progress bar.

_run_lock = threading.Lock()
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
    """Read persona YAML files and return as plain dicts for the API."""
    persona_dir = config_dir / "personas"
    result = []
    for path in sorted(persona_dir.glob("*.yaml")):
        with open(path) as f:
            data = yaml.safe_load(f)
        result.append({
            "stem": path.stem,
            "name": data["name"],
            "role": data["role"],
            "prompt": data["prompt"],
            "scored_fields": data["scored_fields"],
            "model": data.get("model", ""),
            "enabled": data.get("enabled", True),
        })
    return result


def _card_to_dict(card):
    """Serialize a PaperCard to a JSON-safe dict for the digest API."""
    p = card.paper
    return {
        "paper": {
            "doi": p.doi,
            "title": p.title,
            "authors": p.authors,
            "author_corresponding": p.author_corresponding,
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
        "interestingness": card.interestingness,
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
        today = str(date.today())
        today_dt = date.today()
        start = str(today_dt - timedelta(days=1))
        end = today

        profiles = load_profiles(config_dir)
        conn = open_db(db_path)
        conn.execute("PRAGMA journal_mode=WAL")

        # Only scrape profiles that haven't been fetched today
        needs_scrape = [
            p for p in profiles
            if not already_scraped(conn, p["name"], today)
        ]

        if needs_scrape:
            fetch_topics = {"arxiv_categories": union_arxiv_cats(needs_scrape)}
            all_papers, _errors = fetch_from_sources(
                start, end, source, fetch_topics,
            )
            for profile in needs_scrape:
                pname = profile["name"]
                cap = profile.get("max_papers", 500)
                filtered = filter_by_topics(all_papers, profile)
                upsert_papers(conn, filtered, pname, today)
                enforce_cap(conn, pname, cap)
                log_scrape(conn, pname, today)

        # Evaluate each profile's papers with its personas
        _run_state["phase"] = "evaluating"

        for profile in profiles:
            pname = profile["name"]
            _run_state["profile"] = pname
            pnames = profile.get("personas")
            personas = [
                p for p in load_personas(config_dir, pnames)
                if p.enabled
            ]
            if not personas:
                continue

            db_papers = load_papers(conn, pname)
            if not db_papers:
                continue

            persona_names = [p.name for p in personas]
            effective_model = profile.get("model") or model
            to_eval = papers_needing_eval(
                conn, db_papers, pname, persona_names,
            )
            if not to_eval:
                continue

            _run_state["progress"] = {"completed": 0, "total": len(to_eval)}

            def on_progress(done, total):
                _run_state["progress"] = {"completed": done, "total": total}

            new_cards = asyncio.run(
                run_pipeline_headless(
                    to_eval, personas, effective_model,
                    api_base, on_progress,
                )
            )
            for card in new_cards:
                upsert_results(conn, card.paper.doi, pname, card.results)

        conn.close()
        _run_state["phase"] = "done"
        _run_state["status"] = "done"

    except Exception as e:
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
        pass  # silence per-request logs

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

    def _send_html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
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

        elif path in ("/", ""):
            env = Environment(
                loader=FileSystemLoader(str(TEMPLATE_DIR)),
                autoescape=True,
            )
            tmpl = env.get_template("serve.html.j2")
            self._send_html(tmpl.render())

        else:
            self.send_error(404)

    def _get_digest_profiles(self):
        """Return profile list with paper counts and last scrape dates."""
        conn = open_db(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        profiles = load_profiles(self.config_dir)
        result = []
        for p in profiles:
            pname = p["name"]
            count = conn.execute(
                "SELECT COUNT(*) FROM papers WHERE profile = ?",
                (pname,),
            ).fetchone()[0]
            row = conn.execute(
                "SELECT scraped_on FROM scrape_log "
                "WHERE profile = ? ORDER BY scraped_on DESC LIMIT 1",
                (pname,),
            ).fetchone()
            result.append({
                "name": pname,
                "paper_count": count,
                "last_scraped": row[0] if row else None,
            })
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
                cards.append(PaperCard(paper=paper, results=results))

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
        with _run_lock:
            if _run_state["status"] == "running":
                self._send_json({"error": "already running"}, 409)
                return
            _reset_run_state()

        # Resolve model: CLI flag > settings.yaml > fallback
        settings = load_settings(self.config_dir)
        cfg_model = settings.get("default_model", "")
        model = self.model or cfg_model or "openai/gpt-4o-mini"

        # Inject saved API keys into environment for litellm
        api_keys = settings.get("api_keys", {})
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
    ServeHandler.config_dir = config_dir
    ServeHandler.db_path = db_path
    ServeHandler.model = model
    ServeHandler.api_base = api_base
    ServeHandler.source = source
    server = HTTPServer(("127.0.0.1", port), ServeHandler)
    url = f"http://127.0.0.1:{port}"
    print(f"labrats → {url}")
    print("Press Ctrl+C to stop.")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
