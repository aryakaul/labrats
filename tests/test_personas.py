"""Tests for personas.py — slugify, model resolution, message + kwargs."""

import yaml

from labrats import personas
from labrats.models import Paper, PersonaConfig
from labrats.personas import (
	FALLBACK_MODEL,
	_build_messages,
	_completion_kwargs,
	_slugify,
	resolve_model,
)


# ── _slugify ──


def test_slugify_lowercases_and_replaces_whitespace():
	assert _slugify("Excited Grad Student") == "excited_grad_student"


def test_slugify_strips_punctuation_and_collapses_runs():
	assert _slugify("  Reviewer #2!! ") == "reviewer_2"
	assert _slugify("foo---bar  baz") == "foo_bar_baz"


# ── resolve_model ──


def test_resolve_model_cli_flag_wins(tmp_path):
	(tmp_path / "settings.yaml").write_text(
		yaml.safe_dump({"default_model": "from-settings"})
	)
	assert resolve_model("from-cli", tmp_path) == "from-cli"


def test_resolve_model_falls_back_to_settings(tmp_path):
	(tmp_path / "settings.yaml").write_text(
		yaml.safe_dump({"default_model": "from-settings"})
	)
	assert resolve_model(None, tmp_path) == "from-settings"


def test_resolve_model_falls_back_to_constant_when_unset(tmp_path):
	# No settings.yaml present
	assert resolve_model(None, tmp_path) == FALLBACK_MODEL


# ── _build_messages ──


def test_build_messages_substitutes_role_and_lists_fields():
	persona = PersonaConfig(
		name="P", role="grizzled reviewer",
		scored_fields=["rigor", "novelty"],
	)
	paper = Paper(
		doi="d", title="T", authors=[], abstract="A",
		category="c", date="2026-01-01", url="u",
	)
	msgs = _build_messages(persona, paper, "You are a {role}.")
	assert msgs[0]["role"] == "system"
	assert msgs[0]["content"] == "You are a grizzled reviewer."
	user = msgs[1]["content"]
	assert "Title: T" in user
	assert "Abstract: A" in user
	assert "rigor" in user and "novelty" in user


# ── _completion_kwargs ──


def test_completion_kwargs_bare_model_with_explicit_api_base(monkeypatch):
	monkeypatch.setattr(personas, "resolve_local_model", lambda m: None)
	kwargs = _completion_kwargs(
		"gemma-3-4b", [{"role": "user", "content": "hi"}],
		api_base="http://localhost:1234/v1",
	)
	assert kwargs["model"] == "openai/gemma-3-4b"
	assert kwargs["api_base"] == "http://localhost:1234/v1"
	assert kwargs["api_key"] == "sk-local"


def test_completion_kwargs_qualified_model_unchanged(monkeypatch):
	monkeypatch.setattr(personas, "resolve_local_model", lambda m: None)
	kwargs = _completion_kwargs(
		"openai/gpt-4o-mini", [{"role": "user", "content": "hi"}],
		api_base=None,
	)
	assert kwargs["model"] == "openai/gpt-4o-mini"
	assert "api_base" not in kwargs
	assert "api_key" not in kwargs


def test_completion_kwargs_passes_extra_through(monkeypatch):
	monkeypatch.setattr(personas, "resolve_local_model", lambda m: None)
	kwargs = _completion_kwargs(
		"openai/gpt-4o-mini", [], api_base=None, temperature=0.7,
	)
	assert kwargs["temperature"] == 0.7
