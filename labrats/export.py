"""Render the digest into a static site directory (no server)."""

import json
import shutil
from pathlib import Path

from labrats.db import last_scraped, open_db, paper_count
from labrats.personas import load_profiles
from labrats.serve import (
    INDEX_HTML,
    STATIC_DIR,
    _card_to_dict,
    _resolve_persona_image,
    build_profile_cards,
)

_IMG_PREFIX = "/persona-image/"
_APP_JS_TAG = '<script src="app.js">'
_FAVICON_SLUG = "excited_grad_student"


def export_site(config_dir: Path, db_path: Path, out_dir: Path) -> Path:
    """Write a self-contained static digest site to out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "personas").mkdir(exist_ok=True)

    conn = open_db(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    profiles = load_profiles(config_dir)

    digest_profiles = [
        {
            "name": p["name"],
            "paper_count": paper_count(conn, p["name"]),
            "last_scraped": last_scraped(conn, p["name"]),
        }
        for p in profiles
    ]

    slugs = {_FAVICON_SLUG}
    cards_by_profile = {}
    for profile in profiles:
        cards = build_profile_cards(conn, config_dir, profile)
        dicts = [_card_to_dict(c, config_dir) for c in cards]
        for card in dicts:
            for result in card["results"]:
                url = result["image_url"]
                if url.startswith(_IMG_PREFIX):
                    name = url[len(_IMG_PREFIX):]
                    result["image_url"] = f"personas/{name}"
                    slugs.add(Path(name).stem)
        cards_by_profile[profile["name"]] = dicts
    conn.close()

    for asset in ("app.css", "app.js"):
        shutil.copy2(STATIC_DIR / asset, out_dir / asset)
    for slug in slugs:
        src = _resolve_persona_image(slug, config_dir)
        if src:
            shutil.copy2(src, out_dir / "personas" / f"{slug}.png")

    payload = json.dumps(
        {"profiles": digest_profiles, "cards": cards_by_profile}
    ).replace("</", "<\\/")
    blob = f"<script>window.__DIGEST__ = {payload};</script>\n"

    html = INDEX_HTML.read_text().replace("/static/", "")
    html = html.replace(_APP_JS_TAG, blob + _APP_JS_TAG, 1)
    (out_dir / "index.html").write_text(html)
    return out_dir
