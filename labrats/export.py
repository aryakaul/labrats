"""Render the digest into a static site directory (no server)."""

import json
import re
import shutil
from pathlib import Path

from labrats.db import open_db
from labrats.personas import load_profiles
from labrats.serve import (
    INDEX_HTML,
    STATIC_DIR,
    _card_to_dict,
    _resolve_persona_image,
    build_digest_profiles,
    build_profile_cards,
)

_IMG_PREFIX = "/persona-image/"
_DATA_MARKER = "<!-- __DIGEST_DATA__ -->"
_STATIC_REF = re.compile(r'/static/([^"\'#?\s]+)')


def export_site(config_dir: Path, db_path: Path, out_dir: Path) -> Path:
    """Write a self-contained static digest site to out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "personas").mkdir(exist_ok=True)

    html = INDEX_HTML.read_text()
    if _DATA_MARKER not in html:
        raise RuntimeError(f"{INDEX_HTML} is missing {_DATA_MARKER}")

    conn = open_db(db_path)
    digest_profiles = build_digest_profiles(conn, config_dir)

    slugs = set()
    cards_by_profile = {}
    for profile in load_profiles(config_dir):
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

    # Copy every /static/ asset the shell references, preserving paths.
    for rel in set(_STATIC_REF.findall(html)):
        src = STATIC_DIR / rel
        if src.is_file():
            dst = out_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    # Copy persona images referenced by the card data.
    for slug in slugs:
        src = _resolve_persona_image(slug, config_dir)
        if src:
            shutil.copy2(src, out_dir / "personas" / f"{slug}.png")

    payload = json.dumps(
        {"profiles": digest_profiles, "cards": cards_by_profile}
    ).replace("</", "<\\/")
    blob = f"<script>window.__DIGEST__ = {payload};</script>"

    html = html.replace("/static/", "").replace(_DATA_MARKER, blob, 1)
    (out_dir / "index.html").write_text(html)
    return out_dir
