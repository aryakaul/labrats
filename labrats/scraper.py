import xml.etree.ElementTree as ET

import requests

from labrats.models import Paper


BIORXIV_API = "https://api.biorxiv.org/details/biorxiv"
ARXIV_API = "https://export.arxiv.org/api/query"
_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"


def fetch_papers(
    start_date: str,
    end_date: str,
) -> list[Paper]:
    papers = []
    cursor = 0
    while True:
        url = f"{BIORXIV_API}" f"/{start_date}/{end_date}/{cursor}"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("messages", [{}])[0].get("status", "")
        if status not in ("ok", ""):
            raise RuntimeError(f"biorxiv API: {status}")
        collection = data.get("collection", [])
        if not collection:
            break
        for entry in collection:
            papers.append(_parse_paper(entry))
        cursor += len(collection)
    return papers


def _parse_paper(entry: dict) -> Paper:
    doi = entry["doi"]
    return Paper(
        doi=doi,
        title=entry["title"],
        authors=_parse_authors(entry["authors"]),
        author_corresponding=entry.get("author_corresponding", ""),
        author_corresponding_institution=entry.get(
            "author_corresponding_institution", ""
        ),
        abstract=entry["abstract"],
        category=entry["category"],
        date=entry["date"],
        version=entry["version"],
        type=entry["type"],
        url=f"https://www.biorxiv.org/content/{doi}",
    )


def _parse_authors(raw: str) -> list[str]:
    return [a.strip() for a in raw.split(";") if a.strip()]


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
    # "http://arxiv.org/abs/2301.00001v2"
    arxiv_id = raw_id.split("/abs/")[-1]
    parts = arxiv_id.rsplit("v", 1)
    canonical = parts[0]
    version = parts[1] if len(parts) == 2 else "1"

    doi_el = entry.find(f"{_ARXIV}doi")
    doi = doi_el.text.strip() if doi_el is not None else f"arxiv:{canonical}"

    title = (entry.findtext(f"{_ATOM}title") or "").strip()
    abstract = (entry.findtext(f"{_ATOM}summary") or "").strip()

    authors = [
        n.text.strip()
        for a in entry.findall(f"{_ATOM}author")
        for n in [a.find(f"{_ATOM}name")]
        if n is not None and n.text
    ]

    published = entry.findtext(f"{_ATOM}published", "")
    date = published[:10]

    # prefer primary_category from arxiv namespace
    pcat = entry.find(f"{_ARXIV}primary_category")
    if pcat is not None:
        category = pcat.get("term", "")
    else:
        cat_el = entry.find(f"{_ATOM}category")
        category = cat_el.get("term", "") if cat_el is not None else ""

    return Paper(
        doi=doi,
        title=title,
        authors=authors,
        author_corresponding=(authors[0] if authors else ""),
        author_corresponding_institution="",
        abstract=abstract,
        category=category,
        date=date,
        version=version,
        type="",
        url=f"https://arxiv.org/abs/{canonical}",
    )


def filter_by_topics(
    papers: list[Paper],
    topics: dict,
) -> list[Paper]:
    keywords = [kw.lower() for kw in topics.get("keywords", [])]
    # merge biorxiv and arxiv category lists so
    # post-filter works for both sources
    categories = [
        c.lower()
        for c in (
            topics.get("categories", []) + topics.get("arxiv_categories", [])
        )
    ]
    results = []
    for paper in papers:
        if _matches_topics(paper, keywords, categories):
            results.append(paper)
    return results


def _matches_topics(
    paper: Paper,
    keywords: list[str],
    categories: list[str],
) -> bool:
    if categories and paper.category.lower() in categories:
        return True
    text = f"{paper.title} {paper.abstract}".lower()
    return any(kw in text for kw in keywords)
