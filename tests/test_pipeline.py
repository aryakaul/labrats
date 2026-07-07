"""Tests for run_pipeline — bounded concurrency + callback contract."""

import asyncio

from labrats import pipeline
from labrats.models import Paper, PaperCard


def _paper(doi):
    return Paper(
        doi=doi, title=f"t{doi}", authors=[], abstract="",
        category="", date="", url="",
    )


def test_all_papers_evaluated_progress_monotonic(monkeypatch):
    papers = [_paper(str(i)) for i in range(6)]

    async def fake_eval(paper, *a, **k):
        await asyncio.sleep(0)
        return PaperCard(paper=paper)

    monkeypatch.setattr(pipeline, "evaluate_paper", fake_eval)

    progress, carded = [], []
    cards = asyncio.run(
        pipeline.run_pipeline(
            papers, [], "", "m",
            on_progress=lambda d, t: progress.append((d, t)),
            on_card=lambda c: carded.append(c.paper.doi),
            concurrency=3,
        )
    )
    assert len(cards) == 6
    assert len(carded) == 6
    # monotonic 1..6, total always 6
    assert [d for d, _ in progress] == [1, 2, 3, 4, 5, 6]
    assert all(t == 6 for _, t in progress)


def test_failures_skipped(monkeypatch):
    papers = [_paper(str(i)) for i in range(4)]

    async def fake_eval(paper, *a, **k):
        await asyncio.sleep(0)
        if paper.doi == "2":
            raise RuntimeError("boom")
        return PaperCard(paper=paper)

    monkeypatch.setattr(pipeline, "evaluate_paper", fake_eval)

    carded = []
    cards = asyncio.run(
        pipeline.run_pipeline(
            papers, [], "", "m",
            on_card=lambda c: carded.append(c.paper.doi),
            concurrency=2,
        )
    )
    assert len(cards) == 3
    assert "2" not in carded
    # progress still reaches total even with a failure
    # (implicitly: no exception propagated out of gather)


def test_concurrency_bound_respected(monkeypatch):
    papers = [_paper(str(i)) for i in range(10)]
    active = 0
    peak = 0

    async def fake_eval(paper, *a, **k):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return PaperCard(paper=paper)

    monkeypatch.setattr(pipeline, "evaluate_paper", fake_eval)

    asyncio.run(
        pipeline.run_pipeline(papers, [], "", "m", concurrency=3)
    )
    assert peak <= 3
