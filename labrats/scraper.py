"""Fetch preprints from biorxiv and arxiv, with topic filtering."""

import xml.etree.ElementTree as ET

import requests

from labrats.models import Paper

BIORXIV_API = "https://api.biorxiv.org/details/biorxiv"
ARXIV_API = "https://export.arxiv.org/api/query"
_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"


def fetch_papers(start_date: str, end_date: str) -> list[Paper]:
    papers = []
    cursor = 0
    while True:
        url = f"{BIORXIV_API}/{start_date}/{end_date}/{cursor}"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        collection = data.get("collection", [])
        if not collection:
            break
        status = data.get("messages", [{}])[0].get("status", "")
        if status not in ("ok", ""):
            raise RuntimeError(f"biorxiv API: {status}")
        papers.extend(_parse_paper(e) for e in collection)
        cursor += len(collection)
    return papers


def _parse_paper(entry: dict) -> Paper:
    doi = entry["doi"]
    authors = [a.strip() for a in entry["authors"].split(";") if a.strip()]
    return Paper(
        doi=doi,
        title=entry["title"],
        authors=authors,
        abstract=entry["abstract"],
        category=entry["category"],
        date=entry["date"],
        url=f"https://www.biorxiv.org/content/{doi}",
    )


def fetch_papers_arxiv(
    start_date: str,
    end_date: str,
    arxiv_categories: list[str],
) -> list[Paper]:
    # arxiv API uses + as word separator and expects
    # literal brackets — build the query string manually
    # to avoid requests percent-encoding + as %2B.
    d_start = start_date.replace("-", "") + "0000"
    d_end = end_date.replace("-", "") + "2359"
    date_q = f"submittedDate:[{d_start}+TO+{d_end}]"

    if arxiv_categories:
        cat_q = "+OR+".join(f"cat:{c}" for c in arxiv_categories)
        query = f"({cat_q})+AND+{date_q}"
    else:
        query = date_q

    papers = []
    cursor = 0
    page = 100
    while True:
        url = (
            f"{ARXIV_API}?search_query={query}"
            f"&start={cursor}"
            f"&max_results={page}"
            f"&sortBy=submittedDate"
            f"&sortOrder=descending"
        )
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        entries = root.findall(f"{_ATOM}entry")
        if not entries:
            break
        for entry in entries:
            papers.append(_parse_arxiv_entry(entry))
        cursor += len(entries)
        if len(entries) < page:
            break
    return papers


def _parse_arxiv_entry(entry) -> Paper:
    raw_id = entry.findtext(f"{_ATOM}id", "")
    # "http://arxiv.org/abs/2301.00001v2" -> "2301.00001"
    canonical = raw_id.split("/abs/")[-1].rsplit("v", 1)[0]

    doi_el = entry.find(f"{_ARXIV}doi")
    doi = doi_el.text.strip() if doi_el is not None else f"arxiv:{canonical}"

    authors = [
        n.text.strip()
        for a in entry.findall(f"{_ATOM}author")
        for n in [a.find(f"{_ATOM}name")]
        if n is not None and n.text
    ]

    # prefer primary_category from arxiv namespace
    pcat = entry.find(f"{_ARXIV}primary_category")
    cat_el = pcat if pcat is not None else entry.find(f"{_ATOM}category")
    category = cat_el.get("term", "") if cat_el is not None else ""

    return Paper(
        doi=doi,
        title=(entry.findtext(f"{_ATOM}title") or "").strip(),
        authors=authors,
        abstract=(entry.findtext(f"{_ATOM}summary") or "").strip(),
        category=category,
        date=entry.findtext(f"{_ATOM}published", "")[:10],
        url=f"https://arxiv.org/abs/{canonical}",
    )


def filter_by_topics(papers: list[Paper], topics: dict) -> list[Paper]:
    """Keep papers matching both keyword AND category (when each is set)."""
    keywords = [kw.lower() for kw in topics.get("keywords", [])]
    categories = [
        c.lower() for c in (
            topics.get("categories", [])
            + topics.get("arxiv_categories", [])
        )
    ]

    def matches(p: Paper) -> bool:
        in_cat = not categories or p.category.lower() in categories
        text = f"{p.title} {p.abstract}".lower()
        has_kw = not keywords or any(kw in text for kw in keywords)
        return in_cat and has_kw

    return [p for p in papers if matches(p)]


# ── helpers used by both cli.py and serve.py ──


def dedup_papers(papers: list[Paper]) -> list[Paper]:
    """Remove duplicate papers by DOI."""
    seen: set[str] = set()
    result = []
    for p in papers:
        if p.doi not in seen:
            seen.add(p.doi)
            result.append(p)
    return result


def union_arxiv_cats(profiles: list[dict]) -> list[str]:
    """Collect unique arxiv categories across all profiles."""
    return list({c for p in profiles for c in p.get("arxiv_categories", [])})


def fetch_from_sources(
    start: str,
    end: str,
    source: str,
    topics: dict,
) -> tuple[list[Paper], list[str]]:
    """Fetch from biorxiv and/or arxiv, dedup, return (papers, errors).

    Raises RuntimeError only if all requested sources fail; partial
    success returns whatever was fetched plus a list of error messages.
    """
    papers: list[Paper] = []
    errors: list[str] = []

    if source in ("biorxiv", "all"):
        try:
            papers += fetch_papers(start, end)
        except Exception as e:
            errors.append(f"biorxiv: {e}")

    if source in ("arxiv", "all"):
        try:
            cats = topics.get("arxiv_categories", [])
            papers += fetch_papers_arxiv(start, end, cats)
        except Exception as e:
            errors.append(f"arxiv: {e}")

    if not papers and errors:
        raise RuntimeError("All sources failed: " + "; ".join(errors))

    return dedup_papers(papers), errors
