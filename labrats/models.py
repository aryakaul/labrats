from dataclasses import dataclass, field


@dataclass
class Paper:
	doi: str
	title: str
	authors: list[str]
	author_corresponding: str
	author_corresponding_institution: str
	abstract: str
	category: str
	date: str
	version: str
	type: str
	url: str


@dataclass
class PersonaConfig:
	name: str
	role: str
	prompt: str
	scored_fields: list[str]
	model: str | None = None


@dataclass
class PersonaResult:
	persona_name: str
	scores: dict[str, float]
	summary: str


@dataclass
class PaperCard:
	paper: Paper
	results: list[PersonaResult] = field(default_factory=list)
	tension: float = 0.0
	interestingness: float = 0.0
