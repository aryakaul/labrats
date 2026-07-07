"""Unified labrats web server — digest + config + run."""

import json
import logging
import re
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote

from loguru import logger

from labrats.db import (
    last_scraped,
    load_llm_summary,
    load_papers,
    load_results,
    open_db,
    paper_count,
)
from labrats.models import PaperCard
from labrats.models_registry import (
    PROVIDERS,
    discover_local,
    list_cloud_models,
)
from labrats.personas import (
    _slugify,
    delete_persona,
    load_personas,
    load_profiles,
    load_settings,
    resolve_model,
    save_persona,
    save_profiles,
    save_settings,
)
from labrats.runner import run_all_profiles, rerun_paper
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

_rerun_state = {"status": "idle", "error": None}  # idle | running | done | error


def _do_rerun_background(config_dir, db_path, model, api_base, doi, profile_name, persona_name):
    try:
        rerun_paper(config_dir, db_path, model, api_base, doi, profile_name, persona_name)
        _rerun_state.update({"status": "done", "error": None})
    except Exception as e:
        logger.exception(f"rerun failed: {e}")
        _rerun_state.update({"status": "error", "error": str(e)})


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


def _resolve_persona_image(slug: str, config_dir: Path) -> Path | None:
    """Find a persona image file, preferring the user's config dir.

    Looks for ``<config_dir>/personas/<slug>.png`` first (so users can
    drop in art for custom personas — or override bundled images),
    then falls back to the bundled ``labrats/static/personas/<slug>.png``.
    """
    user = config_dir / "personas" / f"{slug}.png"
    if user.is_file():
        return user
    bundled = STATIC_DIR / "personas" / f"{slug}.png"
    if bundled.is_file():
        return bundled
    return None


def _persona_image_url(persona_name: str, config_dir: Path) -> str:
    """Return the URL for a persona image, or '' if none exists."""
    slug = _slugify(persona_name)
    if _resolve_persona_image(slug, config_dir):
        return f"/persona-image/{slug}.png"
    return ""


def _card_to_dict(card, config_dir: Path):
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
                "image_url": _persona_image_url(
                    r.persona_name, config_dir,
                ),
            }
            for r in card.results
        ],
        "tension": card.tension,
        "avg_score": card.avg_score,
        "disputed": card.disputed,
        "disputed_field": card.disputed_field,
        "llm_summary": parse_llm_summary(card.llm_summary),
    }


def build_profile_cards(conn, config_dir: Path, profile: dict):
    """Assemble scored PaperCards for a profile from the DB."""
    pnames = profile.get("personas")
    persona_list = [
        p for p in load_personas(config_dir, pnames)
        if p.enabled
    ]
    persona_names = [p.name for p in persona_list]

    cards = []
    for paper in load_papers(conn, profile["name"]):
        res_map = load_results(conn, paper.doi, profile["name"])
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

    return score_cards(cards)


