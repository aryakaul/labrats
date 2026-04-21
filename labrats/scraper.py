import requests

from labrats.models import Paper


BIORXIV_API = "https://api.biorxiv.org/details/biorxiv"


def fetch_papers(
	start_date: str,
	end_date: str,
) -> list[Paper]:
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
		author_corresponding=entry.get(
			"author_corresponding", ""
		),
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


def filter_by_topics(
	papers: list[Paper],
	topics: dict,
) -> list[Paper]:
	keywords = [
		kw.lower() for kw in topics.get("keywords", [])
	]
	categories = [
		c.lower() for c in topics.get("categories", [])
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
