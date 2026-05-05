"""Load, save, and run LLM personas that evaluate papers."""

import json
import re
from pathlib import Path

import yaml
from litellm import acompletion

from labrats.models import Paper, PersonaConfig, PersonaResult
from labrats.models_registry import PROVIDER_KEYS, resolve_local_model

FALLBACK_MODEL = "openai/gpt-4o-mini"

DEFAULT_PERSONA_PROMPT = (
    "You are a {role}. Read the following paper and evaluate it.\n"
    "Score each field using the full 1-10 range. Anchors: "
    "1-2 = serious flaws or fundamental problems; "
    "3-4 = below average for a preprint in this field; "
    "5 = average, does what it claims; "
    "6-7 = above average, worth attention; "
    "8-9 = excellent, top 10-15% of papers you have seen; "
    "10 = exceptional, field-defining work.\n"
    "Give a short, direct take (1-2 sentences)."
)


def _slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9\s_-]", "", s)
    s = re.sub(r"[\s-]+", "_", s)
    return s.strip("_")


# ── settings (settings.yaml) ──


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
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
    path.chmod(0o600)  # keys are sensitive


def resolve_model(cli_model: str | None, config_dir: Path) -> str:
    """CLI flag > settings.yaml default_model > FALLBACK_MODEL."""
    if cli_model:
        return cli_model
    return load_settings(config_dir).get("default_model") or FALLBACK_MODEL


# ── profiles (topics.yaml) ──


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


# ── personas (personas/*.yaml) ──


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
                scored_fields=data["scored_fields"],
                model=data.get("model"),
                enabled=data.get("enabled", True),
                stem=path.stem,
            )
        )
    return personas


def save_persona(
    config_dir: Path,
    persona: dict,
    stem: str | None = None,
) -> str:
    """Write persona dict to YAML. Returns the file stem."""
    persona_dir = config_dir / "personas"
    persona_dir.mkdir(parents=True, exist_ok=True)
    new_stem = _slugify(persona["name"])
    # rename file if name changed
    if stem and stem != new_stem:
        old = persona_dir / f"{stem}.yaml"
        if old.exists():
            old.unlink()
    path = persona_dir / f"{new_stem}.yaml"
    data = {
        "name": persona["name"],
        "role": persona["role"],
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


# ── LLM evaluation ──


def _build_messages(
    persona: PersonaConfig,
    paper: Paper,
    persona_prompt: str,
    purpose: str = "",
) -> list[dict]:
    system = persona_prompt.format(role=persona.role)
    if purpose:
        system += (
            f"\n\nThe researcher tracking this profile is interested in: "
            f"{purpose}\n"
            f"Factor this into your relevance score in particular."
        )
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


_SUMMARY_SYSTEM = (
    "You are a scientific abstract summarizer. "
    "Structure the abstract into exactly four labeled sections: "
    "Background, Methods, Results, Discussion. "
    "Each section is 1-2 sentences. "
    "If a section cannot be inferred from the abstract, write 'Not stated.' "
    "Respond only with the four labeled lines, nothing else."
)


def _completion_kwargs(
    model: str,
    messages: list[dict],
    api_base: str | None,
    **extra,
) -> dict:
    """Build litellm kwargs, auto-routing non-cloud model names to local APIs."""
    kwargs = {"model": model, "messages": messages, **extra}
    provider = model.split("/", 1)[0] if "/" in model else ""
    is_cloud = provider in PROVIDER_KEYS
    # No explicit api_base + not a known cloud provider → probe local
    if api_base is None and not is_cloud:
        api_base = resolve_local_model(model)
    if api_base is not None:
        # litellm needs an "openai/" prefix to route to an OpenAI-compatible
        # local endpoint; the rest is sent verbatim as the model id.
        if not model.startswith("openai/"):
            kwargs["model"] = f"openai/{model}"
        kwargs["api_base"] = api_base
        kwargs["api_key"] = "sk-local"
    return kwargs


async def summarize_abstract(
    paper: Paper,
    model: str,
    api_base: str | None = None,
) -> str:
    """Return a Background/Methods/Results/Discussion summary."""
    user_msg = (
        f"Abstract:\n{paper.abstract}\n\n"
        "Background: ...\n"
        "Methods: ...\n"
        "Results: ...\n"
        "Discussion: ..."
    )
    messages = [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    kwargs = _completion_kwargs(
        model, messages, api_base,
        temperature=0.2, max_tokens=1024,
    )
    response = await acompletion(**kwargs)
    return (response.choices[0].message.content or "").strip()


async def run_persona(
    persona: PersonaConfig,
    paper: Paper,
    persona_prompt: str,
    model: str,
    api_base: str | None = None,
    purpose: str = "",
) -> PersonaResult:
    """Call the LLM as this persona and parse the scored JSON response."""
    effective_model = persona.model or model
    messages = _build_messages(persona, paper, persona_prompt, purpose)
    kwargs = _completion_kwargs(
        effective_model, messages, api_base,
        response_format={"type": "json_object"},
        temperature=0.3,
        max_tokens=1024,
    )
    response = await acompletion(**kwargs)
    content = (response.choices[0].message.content or "").strip()

    # Strip markdown code fences emitted by some local models
    if content.startswith("```"):
        content = "\n".join(content.splitlines()[1:-1]).strip()

    raw = json.loads(content, strict=False)
    return PersonaResult(
        persona_name=persona.name,
        scores={k: float(raw["scores"][k]) for k in persona.scored_fields},
        summary=raw["summary"],
        model=effective_model,
    )