def build_digest_profiles(conn, config_dir: Path):
    """Profile list with paper counts and last scrape dates."""
    return [
        {
            "name": p["name"],
            "paper_count": paper_count(conn, p["name"]),
            "last_scraped": last_scraped(conn, p["name"]),
        }
        for p in load_profiles(config_dir)
    ]


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
    """Execute the full pipeline in a background thread, updating _run_state."""
    try:
        run_all_profiles(
            config_dir, db_path, model, api_base, source,
            on_phase=lambda p: _run_state.update({"phase": p}),
            on_profile=lambda n: _run_state.update({"profile": n}),
            on_progress=lambda done, total: _run_state.update(
                {"progress": {"completed": done, "total": total}}
            ),
        )
        _run_state["status"] = "done"
        try:
            current = load_settings(config_dir)
            current["last_run_at"] = datetime.now(timezone.utc).isoformat()
            save_settings(config_dir, current)
        except Exception:
            logger.exception("failed to record last_run_at")
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

    def _send_bytes(self, body: bytes, content_type: str,
                    status: int = 200, cache: bool = True):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if not cache:
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data, status=200):
        self._send_bytes(json.dumps(data).encode(), "application/json", status)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        return json.loads(raw) if raw else {}

    def _send_html(self, body: bytes):
        self._send_bytes(body, "text/html; charset=utf-8")

    def _send_persona_image(self, filename: str):
        """Serve a persona image — user config wins over bundled."""
        name = filename.split("?", 1)[0].split("#", 1)[0]
        if not name.endswith(".png") or "/" in name or "\\" in name:
            self.send_error(404)
            return
        slug = name[:-len(".png")]
        if not re.fullmatch(r"[a-z0-9_]+", slug):
            self.send_error(404)
            return
        fpath = _resolve_persona_image(slug, self.config_dir)
        if not fpath:
            self.send_error(404)
            return
        self._send_bytes(fpath.read_bytes(), "image/png", cache=False)

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
        mime = _STATIC_MIME.get(
            fpath.suffix.lower(), "application/octet-stream",
        )
        self._send_bytes(fpath.read_bytes(), mime, cache=False)

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
                "auto_run_hours": data.get("auto_run_hours", 20),
                "last_run_at": data.get("last_run_at", ""),
            })

        elif path == "/api/models":
            settings = load_settings(self.config_dir)
            api_keys = settings.get("api_keys", {})
            extra = settings.get("local_endpoints", [])
            cloud = list_cloud_models()
            providers = [
                {
                    "key": p["key"],
                    "label": p["label"],
                    "env": p["env"],
                    "docs": p["docs"],
                    "models": cloud.get(p["key"], []),
                    "active": bool(api_keys.get(p["key"], "")),
                }
                for p in PROVIDERS
            ]
            self._send_json({
                "providers": providers,
                "local": discover_local(extra),
            })

        elif path == "/api/digest":
            self._get_digest_profiles()

        elif path.startswith("/api/digest/"):
            profile = unquote(path[len("/api/digest/"):])
            self._get_digest_cards(profile)

        elif path == "/api/run/status":
            self._send_json(dict(_run_state))

        elif path == "/api/rerun/status":
            self._send_json(dict(_rerun_state))

        elif path.startswith("/static/"):
            self._send_static(path[len("/static/"):])

        elif path.startswith("/persona-image/"):
            self._send_persona_image(path[len("/persona-image/"):])

        elif path in ("/", ""):
            self._send_html(INDEX_HTML.read_bytes())

        else:
            self.send_error(404)

    def _get_digest_profiles(self):
        """Return profile list with paper counts and last scrape dates."""
        conn = open_db(self.db_path)
        result = build_digest_profiles(conn, self.config_dir)
        conn.close()
        self._send_json(result)

    def _get_digest_cards(self, profile_name):
        """Return scored paper cards for a profile."""
        conn = open_db(self.db_path)
        profiles = load_profiles(self.config_dir)
        profile = next(
            (p for p in profiles if p["name"] == profile_name),
            None,
        )
        if not profile:
            conn.close()
            self._send_json({"error": "unknown profile"}, 404)
            return

        cards = build_profile_cards(conn, self.config_dir, profile)
        conn.close()
        self._send_json([_card_to_dict(c, self.config_dir) for c in cards])

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

        elif self.path == "/api/rerun":
            self._start_rerun()

        else:
            self.send_error(404)

    def _start_run(self):
        """Kick off the background pipeline and return immediately."""
        if _run_state["status"] == "running":
            self._send_json({"error": "already running"}, 409)
            return
        _reset_run_state()
        model = resolve_model(self.model, self.config_dir)
        t = threading.Thread(
            target=_background_run,
            args=(self.config_dir, self.db_path, model, self.api_base, self.source),
            daemon=True,
        )
        t.start()
        self._send_json({"ok": True})

    def _start_rerun(self):
        """Kick off a single-persona rerun in the background."""
        if _rerun_state["status"] == "running":
            self._send_json({"error": "already running"}, 409)
            return
        data = self._read_body()
        doi = data.get("doi", "")
        profile_name = data.get("profile", "")
        persona_name = data.get("persona", "")
        if not doi or not profile_name or not persona_name:
            self._send_json({"error": "missing doi, profile, or persona"}, 400)
            return
        _rerun_state.update({"status": "running", "error": None})
        model = resolve_model(self.model, self.config_dir)
        t = threading.Thread(
            target=_do_rerun_background,
            args=(self.config_dir, self.db_path, model, self.api_base,
                  doi, profile_name, persona_name),
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
            if "auto_run_hours" in data:
                try:
                    current["auto_run_hours"] = max(0, int(data["auto_run_hours"]))
                except (TypeError, ValueError):
                    current["auto_run_hours"] = 0
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
