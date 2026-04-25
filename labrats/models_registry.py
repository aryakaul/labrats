"""Query available LLM models from litellm and local endpoints."""

import re

import requests

try:
	import litellm
except ImportError:
	litellm = None


PROVIDER_DOCS = {
	"openai": "https://platform.openai.com/docs/models",
	"anthropic": "https://docs.anthropic.com/en/docs/about-claude/models",
	"gemini": "https://ai.google.dev/gemini-api/docs/models",
	"groq": "https://console.groq.com/docs/models",
	"mistral": "https://docs.mistral.ai/getting-started/models/models_overview/",
	"cohere": "https://docs.cohere.com/docs/models",
	"together_ai": "https://docs.together.ai/docs/chat-models",
}

# patterns to exclude from model lists — these aren't
# useful for paper evaluation
_EXCLUDE = re.compile(
	r"(^ft:|audio|realtime|embed|rerank|tts|whisper"
	r"|dall-e|canvas|image|video|moderation"
	r"|search-preview|computer-use|-batch$)",
	re.IGNORECASE,
)

_PROVIDER_MAP = {
	"openai": "openai",
	"anthropic": "anthropic",
	"gemini": "gemini",
	"google": "gemini",
	"groq": "groq",
	"mistral": "mistral",
	"cohere": "cohere",
	"together_ai": "together_ai",
}


def list_cloud_models() -> dict[str, list[str]]:
	"""Return {provider: [model_name, ...]} from litellm."""
	if litellm is None:
		return {}

	result: dict[str, list[str]] = {}
	for name, info in litellm.model_cost.items():
		provider = info.get("litellm_provider", "")
		if provider not in _PROVIDER_MAP.values():
			continue
		if info.get("mode") != "chat":
			continue
		if info.get("deprecation_date"):
			continue
		if _EXCLUDE.search(name):
			continue
		# prefix with provider for litellm routing
		if not name.startswith(provider + "/"):
			display = f"{provider}/{name}"
		else:
			display = name
		result.setdefault(provider, []).append(display)

	for provider in result:
		result[provider].sort()
	return result


_KNOWN_LOCAL = [
	("Ollama", "http://localhost:11434/v1"),
	("LM Studio", "http://localhost:1234/v1"),
	("llama.cpp", "http://localhost:8080/v1"),
	("LlamaBarn", "http://localhost:2276/v1"),
]


def list_local_models(api_base: str) -> list[str]:
	"""Query an OpenAI-compatible endpoint for loaded models."""
	url = f"{api_base}/models"
	resp = requests.get(url, timeout=5)
	resp.raise_for_status()
	data = resp.json()
	return sorted(m["id"] for m in data.get("data", []))


def discover_local(
	extra_endpoints: list[str] | None = None,
) -> dict[str, list[str]]:
	"""Probe known local ports and return {label: [models]}.

	extra_endpoints are additional api_base URLs from settings.
	"""
	result: dict[str, list[str]] = {}
	seen_bases: set[str] = set()

	for label, base in _KNOWN_LOCAL:
		seen_bases.add(base)
		try:
			models = list_local_models(base)
			if models:
				result[f"{label} ({base})"] = models
		except Exception:
			pass

	for base in extra_endpoints or []:
		if base in seen_bases:
			continue
		seen_bases.add(base)
		try:
			models = list_local_models(base)
			if models:
				result[f"Local ({base})"] = models
		except Exception:
			pass

	return result
