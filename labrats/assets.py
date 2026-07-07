"""Package asset locations and persona image resolution.

Transport-agnostic: both the web server and the static exporter depend
on these paths and helpers, so they live here rather than in serve.py.
"""

from pathlib import Path

from labrats.personas import _slugify

PACKAGE_DIR = Path(__file__).resolve().parent
INDEX_HTML = PACKAGE_DIR / "templates" / "serve.html"
STATIC_DIR = PACKAGE_DIR / "static"


def resolve_persona_image(slug: str, config_dir: Path) -> Path | None:
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


def persona_image_url(persona_name: str, config_dir: Path) -> str:
    """Return the URL for a persona image, or '' if none exists."""
    slug = _slugify(persona_name)
    if resolve_persona_image(slug, config_dir):
        return f"/persona-image/{slug}.png"
    return ""
