"""Tests for export.py — static site generation from config + DB."""

import json
import re

import pytest
import yaml

from labrats.db import open_db, upsert_papers, upsert_results
from labrats.export import export_site
from labrats.models import Paper, PersonaResult

PNG_BYTES = b"fake-png-override"


def _make_config(tmp_path, profile_name="Test"):
	cfg = tmp_path / "config"
	(cfg / "personas").mkdir(parents=True)
	(cfg / "settings.yaml").write_text(yaml.safe_dump({"api_keys": {}}))
	(cfg / "topics.yaml").write_text(
		yaml.safe_dump(
			{
				"profiles": [
					{
						"name": profile_name,
						"keywords": ["crispr"],
						"categories": ["genetics"],
					}
				]
			}
		)
	)
	(cfg / "personas" / "rat.yaml").write_text(
		yaml.safe_dump(
			{
				"name": "Rat",
				"role": "A rat.",
				"scored_fields": ["rigor"],
			}
		)
	)
	(cfg / "personas" / "rat.png").write_bytes(PNG_BYTES)
	return cfg


def _make_db(tmp_path, profile="Test", abstract="Abs", with_results=True):
	db_path = tmp_path / "labrats.db"
	conn = open_db(db_path)
	paper = Paper(
		doi="d1",
		title="T1",
		authors=["A. Author"],
		abstract=abstract,
		category="genetics",
		date="2026-07-01",
		url="https://example.org/d1",
	)
	upsert_papers(conn, [paper], profile, "2026-07-01")
	if with_results:
		upsert_results(
			conn,
			"d1",
			profile,
			[
				PersonaResult(
					persona_name="Rat",
					scores={"rigor": 7.0},
					summary="solid",
					model="m",
				)
			],
		)
	conn.close()
	return db_path


def _extract_blob(site):
	html = (site / "index.html").read_text()
	m = re.search(r"window\.__DIGEST__ = (.*?);</script>", html)
	assert m, "blob script not found"
	# json accepts the \/ escape natively
	return json.loads(m.group(1))


@pytest.fixture
def site(tmp_path):
	cfg = _make_config(tmp_path)
	db = _make_db(tmp_path)
	return export_site(cfg, db, tmp_path / "site")


def test_export_writes_site_files(site):
	assert (site / "index.html").is_file()
	assert (site / "app.js").is_file()
	assert (site / "app.css").is_file()
	html = (site / "index.html").read_text()
	assert "window.__DIGEST__" in html
	assert "/static/" not in html
	assert "/persona-image/" not in html


def test_export_blob_content(site):
	blob = _extract_blob(site)
	assert blob["profiles"][0]["name"] == "Test"
	assert blob["profiles"][0]["paper_count"] == 1
	cards = blob["cards"]["Test"]
	assert len(cards) == 1
	assert cards[0]["paper"]["title"] == "T1"
	assert cards[0]["results"][0]["image_url"] == "personas/rat.png"


def test_export_copies_persona_images(site):
	# user-config override wins over any bundled image
	assert (site / "personas" / "rat.png").read_bytes() == PNG_BYTES
	# bundled favicon image is always copied
	assert (site / "personas" / "excited_grad_student.png").is_file()


def test_export_profile_without_results(tmp_path):
	cfg = _make_config(tmp_path)
	db = _make_db(tmp_path, with_results=False)
	out = export_site(cfg, db, tmp_path / "site")
	blob = _extract_blob(out)
	assert blob["cards"]["Test"] == []


def test_export_escapes_script_breakout(tmp_path):
	cfg = _make_config(tmp_path)
	db = _make_db(tmp_path, abstract="bad </script><script>alert(1)")
	out = export_site(cfg, db, tmp_path / "site")
	html = (out / "index.html").read_text()
	blob_src = re.search(
		r"window\.__DIGEST__ = (.*?);</script>", html
	).group(1)
	assert "</script>" not in blob_src
	assert "<\\/script>" in blob_src
	# blob still parses to the original abstract
	blob = _extract_blob(out)
	abstract = blob["cards"]["Test"][0]["paper"]["abstract"]
	assert abstract == "bad </script><script>alert(1)"
