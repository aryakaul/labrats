import json
from pathlib import Path

import yaml
from litellm import acompletion

from labrats.models import Paper, PersonaConfig, PersonaResult


def load_topics(config_dir: Path) -> dict:
	path = config_dir / "topics.yaml"
	with open(path) as f:
		return yaml.safe_load(f)


def load_personas(config_dir: Path) -> list[PersonaConfig]:
	persona_dir = config_dir / "personas"
	personas = []
	for path in sorted(persona_dir.glob("*.yaml")):
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
		f"  \"summary\": string (2-3 sentences)"
	)
	return [
		{"role": "system", "content": system},
		{"role": "user", "content": user_msg},
	]


async def run_persona(
	persona: PersonaConfig,
	paper: Paper,
	model: str,
) -> PersonaResult:
	effective_model = persona.model or model
	messages = _build_messages(persona, paper)
	response = await acompletion(
		model=effective_model,
		messages=messages,
		response_format={"type": "json_object"},
		temperature=0.3,
	)
	raw = json.loads(
		response.choices[0].message.content
	)
	return PersonaResult(
		persona_name=persona.name,
		scores={
			k: float(raw["scores"][k])
			for k in persona.scored_fields
		},
		summary=raw["summary"],
	)
