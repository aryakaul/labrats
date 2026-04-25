from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from labrats.models import PaperCard


def render_digest(
    sections: list[tuple[str, list[PaperCard]]],
    template_dir: Path,
    output_dir: Path,
) -> Path:
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=True,
    )
    template = env.get_template("digest.html.j2")
    html = template.render(sections=sections)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "digest.html"
    out_path.write_text(html)
    return out_path
