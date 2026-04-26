"""Discover and list available LLM models (cloud via litellm, local via API)."""

import re

import requests

try:
    import litellm
except ImportError:
    litellm = None

# Maps provider name -> env var name for API key
PROVIDER_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "google": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "together_ai": "TOGETHERAI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "cohere": "COHERE_API_KEY",
}

# Links to each provider's model documentation
PROVIDER_DOCS = {
    "openai": "https://platform.openai.com/docs/models",
    "anthropic": "https://docs.anthropic.com/en/docs/about-claude/models",
    "gemini": "https://ai.google.dev/gemini-api/docs/models",
    "groq": "https://console.groq.com/docs/models",
    "mistral": "https://docs.mistral.ai/getting-started/models/models_overview/",
    "cohere": "https://docs.cohere.com/docs/models",
    "together_ai": "https://docs.together.ai/docs/chat-models",
}

# Filter out non-chat models (embeddings, TTS, image gen, etc.)
_EXCLUDE = re.compile(
    r"(^ft:|audio|realtime|embed|rerank|tts|whisper"
    r"|dall-e|canvas|image|video|moderation"
    r"|search-preview|computer-use|-batch$)",
    re.IGNORECASE,
)

# Canonical provider names we care about
_PROVIDERS = {
    "openai", "anthropic", "gemini", "groq",
    "mistral", "cohere", "together_ai",
}


def list_cloud_models() -> dict[str, list[str]]:
    """Return {provider: [model_name, ...]} from litellm's model registry."""
    if litellm is None:
        return {}

    result: dict[str, list[str]] = {}
    for name, info in litellm.model_cost.items():
        provider = info.get("litellm_provider", "")
        if provider not in _PROVIDERS:
            continue
        if info.get("mode") != "chat":
            continue
        if info.get("deprecation_date"):
            continue
        if _EXCLUDE.search(name):
            continue
        # litellm routing requires "provider/model" format
        if not name.startswith(provider + "/"):
            display = f"{provider}/{name}"
        else:
            display = name
        result.setdefault(provider, []).append(display)

    for provider in result:
        result[provider].sort()
    return result


# Well-known local model servers and their default OpenAI-compatible endpoints
_KNOWN_LOCAL = [
    ("Ollama", "http://localhost:11434/v1"),
    ("LM Studio", "http://localhost:1234/v1"),
    ("llama.cpp", "http://localhost:8080/v1"),
    ("LlamaBarn", "http://localhost:2276/v1"),
]


def list_local_models(api_base: str) -> list[str]:
    """Query an OpenAI-compatible /v1/models endpoint."""
    url = f"{api_base}/models"
    resp = requests.get(url, timeout=5)
    resp.raise_for_status()
    data = resp.json()
    return sorted(m["id"] for m in data.get("data", []))


# Cache resolve_local_model results to avoid repeated HTTP probes
_local_model_cache: dict[str, str | None] = {}


def resolve_local_model(
    model: str,
    extra_endpoints: list[str] | None = None,
) -> str | None:
    """Given a bare model name, find which local endpoint serves it.

    Returns the api_base URL, or None if not found locally.
    """
    if model in _local_model_cache:
        return _local_model_cache[model]

    all_bases = [base for _, base in _KNOWN_LOCAL]
    for base in extra_endpoints or []:
        if base not in all_bases:
            all_bases.append(base)

    for base in all_bases:
        try:
            models = list_local_models(base)
            if model in models:
                _local_model_cache[model] = base
                return base
        except Exception:
            pass
    _local_model_cache[model] = None
    return None


def discover_local(
    extra_endpoints: list[str] | None = None,
) -> dict[str, list[str]]:
    """Probe known local ports and return {label: [model_ids]}.

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
