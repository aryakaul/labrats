"""Discover and list available LLM models (cloud via litellm, local via API)."""

import re
from concurrent.futures import ThreadPoolExecutor

import requests

try:
    import litellm
except ImportError:
    litellm = None

# Canonical, ordered provider registry — the single source of truth for
# provider key, display label, API-key env var, and docs link. The
# frontend consumes this via /api/models rather than re-declaring it.
PROVIDERS = [
    {"key": "openai", "label": "OpenAI", "env": "OPENAI_API_KEY",
     "docs": "https://platform.openai.com/docs/models"},
    {"key": "anthropic", "label": "Anthropic", "env": "ANTHROPIC_API_KEY",
     "docs": "https://docs.anthropic.com/en/docs/about-claude/models"},
    {"key": "gemini", "label": "Google / Gemini", "env": "GOOGLE_API_KEY",
     "docs": "https://ai.google.dev/gemini-api/docs/models"},
    {"key": "groq", "label": "Groq", "env": "GROQ_API_KEY",
     "docs": "https://console.groq.com/docs/models"},
    {"key": "mistral", "label": "Mistral", "env": "MISTRAL_API_KEY",
     "docs": "https://docs.mistral.ai/getting-started/models/models_overview/"},
    {"key": "cohere", "label": "Cohere", "env": "COHERE_API_KEY",
     "docs": "https://docs.cohere.com/docs/models"},
    {"key": "together_ai", "label": "Together AI",
     "env": "TOGETHERAI_API_KEY",
     "docs": "https://docs.together.ai/docs/chat-models"},
]

# Derived lookups (keep the old names/shapes callers already use).
PROVIDER_KEYS = {p["key"]: p["env"] for p in PROVIDERS}
PROVIDER_DOCS = {p["key"]: p["docs"] for p in PROVIDERS}

# Filter out non-chat models (embeddings, TTS, image gen, etc.)
_EXCLUDE = re.compile(
    r"(^ft:|audio|realtime|embed|rerank|tts|whisper"
    r"|dall-e|canvas|image|video|moderation"
    r"|search-preview|computer-use|-batch$)",
    re.IGNORECASE,
)

# Canonical provider names we care about
_PROVIDERS = {p["key"] for p in PROVIDERS}

# litellm's registry is static per process — build the list once.
_cloud_cache: dict[str, list[str]] | None = None


def list_cloud_models() -> dict[str, list[str]]:
    """Return {provider: [model_name, ...]} from litellm's model registry."""
    global _cloud_cache
    if _cloud_cache is not None:
        return _cloud_cache
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
    _cloud_cache = result
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


def resolve_local_model(model: str) -> str | None:
    """Given a bare model name, find which local endpoint serves it.

    Returns the api_base URL, or None if not found locally.
    """
    if model in _local_model_cache:
        return _local_model_cache[model]

    all_bases = [base for _, base in _KNOWN_LOCAL]
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
    targets: list[tuple[str, str]] = []
    seen_bases: set[str] = set()
    for label, base in _KNOWN_LOCAL:
        seen_bases.add(base)
        targets.append((f"{label} ({base})", base))
    for base in extra_endpoints or []:
        if base in seen_bases:
            continue
        seen_bases.add(base)
        targets.append((f"Local ({base})", base))

    def _probe(target):
        label, base = target
        try:
            models = list_local_models(base)
            return (label, models) if models else None
        except Exception:
            return None

    # Probe endpoints concurrently — each blocks up to the request
    # timeout, so serial probing stalls the single-threaded server.
    result: dict[str, list[str]] = {}
    with ThreadPoolExecutor(max_workers=max(len(targets), 1)) as pool:
        for hit in pool.map(_probe, targets):
            if hit:
                result[hit[0]] = hit[1]
    return result
