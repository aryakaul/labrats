import json
from pathlib import Path

import yaml
from litellm import acompletion

from labrats.models import Paper, PersonaConfig, PersonaResult


def load_profiles(config_dir: Path) -> list[dict]:
	path = config_dir / "topics.yaml"
	with open(path) as f:
		data = yaml.safe_load(f)
	# backward compat: flat format → single profile
	if "profiles" not in data:
		return [{"name": "Default", **data}]
	return data["profiles"]


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
		personas.append(PersonaConfig(
			name=data["name"],
			role=data["role"],
			prompt=data["prompt"],
			scored_fields=data["scored_fields"],
			model=data.get("model"),
		))
	return personas


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
		f"  \"scores\": {{{fields_spec}}} "
		f"(each 1-10)\n"
		f"  \"summary\": string "
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
	content = (
		response.choices[0].message.content or ""
	).strip()
	# strip markdown code fences emitted by some local models
	if content.startswith("```"):
		lines = content.splitlines()
		content = "\n".join(lines[1:-1]).strip()
	raw = json.loads(content)
	return PersonaResult(
		persona_name=persona.name,
		scores={
			k: float(raw["scores"][k])
			for k in persona.scored_fields
		},
		summary=raw["summary"],
		model=effective_model,
	)
