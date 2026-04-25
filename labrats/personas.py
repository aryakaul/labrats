import json
import re
from pathlib import Path

import yaml
from litellm import acompletion

from labrats.models import Paper, PersonaConfig, PersonaResult


def _slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9\s_-]", "", s)
    s = re.sub(r"[\s-]+", "_", s)
    return s.strip("_")


def load_settings(config_dir: Path) -> dict:
	path = config_dir / "settings.yaml"
	if not path.exists():
		return {"api_keys": {}}
	with open(path) as f:
		data = yaml.safe_load(f) or {}
	return data


def save_settings(config_dir: Path, data: dict) -> None:
	path = config_dir / "settings.yaml"
	with open(path, "w") as f:
		yaml.safe_dump(
			data, f, default_flow_style=False, sort_keys=False,
		)
	path.chmod(0o600)


def load_profiles(config_dir: Path) -> list[dict]:
    path = config_dir / "topics.yaml"
    with open(path) as f:
        data = yaml.safe_load(f)
    # backward compat: flat format → single profile
    if "profiles" not in data:
        return [{"name": "Default", **data}]
    return data["profiles"]


def save_profiles(config_dir: Path, profiles: list[dict]) -> None:
    path = config_dir / "topics.yaml"
    with open(path, "w") as f:
        yaml.safe_dump(
            {"profiles": profiles},
            f,
            default_flow_style=False,
            sort_keys=False,
        )


def load_personas(
    config_dir: Path,
    names: list[str] | None = None,
) -> list[PersonaConfig]:
    persona_dir = config_dir / "personas"
    personas = []
    for path in sorted(persona_dir.glob("*.yaml")):
        if names is not None and path.stem not in names:
            continue
        with open(path) as f:
            data = yaml.safe_load(f)
        personas.append(
            PersonaConfig(
                name=data["name"],
                role=data["role"],
                prompt=data["prompt"],
                scored_fields=data["scored_fields"],
                model=data.get("model"),
                enabled=data.get("enabled", True),
            )
        )
    return personas


def save_persona(
    config_dir: Path,
    persona: dict,
    stem: str | None = None,
) -> str:
    """Write persona dict to YAML. Returns the stem."""
    persona_dir = config_dir / "personas"
    persona_dir.mkdir(parents=True, exist_ok=True)
    new_stem = _slugify(persona["name"])
    # rename file if stem changed
    if stem and stem != new_stem:
        old = persona_dir / f"{stem}.yaml"
        if old.exists():
            old.unlink()
    path = persona_dir / f"{new_stem}.yaml"
    data = {
        "name": persona["name"],
        "role": persona["role"],
        "prompt": persona["prompt"],
        "scored_fields": persona["scored_fields"],
    }
    if persona.get("model"):
        data["model"] = persona["model"]
    if not persona.get("enabled", True):
        data["enabled"] = False
    with open(path, "w") as f:
        yaml.safe_dump(
            data,
            f,
            default_flow_style=False,
            sort_keys=False,
        )
    return new_stem


def delete_persona(config_dir: Path, stem: str) -> None:
    path = config_dir / "personas" / f"{stem}.yaml"
    if path.exists():
        path.unlink()


def _build_messages(
    persona: PersonaConfig,
    paper: Paper,
) -> list[dict]:
    system = persona.prompt.format(role=persona.role)
    fields_spec = ", ".join(persona.scored_fields)
    user_msg = (
        f"Title: {paper.title}\n\n"
        f"Abstract: {paper.abstract}\n\n"
        f"Respond with JSON only. Keys:\n"
        f'  "scores": {{{fields_spec}}} '
        f"(each 1-10)\n"
        f'  "summary": string '
        f"(1-2 sentences, direct and opinionated)"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]


async def run_persona(
    persona: PersonaConfig,
    paper: Paper,
    model: str,
    api_base: str | None = None,
) -> PersonaResult:
    effective_model = persona.model or model
    messages = _build_messages(persona, paper)
    kwargs: dict = {
        "model": effective_model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "temperature": 0.3,
    }
    if api_base is not None:
        kwargs["api_base"] = api_base
        kwargs["api_key"] = "sk-local"
    response = await acompletion(**kwargs)
    content = (response.choices[0].message.content or "").strip()
    # strip markdown code fences emitted by some local models
    if content.startswith("```"):
        lines = content.splitlines()
        content = "\n".join(lines[1:-1]).strip()
    raw = json.loads(content)
    return PersonaResult(
        persona_name=persona.name,
        scores={k: float(raw["scores"][k]) for k in persona.scored_fields},
        summary=raw["summary"],
        model=effective_model,
    )
