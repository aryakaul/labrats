"""Core data structures shared across the entire pipeline."""

from dataclasses import dataclass, field


@dataclass
class Paper:
    """A preprint from biorxiv or arxiv."""
    doi: str
    title: str
    authors: list[str]
    abstract: str
    category: str
    date: str  # YYYY-MM-DD
    url: str


@dataclass
class PersonaConfig:
    """An LLM persona that evaluates papers (loaded from YAML)."""
    name: str
    role: str
    scored_fields: list[str]
    model: str | None = None
    enabled: bool = True
    stem: str = ""  # filename stem (e.g. "critical_methodologist")


@dataclass
class PersonaResult:
    """One persona's evaluation of one paper."""
    persona_name: str
    scores: dict[str, float]  # field_name -> 1-10
    summary: str
    model: str = ""


@dataclass
class PaperCard:
    """A paper bundled with all persona evaluations and aggregate scores."""
    paper: Paper
    results: list[PersonaResult] = field(default_factory=list)
    tension: float = 0.0
    avg_score: float = 0.0
    disputed: bool = False
    disputed_field: str = ""
    llm_summary: str = ""
