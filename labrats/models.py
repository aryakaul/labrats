"""Core data structures shared across the entire pipeline."""

from dataclasses import dataclass, field


@dataclass
class Paper:
    """A preprint from biorxiv or arxiv."""
    doi: str
    title: str
    authors: list[str]
    author_corresponding: str
    author_corresponding_institution: str
    abstract: str
    category: str
    date: str          # YYYY-MM-DD
    version: str
    type: str
    url: str


@dataclass
class PersonaConfig:
    """An LLM persona that evaluates papers (loaded from YAML)."""
    name: str
    role: str
    prompt: str
    scored_fields: list[str]
    model: str | None = None   # per-persona model override
    enabled: bool = True


@dataclass
class PersonaResult:
    """One persona's evaluation of one paper."""
    persona_name: str
    scores: dict[str, float]   # field_name -> 1-10
    summary: str
    model: str = ""


@dataclass
class PaperCard:
    """A paper bundled with all persona evaluations and aggregate scores."""
    paper: Paper
    results: list[PersonaResult] = field(default_factory=list)
    tension: float = 0.0           # cross-persona disagreement
    interestingness: float = 0.0   # weighted blend of scores + tension
