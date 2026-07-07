"""SQLite storage for papers, persona results, and scrape history."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from labrats.models import Paper, PersonaResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    doi        TEXT NOT NULL,
    profile    TEXT NOT NULL,
    title      TEXT NOT NULL,
    abstract   TEXT NOT NULL,
    authors    TEXT NOT NULL,
    category   TEXT NOT NULL,
    date       TEXT NOT NULL,
    url        TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (doi, profile)
);
CREATE TABLE IF NOT EXISTS results (
    doi          TEXT NOT NULL,
    profile      TEXT NOT NULL,
    persona_name TEXT NOT NULL,
    scores       TEXT NOT NULL,
    summary      TEXT NOT NULL,
    model        TEXT NOT NULL DEFAULT '',
    run_at       TEXT NOT NULL,
    PRIMARY KEY (doi, profile, persona_name)
);
CREATE TABLE IF NOT EXISTS scrape_log (
    profile    TEXT NOT NULL,
    scraped_on TEXT NOT NULL,
    PRIMARY KEY (profile, scraped_on)
);
CREATE TABLE IF NOT EXISTS summaries (
    doi         TEXT NOT NULL PRIMARY KEY,
    llm_summary TEXT NOT NULL DEFAULT ''
);
"""


def open_db(db_path: Path) -> sqlite3.Connection:
    """Open (and auto-create) the database."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def already_scraped(conn: sqlite3.Connection, profile: str, today: str) -> bool:
    """Check if a profile has already been scraped today."""
    row = conn.execute(
        "SELECT 1 FROM scrape_log WHERE profile = ? AND scraped_on = ?",
        (profile, today),
    ).fetchone()
    return row is not None


def last_scraped(conn: sqlite3.Connection, profile: str) -> str | None:
    """Return the most recent scrape date for a profile, or None."""
    row = conn.execute(
        "SELECT scraped_on FROM scrape_log "
        "WHERE profile = ? ORDER BY scraped_on DESC LIMIT 1",
        (profile,),
    ).fetchone()
    return row[0] if row else None


def log_scrape(conn: sqlite3.Connection, profile: str, today: str) -> None:
    """Record that a profile was scraped on a given date."""
    conn.execute(
        "INSERT OR IGNORE INTO scrape_log (profile, scraped_on) VALUES (?, ?)",
        (profile, today),
    )
    conn.commit()


def upsert_papers(
    conn: sqlite3.Connection,
    papers: list[Paper],
    profile: str,
    today: str,
) -> None:
    """Insert or update papers for a profile."""
    conn.executemany(
        """
        INSERT OR REPLACE INTO papers
        (doi, profile, title, abstract, authors, category, date, url, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                p.doi, profile, p.title, p.abstract,
                json.dumps(p.authors), p.category, p.date, p.url, today,
            )
            for p in papers
        ],
    )
    conn.commit()


def enforce_cap(conn: sqlite3.Connection, profile: str, max_papers: int) -> None:
    """Delete oldest papers beyond the per-profile cap."""
    if not max_papers:
        return
    keep_q = (
        "SELECT doi FROM papers WHERE profile = ? "
        "ORDER BY date DESC, fetched_at DESC LIMIT ?"
    )
    args = (profile, profile, max_papers)
    conn.execute(
        f"DELETE FROM results WHERE profile = ? AND doi NOT IN ({keep_q})",
        args,
    )
    conn.execute(
        f"DELETE FROM papers WHERE profile = ? AND doi NOT IN ({keep_q})",
        args,
    )
    conn.commit()


def paper_count(conn: sqlite3.Connection, profile: str) -> int:
    """Number of papers stored for a profile."""
    return conn.execute(
        "SELECT COUNT(*) FROM papers WHERE profile = ?", (profile,),
    ).fetchone()[0]


def load_papers(conn: sqlite3.Connection, profile: str) -> list[Paper]:
    """Load all papers for a profile, newest first."""
    rows = conn.execute(
        "SELECT * FROM papers WHERE profile = ? ORDER BY date DESC",
        (profile,),
    ).fetchall()
    return [
        Paper(
            doi=r["doi"],
            title=r["title"],
            authors=json.loads(r["authors"]),
            abstract=r["abstract"],
            category=r["category"],
            date=r["date"],
            url=r["url"],
        )
        for r in rows
    ]


def papers_needing_eval(
    conn: sqlite3.Connection,
    papers: list[Paper],
    profile: str,
    persona_names: list[str],
) -> list[Paper]:
    """Return papers that haven't been evaluated by all personas yet."""
    if not persona_names or not papers:
        return []
    ph = ",".join("?" * len(persona_names))
    rows = conn.execute(
        f"SELECT doi FROM results "
        f"WHERE profile = ? AND persona_name IN ({ph}) "
        f"GROUP BY doi HAVING COUNT(DISTINCT persona_name) = ?",
        (profile, *persona_names, len(persona_names)),
    ).fetchall()
    done = {r["doi"] for r in rows}
    return [p for p in papers if p.doi not in done]


def delete_persona_result(
    conn: sqlite3.Connection, doi: str, profile: str, persona_name: str
) -> None:
    """Delete one persona's result for a paper so it can be re-evaluated."""
    conn.execute(
        "DELETE FROM results WHERE doi = ? AND profile = ? AND persona_name = ?",
        (doi, profile, persona_name),
    )
    conn.commit()


def upsert_results(
    conn: sqlite3.Connection,
    doi: str,
    profile: str,
    persona_results: list[PersonaResult],
) -> None:
    """Insert or update persona evaluation results."""
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        """
        INSERT OR REPLACE INTO results
        (doi, profile, persona_name, scores, summary, model, run_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                doi, profile, r.persona_name,
                json.dumps(r.scores), r.summary, r.model, now,
            )
            for r in persona_results
        ],
    )
    conn.commit()


def load_results(
    conn: sqlite3.Connection,
    doi: str,
    profile: str,
) -> dict[str, PersonaResult]:
    """Load all persona results for a paper, keyed by persona name."""
    rows = conn.execute(
        "SELECT * FROM results WHERE doi = ? AND profile = ?",
        (doi, profile),
    ).fetchall()
    return {
        r["persona_name"]: PersonaResult(
            persona_name=r["persona_name"],
            scores=json.loads(r["scores"]),
            summary=r["summary"],
            model=r["model"] or "",
        )
        for r in rows
    }


def load_llm_summary(conn: sqlite3.Connection, doi: str) -> str:
    """Return the cached LLM-structured summary for a DOI, or empty string."""
    row = conn.execute(
        "SELECT llm_summary FROM summaries WHERE doi = ?", (doi,),
    ).fetchone()
    return row[0] if row else ""


def upsert_llm_summary(
    conn: sqlite3.Connection,
    doi: str,
    summary: str,
) -> None:
    """Store an LLM-structured summary for a DOI."""
    conn.execute(
        "INSERT OR REPLACE INTO summaries (doi, llm_summary) VALUES (?, ?)",
        (doi, summary),
    )
    conn.commit()
